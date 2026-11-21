"""Stream tiles, detect tanks, write sidecars and Blender FBX."""

from __future__ import annotations

import json
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from tankextract.detect import DetectConfig, SceneAccumulator, refine_candidates
from tankextract.discover import Block, discover_blocks
from tankextract.model import Tank
from tankextract.osgb_reader import OsgbError
from tankextract.connectors import find_connectors
from tankextract.grounding import ground_only
from tankextract.structures import collect_faces, detect_structures, tank_boxes
from tankextract.deliverable import assign_export_names, handrail_gaps, seat_walkway_ends
from tankextract.sidecar import write_connectors, write_ground_report, write_name_map, write_sidecars, write_structures
from tankextract.treefilter import TreeFilterConfig, colour_stats, judge, sample_colours
from tankextract.tools import load_osgb_bytes, normalize_child, resolve_blender, resolve_osgconv, resource_path

_LEVEL_RE = re.compile(r"_L(\d+)(?:_|\.)", re.IGNORECASE)


@dataclass
class RunOptions:
    reader: str = "auto"
    osgconv: str | None = None
    blender: str | None = None
    lod: str = "finest"
    min_radius: float = 2.0
    max_radius: float = 80.0
    min_height: float = 2.0
    max_height: float = 80.0
    max_rms: float = 0.20
    min_coverage: float = 260.0
    cell_size: float = 0.25
    max_base_above_ground: float = 3.0
    max_aspect: float = 4.0
    local_offset: bool = False
    merge: bool = True
    segments: int = 96
    precision_m: float = 0.50  # sagitta of large shells; small parts are kept
    max_faces: int = 2000  # per-object polygon budget used when quality == "budget"
    quality: str = "full"  # full keeps segments; budget retessellates shells only
    name_prefix: str = "LA"
    material_name: str = "Standard"
    model_origin: tuple[float, float, float] | None = None
    no_blender: bool = False
    limit_tiles: int | None = None
    connectors: bool = True
    sphere_bracing: bool = True
    structures: bool = True  # stair towers, roof platforms / walkways, wall stairs, sphere pedestals
    pipes: bool = False  # wall pipes, pipe runs and pipe racks (off by default)
    grounding: bool = True  # snap bottoms to the measured ground / foundation top, model foundations
    exclude: tuple[str, ...] = ()  # tank IDs (e.g. Tank_007) checked by eye and dropped; other IDs are kept
    tree_filter: bool = True  # drop tree crowns (foliage-green texture + irregular geometry), offline


