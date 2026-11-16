"""Stream tiles, detect tanks, write sidecars and Blender FBX."""

from __future__ import annotations

import json
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
from tankextract.structures import collect_faces, detect_structures, tank_boxes
from tankextract.sidecar import write_connectors, write_sidecars, write_structures
from tankextract.tools import load_osgb_bytes, normalize_child, resolve_blender, resolve_osgconv

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
    no_blender: bool = False
    limit_tiles: int | None = None
    connectors: bool = True
    sphere_bracing: bool = True
    structures: bool = True  # stair towers, roof platforms / walkways, wall stairs, sphere pedestals
    pipes: bool = False  # wall pipes, pipe runs and pipe racks (off by default)


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
    _assume_missing_rails(connectors, log)
    if options.structures and tanks:
        structures.extend(_detect_structures_all(tanks, connectors, log, struct_stats, pipes=options.pipes))
    output.mkdir(parents=True, exist_ok=True)
    mode = "local" if options.local_offset else "absolute"
    write_sidecars(tanks, output, mode)
    write_connectors(connectors, output, mode)
    write_structures(structures, tanks, output, mode)
    (output / "structures_stats.json").write_text(json.dumps(struct_stats, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (output / "connectors_debug.json").write_text(
        json.dumps(conn_debug, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    _write_candidate_report(debug, tanks, output)
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
            "未找到 Blender。macOS 默认查找 /Applications/Blender.app/Contents/MacOS/Blender，"
            "也可以把 blender 放入 PATH，或传入 --blender。"
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
        contents, used = _read_tile(block, name, options.reader, osgconv)
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
        contents, used = _read_tile(block, name, "python" if used == "python" else options.reader, osgconv)
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
    return found, links, [], {}


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


def _detect_structures_all(tanks, connectors, log, struct_stats, pipes: bool = False) -> list:
    out = []
    groups: dict[int, list] = {}
    for tank in tanks:
        faces = getattr(tank, "_faces", None)
        if faces is not None:
            groups.setdefault(id(faces), [faces, []])[1].append(tank)
    for faces, group in groups.values():
        links = [c for c in connectors if c.tank_refs and c.tank_refs[0] in group]
        found, sstats = detect_structures(group, links, faces, log, pipes=pipes)
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
            writer.writerow([
                names.get(id(tank), "") if tank is not None else "",
                rec["kept"],
                rec["reason"],
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


def _read_tile(block: Block, name: str, reader: str, osgconv: str | None):
    data = block.read_bytes(name)
    try:
        return load_osgb_bytes(data, reader=reader, osgconv=osgconv, filename=posixpath.basename(name))
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
    want = None if lod == "finest" else int(lod)
    for files in groups.values():
        levels = [(_level(name), name) for name in files]
        if want is None:
            deepest = max(level for level, _ in levels)
            chosen.extend(name for level, name in levels if level == deepest)
        else:
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
            x, y, z = out[key]
            out[key] = [x - ox, y - oy, z - oz]
    if "polygon" in out:
        out["polygon"] = [[x - ox, y - oy] for x, y in out["polygon"]]
        out["z0"] = out["z0"] - oz
        out["z1"] = out["z1"] - oz
    return out


def _spec(tanks: list[Tank], options: RunOptions, connectors=None, structures=None) -> dict:
    records = []
    for tank in tanks:
        lx, ly = tank.center_local
        records.append(
            {
                "id": tank.name,
                "origin": list(tank.origin),
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
            "kind": conn.kind,
            "tank_a": conn.tank_a,
            "tank_b": conn.tank_b,
            "origin": list(conn.origin),
            "start_local": [conn.x0 - ox, conn.y0 - oy, conn.z0 - oz],
            "end_local": [conn.x1 - ox, conn.y1 - oy, conn.z1 - oz],
            "width": conn.width,
            "depth": conn.depth,
            "handrail_height": conn.handrail_height,
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
            "id": st.name, "kind": st.kind, "parent": st.parent, "origin": list(st.origin),
            "anchor_local": [st.anchor[0] - ox, st.anchor[1] - oy, st.anchor[2] - oz],
            "parts": [_local_part(p, st.origin) for p in st.parts], "epsg": st.srs,
        })
    return {
        "coordinate_mode": "local" if options.local_offset else "absolute",
        "segments": options.segments,
        "tanks": records,
        "connectors": links,
        "structures": extra,
    }


def _export_blender(blender: str, spec_path: Path, output: Path, merge: bool, log) -> None:
    import subprocess

    script = Path(__file__).resolve().parent / "blender" / "export_tanks.py"
    cmd = [
        blender,
        "-b",
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
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    log_path = output / "blender.log"
    log_path.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise RuntimeError(f"Blender 导出失败（退出码 {proc.returncode}）。详见 {log_path}\n{tail}")
    if not (output / "blender_report.json").exists():
        tail = (proc.stderr or proc.stdout or "")[-2000:]
        raise RuntimeError(f"Blender 未写出 FBX（脚本出错）。详见 {log_path}\n{tail}")
    log(f"FBX 已写入 {output}")