def run(input_path: Path, output: Path, options: RunOptions, log=print) -> list[Tank]:
    blocks = discover_blocks(Path(input_path))
    osgconv = resolve_osgconv(options.osgconv)
    if options.reader == "osgconv" and not osgconv:
        raise FileNotFoundError(
            "指定了 --reader osgconv，但没有找到可执行文件。"
            "已查找 PATH 和 ~/3dobj_env/osg/bin/osgconv，也可用 --osgconv 指定。"
        )
    config = DetectConfig(
        min_radius=options.min_radius,
        max_radius=options.max_radius,
        min_height=options.min_height,
        max_height=options.max_height,
        max_rms=options.max_rms,
        min_coverage=options.min_coverage,
        cell_size=options.cell_size,
        max_base_above_ground=options.max_base_above_ground,
        max_aspect=options.max_aspect,
    )
    tanks: list[Tank] = []
    connectors: list = []
    structures: list = []
    struct_stats: list = []
    debug: list[dict] = []
    conn_debug: list[dict] = []
    stats = {"python": 0, "osgconv": 0, "tiles": 0}
    for block in blocks:
        log(
            f"块 {block.label}: SRS={block.srs} 原点={block.origin[0]:.3f},"
            f"{block.origin[1]:.3f},{block.origin[2]:.3f} 瓦片文件 {len(block.osgb_names)}"
        )
        found, links, extra, sstats = _detect_block(block, options, config, osgconv, stats, log, debug, conn_debug)
        tanks.extend(found)
        connectors.extend(links)
        structures.extend(extra)
        if sstats:
            struct_stats.append({"block": block.label, **sstats})
    tanks.sort(key=lambda item: (item.block, round(item.center_x, 2), round(item.center_y, 2)))
    for index, tank in enumerate(tanks, start=1):
        tank.name = f"Tank_{index:03d}"
        if tank.sphere and tank.sphere.get("legs") and options.sphere_bracing:
            tank.sphere["bracing"] = "X"
    for conn in connectors:
        conn.refresh_names()
    connectors.sort(key=lambda c: (c.block, c.tank_a, c.tank_b))
    for index, conn in enumerate(connectors, start=1):
        conn.name = f"Connector_{index:03d}"
    tree_rows: list[dict] = []
    if options.tree_filter:
        tanks, connectors, structures = _apply_tree_filter(tanks, connectors, structures, tree_rows, log)
    if options.exclude:
        # Drop tanks rejected on review (trees, etc.) after numbering, so every other tank and
        # connector keeps the ID it had in the full run; their foundations, stairs and other
        # attached parts are never built because structures are detected per remaining tank.
        drop = {name.strip() for name in options.exclude if name.strip()}
        unknown = sorted(drop - {tank.name for tank in tanks})
        if unknown:
            raise ValueError(f"--exclude 中的罐编号不存在: {', '.join(unknown)}")
        tanks = [tank for tank in tanks if tank.name not in drop]
        before = len(connectors)
        connectors = [c for c in connectors if c.tank_a not in drop and c.tank_b not in drop]
        structures = [st for st in structures if getattr(st, "parent", None) not in drop]
        log(f"  按 --exclude 剔除 {len(drop)} 个罐: {', '.join(sorted(drop))}；去掉相连走道 {before - len(connectors)} 处")
    _assume_missing_rails(connectors, log)
    ground_rows: list = []
    if (options.structures or options.grounding) and tanks:
        structures.extend(_detect_structures_all(tanks, connectors, log, struct_stats, pipes=options.pipes,
                                                 structures=options.structures,
                                                 ground_rows=ground_rows if options.grounding else None))
    seat_walkway_ends(tanks, connectors)
    name_rows = assign_export_names(tanks, connectors, structures, options.name_prefix)
    output.mkdir(parents=True, exist_ok=True)
    mode = "local" if options.local_offset else "absolute"
    write_name_map(name_rows, output)
    write_sidecars(tanks, output, mode, model_origin=options.model_origin)
    write_connectors(connectors, output, mode)
    write_structures(structures, tanks, output, mode)
    if options.grounding:
        write_ground_report(ground_rows, output)
        gaps = [abs(r["gap_before_m"]) for r in ground_rows if r.get("gap_before_m") is not None]
        if gaps:
            log(f"  落地检查：{len(ground_rows)} 个对象，修正前最大离地 / 入地 {max(gaps):.2f} m，中位 {sorted(gaps)[len(gaps) // 2]:.2f} m；修正后 0")
    (output / "structures_stats.json").write_text(json.dumps(struct_stats, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (output / "connectors_debug.json").write_text(
        json.dumps(conn_debug, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    _write_candidate_report(debug, tanks, output)
    if options.tree_filter:
        _write_tree_report(tree_rows, output)
    spec_path = output / "tanks_spec.json"
    spec_path.write_text(
        json.dumps(_spec(tanks, options, connectors, structures), ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (output / "read_stats.json").write_text(
        json.dumps({"reader_requested": options.reader, "osgconv": osgconv, **stats}, indent=2),
        encoding="utf-8",
    )
    log(f"检测到 {len(tanks)} 个罐，{len(connectors)} 处罐间连接（走道/管道），{len(structures)} 个独立附属结构"
        f"（楼梯塔/管廊/管道）。读取统计: {stats}")
    if options.no_blender:
        log("已跳过 Blender（--no-blender）。侧车文件和 tanks_spec.json 已写出。")
        return tanks
    blender = resolve_blender(options.blender)
    if not blender:
        raise FileNotFoundError(
            "未找到 Blender。Windows 安装包会自带一份；也可以传入 --blender，"
            "或安装到「Program Files\\Blender Foundation」。"
            "macOS 默认查找 /Applications/Blender.app/Contents/MacOS/Blender。"
        )
    _export_blender(blender, spec_path, output, options.merge, log)
    return tanks


def _detect_block(block: Block, options: RunOptions, config: DetectConfig, osgconv, stats, log, debug=None, conn_debug=None):
    selected = _select_tiles(block.osgb_names, options.lod)
    if options.limit_tiles:
        selected = selected[: options.limit_tiles]
    names = set(block.osgb_names)
    accumulator = SceneAccumulator(config)
    loaded: list[tuple[str, str]] = []  # name, reader used

    queue = list(selected)
    seen: set[str] = set()
    while queue:
        name = queue.pop(0)
        if name in seen or name not in names:
            continue
        seen.add(name)
        try:
            contents, used = _read_tile(block, name, options.reader, osgconv)
        except OsgbError as exc:
            # A damaged tile in the source data (e.g. truncated on export) must not stop the
            # block: skip it, and list it in read_stats.json so the gap can be checked.
            _skip_tile(stats, name, exc, log)
            continue
        stats[used] = stats.get(used, 0) + 1
        stats["tiles"] += 1
        children = []
        if options.lod == "finest":
            for child in contents.child_files:
                resolved = _resolve_existing(name, child, names)
                if resolved and resolved not in seen:
                    children.append(resolved)
        if children:
            # This file still has a finer level on disk. Do not use its mesh.
            queue.extend(children)
            continue
        for mesh in contents.meshes:
            accumulator.add_mesh(name, mesh.vertices, mesh.triangles, block.origin)
        loaded.append((name, used))
        if stats["tiles"] % 25 == 0:
            log(f"  已处理 {stats['tiles']} 个瓦片…")

    rough = accumulator.candidates()
    log(f"  {block.label}: 候选圆柱 {len(rough)}，参与几何的瓦片 {len(loaded)}")
    if not rough:
        return [], [], [], {}
    # Second pass: only tiles whose box overlaps a candidate.
    tiles = []
    for name, _used in loaded:
        box = _box_for(accumulator, name)
        if box is None or not _overlaps_any(box, rough):
            continue
        try:
            contents, used = _read_tile(block, name, "python" if used == "python" else options.reader, osgconv)
        except OsgbError as exc:
            _skip_tile(stats, name, exc, log)
            continue
        for mesh in contents.meshes:
            absolute = np.array(mesh.vertices, dtype=np.float64, copy=True)
            absolute[:, 0] += block.origin[0]
            absolute[:, 1] += block.origin[1]
            absolute[:, 2] += block.origin[2]
            tiles.append((absolute, mesh.triangles))
    found = refine_candidates(
        rough,
        tiles,
        config,
        accumulator.ground_near,
        block.label,
        block.srs,
        block.origin,
        debug=debug,
    )
    links = find_connectors(found, tiles, accumulator.ground_near, conn_debug) if options.connectors else []
    if not options.pipes:
        links = [c for c in links if c.kind != "pipe"]  # roof-to-roof pipes are left to the pipe model
    if links:
        log(f"  {block.label}: 罐间连接 {len(links)} 处")
    faces = None
    if options.structures and found:
        faces = collect_faces(tiles, tank_boxes(found))
        log(f"  {block.label}: 附属结构检测用面片 {len(faces)}")
    for tank in found:
        tank._faces = faces  # released after structure detection
    if options.tree_filter and found:
        _measure_colours(block, found, accumulator, options, osgconv, stats, log)
    return found, links, [], {}


def _measure_colours(block: Block, tanks: list[Tank], accumulator, options: RunOptions, osgconv, stats, log) -> None:
    """Texture colour inside each kept cylinder, from the textures embedded in the tiles.

    Re-reads only the finest tiles that overlap a kept tank, with textures, using the
    built-in reader (osgconv output carries no textures; those tanks fall back to the
    geometry-only tree test).
    """
    hits: dict[str, list] = {}
    for name, box in accumulator.tile_boxes:  # one box per mesh of every finest tile
        for t in tanks:
            if (box[0, 0] - t.radius - 1 < t.center_x < box[1, 0] + t.radius + 1
                    and box[0, 1] - t.radius - 1 < t.center_y < box[1, 1] + t.radius + 1):
                lst = hits.setdefault(name, [])
                if t not in lst:
                    lst.append(t)
    cache: dict = {}
    per_tank: dict[int, list] = {id(t): [] for t in tanks}
    names = list(hits)
    textured = 0
    for name in names:
        try:
            contents, _used = _read_tile(block, name, "python", None, textures=True)
        except Exception:  # noqa: BLE001 - texture pass is best effort, never fatal
            continue
        meshes = []
        for mesh in contents.meshes:
            if mesh.uv is None or mesh.image is None:
                continue
            absolute = np.array(mesh.vertices, dtype=np.float64, copy=True)
            absolute += np.asarray(block.origin, dtype=np.float64)
            meshes.append((absolute, mesh.triangles, mesh.uv, mesh.image))
        if not meshes:
            continue
        textured += 1
        for t in hits[name]:
            top = t.z_base + max(t.total_height, t.wall_height) + 1.0
            rgb = sample_colours(meshes, t.center_x, t.center_y, t.radius, t.z_base + 0.3, top, cache)
            if len(rgb):
                per_tank[id(t)].append(rgb)
        cache.clear()  # decoded textures are per tile
    for t in tanks:
        chunks = per_tank[id(t)]
        rgb = np.concatenate(chunks, axis=0) if chunks else np.zeros((0, 3), dtype=np.uint8)
        t.tree_check["colour"] = colour_stats(rgb)
    log(f"  {block.label}: 罐体纹理颜色检查（树冠过滤）读取带纹理瓦片 {textured}/{len(names)}")


def _apply_tree_filter(tanks, connectors, structures, rows: list, log, config: TreeFilterConfig | None = None):
    """Drop tree crowns after numbering, so every real tank keeps the ID it has in a full run."""
    config = config or TreeFilterConfig()
    drop = set()
    for tank in tanks:
        geom = tank.tree_check.get("geometry", {})
        colour = tank.tree_check.get("colour")
        if geom.get("sphere"):
            is_tree, reason = False, ""
        else:
            is_tree, reason = judge(geom, colour, tank.radius, config)
        tank.tree_check["tree"] = is_tree
        tank.tree_check["reason"] = reason
        colour = colour or {}
        rows.append({
            "block": tank.block, "tank_id": tank.name, "X": tank.center_x, "Y": tank.center_y,
            "radius_m": tank.radius, "tree": is_tree,
            "green_fraction": colour.get("green_fraction"), "texture_samples": colour.get("samples", 0),
            "mean_rgb": colour.get("mean_rgb"),
            "wall_rms_m": geom.get("rms"), "wall_fill": geom.get("wall_fill"), "roof_rms_m": geom.get("roof_rms"),
            "reason": reason,
        })
        if is_tree:
            drop.add(tank.name)
    if not drop:
        log("  树冠过滤：未发现树冠")
        return tanks, connectors, structures
    for tank in tanks:
        if tank.name in drop:
            log(f"  树冠过滤：剔除 {tank.block} {tank.name} ({tank.center_x:.2f}, {tank.center_y:.2f}) r={tank.radius:.2f}m — {tank.tree_check['reason']}")
    tanks = [t for t in tanks if t.name not in drop]
    connectors = [c for c in connectors if c.tank_a not in drop and c.tank_b not in drop]
    structures = [st for st in structures if getattr(st, "parent", None) not in drop]
    log(f"  树冠过滤：共剔除 {len(drop)} 个（可用 --no-tree-filter 关闭）")
    return tanks, connectors, structures


def _write_tree_report(rows: list[dict], output: Path) -> None:
    """tree_filter_report.csv: colour / geometry cues of every detected cylinder and the verdict."""
    import csv

    fields = ["block", "tank_id", "X", "Y", "radius_m", "tree", "green_fraction", "texture_samples", "mean_rgb",
              "wall_rms_m", "wall_fill", "roof_rms_m", "reason"]
    output.mkdir(parents=True, exist_ok=True)

    def fmt(v, nd):
        return "" if v is None else f"{v:.{nd}f}"

    with open(output / "tree_filter_report.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for r in sorted(rows, key=lambda r: (not r["tree"], r["block"], r["tank_id"])):
            writer.writerow([
                r["block"], r["tank_id"], fmt(r["X"], 3), fmt(r["Y"], 3), fmt(r["radius_m"], 3), r["tree"],
                fmt(r["green_fraction"], 3), r["texture_samples"],
                " ".join(str(c) for c in r["mean_rgb"]) if r["mean_rgb"] else "",
                fmt(r["wall_rms_m"], 4), fmt(r["wall_fill"], 2), fmt(r["roof_rms_m"], 4), r["reason"],
            ])


def _assume_missing_rails(connectors, log) -> None:
    """Walkways high above the ground always carry guard rails; when the mesh is too thin to
    show them, use the median measured rail height of the site (marked as assumed)."""
    measured = [c.handrail_height for c in connectors if c.kind == "walkway" and c.handrail_height]
    if not measured:
        return
    median = float(np.median(measured))
    for conn in connectors:
        if conn.kind == "walkway" and conn.handrail_height is None:
            conn.handrail_height = median
            conn.rail_source = "assumed (site median, rail not visible in mesh)"
            log(f"  {conn.name}: 护栏在网格中不可见，按全场中位数 {median:.2f} m 补全")


def _detect_structures_all(tanks, connectors, log, struct_stats, pipes: bool = False, structures: bool = True,
                           ground_rows: list | None = None) -> list:
    out = []
    groups: dict[int, list] = {}
    for tank in tanks:
        faces = getattr(tank, "_faces", None)
        if faces is not None:
            groups.setdefault(id(faces), [faces, []])[1].append(tank)
    for faces, group in groups.values():
        links = [c for c in connectors if c.tank_refs and c.tank_refs[0] in group]
        if structures:
            found, sstats = detect_structures(group, links, faces, log, pipes=pipes, ground_rows=ground_rows)
        else:
            found, sstats = [], {"faces": int(len(faces))}
            if ground_rows is not None:
                ground_only(group, links, faces, ground_rows)
        block = group[0].block
        for st in found:
            st.block, st.srs, st.origin = block, group[0].srs, tuple(group[0].origin)
        out.extend(found)
        sstats["block"] = block
        struct_stats.append(sstats)
        log(f"  {block}: 附属结构 {sstats}")
    for tank in tanks:
        if hasattr(tank, "_faces"):
            del tank._faces
    # global names, ordered by kind then position
    order = {"stair_tower": "Stair", "pipe_rack": "PipeRack", "pipe_run": "Pipe"}
    for kind, prefix in order.items():
        items = sorted([s for s in out if s.kind == kind], key=lambda s: (s.block, round(s.anchor[0], 1), round(s.anchor[1], 1)))
        for index, st in enumerate(items, start=1):
            _rename(st, f"{prefix}_{index:03d}")
    for conn in connectors:
        for sp in conn.supports:
            tower = sp.get("_tower")
            if tower is not None:
                sp["stair_tower"] = tower.name
                del sp["_tower"]
    for r in ground_rows or []:
        ref = r.pop("_ref", None)
        r.pop("_support", None)
        if ref is not None:
            r["object"] = ref.name
    return out


def _rename(st, new: str) -> None:
    old = st.name
    for part in st.parts:
        if part["name"].startswith(old):
            part["name"] = new + part["name"][len(old):]
    st.name = new


def _write_candidate_report(debug: list[dict], tanks: list[Tank], output: Path) -> None:
    """candidates.csv: every pass-1 cylinder candidate and why it was kept or rejected."""
    import csv

    names = {id(tank): tank.name for tank in tanks}
    fields = [
        "tank_id", "kept", "reason", "X", "Y", "radius_m", "rms_m", "coverage_deg",
        "wall_extent_level_m", "z_base", "wall_height_m", "ground_z", "interior_ratio", "wall_fill",
        "roof_profile",
    ]
    output.mkdir(parents=True, exist_ok=True)
    with open(output / "candidates.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for rec in debug:
            tank = rec.get("tank")
            kept, reason = rec["kept"], rec["reason"]
            if tank is not None and tank.tree_check.get("tree"):
                kept, reason = False, "tree crown: " + tank.tree_check.get("reason", "")
            writer.writerow([
                names.get(id(tank), "") if tank is not None else "",
                kept,
                reason,
                f"{rec['cx']:.3f}",
                f"{rec['cy']:.3f}",
                f"{rec.get('fit_radius', rec['radius']):.3f}",
                f"{rec.get('fit_rms', rec['rms']):.4f}",
                f"{rec.get('fit_coverage', rec['coverage']):.0f}",
                rec["level"],
                f"{rec['z_base']:.2f}" if "z_base" in rec else "",
                f"{rec['wall_height']:.2f}" if "wall_height" in rec else "",
                f"{rec['ground']:.2f}" if "ground" in rec else "",
                f"{rec['interior_ratio']:.3f}" if "interior_ratio" in rec else "",
                f"{rec['wall_fill']:.2f}" if "wall_fill" in rec else "",
                json.dumps({"profile": rec.get("roof_profile", []), "fit": rec.get("roof_fit", {})}),
            ])


def _box_for(accumulator: SceneAccumulator, name: str):
    boxes = [box for tile, box in accumulator.tile_boxes if tile == name]
    if not boxes:
        return None
    stacked = np.stack(boxes, axis=0)
    return np.vstack([stacked[:, 0, :].min(axis=0), stacked[:, 1, :].max(axis=0)])


def _overlaps_any(box: np.ndarray, candidates) -> bool:
    for candidate in candidates:
        margin = candidate.radius + 14.0  # + corridors, stair towers and pipe racks around it
        if box[1, 0] < candidate.cx - margin or box[0, 0] > candidate.cx + margin:
            continue
        if box[1, 1] < candidate.cy - margin or box[0, 1] > candidate.cy + margin:
            continue
        return True
    return False


def _skip_tile(stats: dict, name: str, exc: Exception, log) -> None:
    stats["unreadable"] = stats.get("unreadable", 0) + 1
    bad = stats.setdefault("unreadable_tiles", [])
    if name not in bad and len(bad) < 200:
        bad.append(name)
        log(f"  警告：瓦片无法读取，已跳过 {name}: {str(exc)[-160:]}")


def _read_tile(block: Block, name: str, reader: str, osgconv: str | None, textures: bool = False):
    data = block.read_bytes(name)
    try:
        return load_osgb_bytes(data, reader=reader, osgconv=osgconv, filename=posixpath.basename(name),
                               textures=textures)
    except OsgbError as exc:
        raise OsgbError(f"{name}: {exc}") from exc


def _resolve_existing(parent: str, child: str, names: set[str]) -> str | None:
    joined = normalize_child(parent, child)
    if joined in names:
        return joined
    base = posixpath.basename(child.replace("\\", "/"))
    parent_dir = posixpath.dirname(parent)
    sibling = posixpath.join(parent_dir, base) if parent_dir else base
    if sibling in names:
        return sibling
    return None


def _select_tiles(names: list[str], lod: str) -> list[str]:
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(posixpath.dirname(name), []).append(name)
    chosen: list[str] = []
    # "finest" cannot keep only the deepest filename level in a folder.
    # A PagedLOD branch may end one level early, and that coarser file is a
    # real leaf. The read loop drops a file only when its own children exist.
    if lod == "finest":
        return list(names)
    want = int(lod)
    for files in groups.values():
        levels = [(_level(name), name) for name in files]
        chosen.extend(name for level, name in levels if level == want)
    return chosen


def _level(name: str) -> int:
    found = _LEVEL_RE.findall(posixpath.basename(name))
    if not found:
        return -1
    return int(found[-1])


_SPHERE_Z_KEYS = ("center_z", "leg_bottom", "leg_top", "leg_contact_z", "brace_z0", "brace_z1")


def _local_sphere(tank: Tank) -> dict | None:
    if not tank.sphere:
        return None
    sphere = dict(tank.sphere)
    sphere.setdefault("leg_bottom", tank.z_base)
    for key in _SPHERE_Z_KEYS:
        if key in sphere:
            sphere[key] = float(sphere[key]) - tank.origin[2]
    return sphere


def _local_part(part: dict, origin) -> dict:
    ox, oy, oz = origin
    out = dict(part)
    for key in ("p0", "p1", "center"):
        if key in out:
            if len(out[key]) == 3:
                x, y, z = out[key]
                out[key] = [x - ox, y - oy, z - oz]
            else:  # plan-only centre (cylinder): heights are in z0 / z1
                x, y = out[key]
                out[key] = [x - ox, y - oy]
    if "polygon" in out:
        out["polygon"] = [[x - ox, y - oy] for x, y in out["polygon"]]
    if "points" in out:
        shifted = []
        for point in out["points"]:
            if len(point) == 3:
                shifted.append([point[0] - ox, point[1] - oy, point[2] - oz])
            else:
                shifted.append([point[0] - ox, point[1] - oy])
        out["points"] = shifted
    for key in ("z0", "z1"):
        if key in out:
            out[key] = out[key] - oz
    return out


def _spec(tanks: list[Tank], options: RunOptions, connectors=None, structures=None) -> dict:
    records = []
    for tank in tanks:
        lx, ly = tank.center_local
        records.append(
            {
                "id": tank.name,
                "export_name": getattr(tank, "export_name", "") or tank.name,
                "origin": list(tank.origin),
                "handrail_gaps": handrail_gaps(tank),
                "center_local": [lx, ly],
                "z_base": tank.z_base_local,
                "radius": tank.radius,
                "wall_height": tank.wall_height,
                "total_height": tank.total_height,
                "roof_type": tank.roof_type,
                "roof_rise": tank.roof_rise,
                "top_rim_width": tank.top_rim_width,
                "wind_girders": [
                    {
                        "z": item["z"] - tank.origin[2],
                        "protrusion": item["protrusion"],
                        "height": item.get("height", 0.15),
                        "diameter": item.get("diameter"),
                        "center_rho": item.get("center_rho"),
                    }
                    for item in tank.wind_girders
                ],
                "base_ring": (
                    None
                    if not tank.base_ring
                    else {
                        "protrusion": tank.base_ring["protrusion"],
                        "height": tank.base_ring.get("height", 0.22),
                    }
                ),
                "spiral_stair": tank.spiral_stair,
                "handrail_height": tank.handrail_height,
                "sphere": _local_sphere(tank),
                "extra_parts": [_local_part(p, tank.origin) for p in (tank.extra_parts or [])],
                "epsg": tank.srs,
                "rms_m": tank.rms,
            }
        )
    links = []
    for conn in connectors or []:
        ox, oy, oz = conn.origin
        links.append({
            "id": conn.name,
            "export_name": getattr(conn, "export_name", "") or conn.name,
            "kind": conn.kind,
            "tank_a": conn.tank_a,
            "tank_b": conn.tank_b,
            "origin": list(conn.origin),
            "start_local": [conn.x0 - ox, conn.y0 - oy, conn.z0 - oz],
            "end_local": [conn.x1 - ox, conn.y1 - oy, conn.z1 - oz],
            "width": conn.width,
            "depth": conn.depth,
            "handrail_height": conn.handrail_height,
            "junctions": list(getattr(conn, "junctions", None) or []),
            "supports": [
                {**sp, "x": sp["x"] - ox, "y": sp["y"] - oy, "z_bottom": sp["z_bottom"] - oz, "z_top": sp["z_top"] - oz}
                for sp in conn.supports
            ],
            "epsg": conn.srs,
        })
    extra = []
    for st in structures or []:
        ox, oy, oz = st.origin
        extra.append({
            "id": st.name, "export_name": getattr(st, "export_name", "") or st.name,
            "kind": st.kind, "parent": st.parent, "origin": list(st.origin),
            "anchor_local": [st.anchor[0] - ox, st.anchor[1] - oy, st.anchor[2] - oz],
            "parts": [_local_part(p, st.origin) for p in st.parts], "epsg": st.srs,
        })
    return {
        "coordinate_mode": "local" if options.local_offset else "absolute",
        "model_origin": list(options.model_origin) if options.model_origin else None,
        "vertical_datum": None,
        "precision_m": options.precision_m,
        "max_faces": options.max_faces,
        "quality": options.quality,
        "material_name": options.material_name,
        "segments": options.segments,
        "pipes": "measured_wall_pipes_only" if options.pipes else "omitted_for_laser",
        "tanks": records,
        "connectors": links,
        "structures": extra,
    }


def _export_blender(blender: str, spec_path: Path, output: Path, merge: bool, log) -> None:
    import subprocess

    script = resource_path("tankextract", "blender", "export_tanks.py")
    if not script.is_file():
        script = Path(__file__).resolve().parent / "blender" / "export_tanks.py"
    if not script.is_file():
        raise FileNotFoundError(f"找不到 Blender 导出脚本: {script}")
    cmd = [
        blender,
        "-b",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "-P",
        str(script),
        "--",
        "--spec",
        str(spec_path),
        "--output",
        str(output),
    ]
    if merge:
        cmd.append("--merge")
    log("调用 Blender 导出 FBX…")
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    log_path = output / "blender.log"
    log_path.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise RuntimeError(f"Blender 导出失败（退出码 {proc.returncode}）。详见 {log_path}\n{tail}")
    if not (output / "blender_report.json").exists():
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise RuntimeError(f"Blender 未写出 FBX（脚本出错）。详见 {log_path}\n{tail}")
    log(f"FBX 已写入 {output}")
