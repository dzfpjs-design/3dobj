"""Headless Blender exporter: parametric white tanks to FBX.

Run with:
    blender -b -P export_tanks.py -- --spec tanks_spec.json --output outdir [--merge]

Mesh vertices are metres, Z up. ``coordinate_mode`` ``absolute`` adds each
tank's SRSOrigin (float64 in this script, then Blender stores float32).
``local`` keeps tile-local metres and records the origin as custom properties.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    spec_path, output, merge = _args(argv)
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    _reset_scene()
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    material = _white_material()
    mode = spec.get("coordinate_mode", "absolute")
    segments = int(spec.get("segments", 96))
    report = {"coordinate_mode": mode, "files": []}
    all_objects = []
    for tank in spec["tanks"]:
        objects = _build_tank(tank, segments, mode, material)
        all_objects.extend(objects)
        path = output / f"{tank['id']}.fbx"
        _export(path, objects)
        sample = _sample_vertex(objects)
        re_sample, re_names, re_props = _reimport_sample(path, tank["id"])
        report["files"].append(
            {
                "path": str(path),
                "objects": [obj.name for obj in objects],
                "sample_vertex": sample,
                "reimport_vertex": re_sample,
                "reimport_objects": re_names,
                "reimport_props": re_props,
                "epsg": tank.get("epsg", ""),
                "origin": tank["origin"],
            }
        )
    links = spec.get("connectors") or []
    if links:
        _reset_scene()
        material = _white_material()
        objects = []
        for conn in links:
            objects.extend(_build_connector(conn, mode, material))
        path = output / "connectors.fbx"
        _export(path, objects)
        report["connectors_file"] = str(path)
        report["connector_objects"] = [obj.name for obj in objects]
    extras = spec.get("structures") or []
    if extras:
        _reset_scene()
        material = _white_material()
        objects = []
        for st in extras:
            objects.extend(_build_structure(st, mode, material))
        path = output / "structures.fbx"
        _export(path, objects)
        report["structures_file"] = str(path)
        report["structure_objects"] = [obj.name for obj in objects]
    if merge and all_objects:
        # Rebuild so per-tank exports (which reimport) cannot disturb the merge.
        _reset_scene()
        material = _white_material()
        merged = []
        for tank in spec["tanks"]:
            merged.extend(_build_tank(tank, segments, mode, material))
        for conn in links:
            merged.extend(_build_connector(conn, mode, material))
        for st in extras:
            merged.extend(_build_structure(st, mode, material))
        path = output / "tanks_all.fbx"
        _export(path, merged)
        report["merged"] = str(path)
    (output / "blender_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"tankextract: wrote {len(report['files'])} FBX files")


def _args(argv: list[str]) -> tuple[str, str, bool]:
    spec = output = None
    merge = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--spec":
            spec = argv[index + 1]
            index += 2
        elif token == "--output":
            output = argv[index + 1]
            index += 2
        elif token == "--merge":
            merge = True
            index += 1
        else:
            index += 1
    if not spec or not output:
        raise SystemExit("需要 --spec 和 --output")
    return spec, output, merge


def _reset_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)


def _white_material():
    material = bpy.data.materials.new("White")
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        principled.inputs["Roughness"].default_value = 0.55
    material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    return material


def _build_tank(tank: dict, segments: int, mode: str, material) -> list:
    origin = tank["origin"]
    cx, cy = tank["center_local"]
    z0 = float(tank["z_base"])
    z1 = z0 + float(tank["wall_height"])
    radius = float(tank["radius"])
    pieces = []
    sphere = tank.get("sphere")
    if sphere:
        zc = float(sphere["center_z"])
        rs = float(sphere["radius"])
        pieces.append(("body", *_uv_sphere(cx, cy, zc, rs, segments, max(12, segments // 2))))
        leg_r = float(sphere.get("leg_radius") or 0.3)
        leg_rho = float(sphere.get("leg_rho") or rs * 0.97)
        bottom = float(sphere.get("leg_bottom", z0))
        for index, azimuth in enumerate(sphere.get("legs") or [], start=1):
            a = math.radians(float(azimuth))
            lx, ly = cx + leg_rho * math.cos(a), cy + leg_rho * math.sin(a)
            pieces.append((f"leg_{index:02d}", *_cylinder(lx, ly, bottom, zc, leg_r, 12, caps=True)))
        legs = [float(a) for a in sphere.get("legs") or []]
        if sphere.get("bracing") and len(legs) >= 3:
            # X bracing between neighbouring legs, from just above the base to
            # just under where the legs meet the shell.
            lo = float(sphere["brace_z0"]) if sphere.get("brace_z0") is not None else bottom + 0.8
            hi = (float(sphere["brace_z1"]) if sphere.get("brace_z1") is not None
                  else float(sphere.get("leg_contact_z", zc - 0.25 * rs)) - 0.5)
            if hi > lo + 1.0:
                pts = [
                    (cx + leg_rho * math.cos(math.radians(a)), cy + leg_rho * math.sin(math.radians(a)))
                    for a in legs
                ]
                for index in range(len(pts)):
                    (x0, y0), (x1, y1) = pts[index], pts[(index + 1) % len(pts)]
                    pieces.append((f"brace_{index + 1:02d}a", *_rod((x0, y0, lo), (x1, y1, hi), 0.05, 8)))
                    pieces.append((f"brace_{index + 1:02d}b", *_rod((x0, y0, hi), (x1, y1, lo), 0.05, 8)))
        pieces.extend(_part_pieces(tank.get("extra_parts") or [], tank["id"]))
        return _finish(pieces, tank, mode, material, tank["id"], (cx, cy, z0))
    roof = tank.get("roof_type", "flat")
    rise = float(tank.get("roof_rise") or 0.0)
    # One closed, manifold shell: bottom plate + wall + roof share their rim vertices.
    pieces.append(("body", *_closed_body(cx, cy, z0, z1, radius, roof, rise, segments)))
    handrail = tank.get("handrail_height")
    if handrail:
        # Lifted 2 cm off the wall top so its bottom does not z-fight with
        # the roof edge and rim annulus.
        pieces.append(
            ("handrail", *_band(cx, cy, z1 + 0.02, float(handrail) - 0.02, radius - 0.06, radius, segments))
        )
    rim = tank.get("top_rim_width")
    if rim:
        pieces.append(
            ("top_rim", *_annulus(cx, cy, z1, max(radius - 0.05, 0.01), radius + float(rim) * 0.5, segments))
        )
    for index, girder in enumerate(tank.get("wind_girders") or [], start=1):
        gz = float(girder["z"])
        prot = float(girder["protrusion"])
        height = float(girder.get("height") or 0.15)
        pieces.append(
            (
                f"wind_girder_{index:02d}",
                *_band(cx, cy, gz - height / 2, height, radius, radius + prot, segments),
            )
        )
    ring = tank.get("base_ring")
    if ring:
        prot = float(ring["protrusion"])
        height = float(ring.get("height") or 0.22)
        pieces.append(("base_ring", *_band(cx, cy, z0, height, radius, radius + prot, segments)))
    stair = tank.get("spiral_stair")
    if stair:
        outer = float(stair["outer_radius"])
        width = float(stair["width"])
        turns = float(stair["turns"])
        pieces.append(
            (
                "stair",
                *_stair(cx, cy, z0, float(tank["wall_height"]), max(outer - width, radius + 0.05), outer, turns, segments // 2),
            )
        )
    pieces.extend(_part_pieces(tank.get("extra_parts") or [], tank["id"]))
    return _finish(pieces, tank, mode, material, tank["id"], (cx, cy, z0))


def _finish(pieces, tank: dict, mode: str, material, group: str, anchor) -> list:
    """One parent empty per tank / connector, one mesh object per part.

    Pieces with the same part name (wall + roof -> ``body``) are merged. The
    empty sits at ``anchor`` (tank centre at its base, in real coordinates);
    child vertices are stored relative to it, so world positions stay real
    and each part can be moved or edited on its own.
    """
    origin = tank["origin"]
    merged: dict[str, tuple[list, list]] = {}
    for name, verts, faces in pieces:
        verts_acc, faces_acc = merged.setdefault(name, ([], []))
        base = len(verts_acc)
        verts_acc.extend(verts)
        faces_acc.extend(tuple(i + base for i in face) for face in faces)
    ax, ay, az = _place([tuple(anchor)], origin, mode)[0]
    empty = bpy.data.objects.new(group, None)
    empty.empty_display_type = "PLAIN_AXES"
    empty.location = (ax, ay, az)
    bpy.context.scene.collection.objects.link(empty)
    _stamp(empty, tank, mode)
    empty["part"] = "group"
    objects = [empty]
    for name, (verts, faces) in merged.items():
        placed = _place(verts, origin, mode)
        rel = [(x - ax, y - ay, z - az) for x, y, z in placed]
        obj = _object(f"{group}_{name}", rel, faces, material)
        obj.parent = empty
        _stamp(obj, tank, mode)
        obj["part"] = name
        objects.append(obj)
    return objects


def _build_connector(conn: dict, mode: str, material) -> list:
    """Walkway deck (box), handrails (top + mid rail, posts) and supports; or a pipe."""
    cid = conn["id"]
    p0 = [float(v) for v in conn["start_local"]]
    p1 = [float(v) for v in conn["end_local"]]
    width = float(conn["width"])
    depth = float(conn.get("depth") or 0.2)
    pieces = []
    if conn.get("kind") == "pipe":
        r = width / 2.0
        pieces.append(("pipe", *_rod((p0[0], p0[1], p0[2] - r), (p1[0], p1[1], p1[2] - r), r, 16)))
    else:
        pieces.append(("deck", *_beam(p0, p1, width, depth)))
        rail = conn.get("handrail_height")
        if rail:
            rail = float(rail)
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            run = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / run, dx / run
            off = width / 2.0 - 0.03
            posts = max(2, int(run / 1.5) + 1)
            for side, sign in (("L", 1.0), ("R", -1.0)):
                ox, oy = nx * off * sign, ny * off * sign
                for level, tag in ((rail, "top"), (rail * 0.5, "mid")):
                    a = (p0[0] + ox, p0[1] + oy, p0[2] + level)
                    b = (p1[0] + ox, p1[1] + oy, p1[2] + level)
                    pieces.append((f"rail_{side}_{tag}", *_rod(a, b, 0.025, 8)))
                for k in range(posts):
                    f = k / (posts - 1)
                    x = p0[0] + dx * f + ox
                    y = p0[1] + dy * f + oy
                    z = p0[2] + (p1[2] - p0[2]) * f
                    pieces.append((f"post_{side}_{k + 1:02d}", *_rod((x, y, z), (x, y, z + rail), 0.025, 6)))
    for index, sp in enumerate(conn.get("supports") or [], start=1):
        if sp.get("stair_tower"):
            continue  # modelled as its own Stair_### group (open frame, flights, landings)
        pieces.append((
            f"support_{index:02d}",
            *_vbox(float(sp["x"]), float(sp["y"]), float(sp["z_bottom"]), float(sp["z_top"]),
                   float(sp.get("length", 0.4)), float(sp.get("width", 0.4)), float(sp.get("axis_deg", 0.0))),
        ))
    meta = {
        "id": cid, "origin": conn["origin"], "epsg": conn.get("epsg", ""), "radius": 0.0,
        "wall_height": 0.0, "total_height": 0.0, "roof_type": conn.get("kind", ""), "rms_m": 0.0,
    }
    objects = _finish(pieces, meta, mode, material, cid, p0)
    for obj in objects:
        obj["connector_id"] = cid
        obj["tank_a"] = conn.get("tank_a", "")
        obj["tank_b"] = conn.get("tank_b", "")
    return objects


def _part_pieces(parts, group: str) -> list:
    """Primitive parts from the spec (rod / beam / box / prism) -> (name, verts, faces).

    Part names carry the group prefix (``Tank_015_pedestal_01``); the piece name is the
    remainder so ``_finish`` names the object ``{group}_{rest}``."""
    out = []
    for part in parts:
        name = part["name"]
        rest = name[len(group) + 1:] if name.startswith(group + "_") else name
        kind = part["type"]
        if kind == "rod":
            segs = 12 if part["radius"] > 0.1 else 8
            out.append((rest, *_rod(part["p0"], part["p1"], part["radius"], segs)))
        elif kind == "beam":
            out.append((rest, *_beam(part["p0"], part["p1"], part["width"], part["depth"])))
        elif kind == "box":
            cx, cy, cz = part["center"]
            length, width, height = part["size"]
            out.append((rest, *_vbox(cx, cy, cz - height / 2, cz + height / 2, length, width, part["yaw_deg"])))
        elif kind == "prism":
            out.append((rest, *_prism(part["polygon"], part["z0"], part["z1"])))
        elif kind == "cylinder":
            cx, cy = part["center"]
            out.append((rest, *_cylinder(cx, cy, part["z0"], part["z1"], part["radius"], int(part.get("segments", 96)), caps=True)))
    return out


def _prism(polygon, z0, z1):
    pts = [tuple(p) for p in polygon]
    area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    if area < 0:
        pts = pts[::-1]
    n = len(pts)
    verts = [(x, y, z0) for x, y in pts] + [(x, y, z1) for x, y in pts]
    faces = [tuple(range(n - 1, -1, -1)), tuple(range(n, 2 * n))]
    for k in range(n):
        nxt = (k + 1) % n
        faces.append((k, nxt, n + nxt, n + k))
    return verts, faces


def _build_structure(st: dict, mode: str, material) -> list:
    """Free-standing structure (Stair_###, PipeRack_###, Pipe_###): one Empty, one object per part."""
    sid = st["id"]
    pieces = _part_pieces(st.get("parts") or [], sid)
    meta = {
        "id": sid, "origin": st["origin"], "epsg": st.get("epsg", ""), "radius": 0.0,
        "wall_height": 0.0, "total_height": 0.0, "roof_type": st.get("kind", ""), "rms_m": 0.0,
    }
    objects = _finish(pieces, meta, mode, material, sid, st["anchor_local"])
    for obj in objects:
        obj["structure_id"] = sid
        obj["structure_kind"] = st.get("kind", "")
        obj["structure_parent"] = st.get("parent", "")
    return objects


def _frame(p0, p1):
    """Unit axis along p0->p1 plus two perpendicular unit vectors."""
    ax = [p1[i] - p0[i] for i in range(3)]
    length = math.sqrt(sum(c * c for c in ax)) or 1.0
    ax = [c / length for c in ax]
    ref = (0.0, 0.0, 1.0) if abs(ax[2]) < 0.9 else (1.0, 0.0, 0.0)
    e1 = [ax[1] * ref[2] - ax[2] * ref[1], ax[2] * ref[0] - ax[0] * ref[2], ax[0] * ref[1] - ax[1] * ref[0]]
    n1 = math.sqrt(sum(c * c for c in e1)) or 1.0
    e1 = [c / n1 for c in e1]
    e2 = [ax[1] * e1[2] - ax[2] * e1[1], ax[2] * e1[0] - ax[0] * e1[2], ax[0] * e1[1] - ax[1] * e1[0]]
    return ax, e1, e2


def _rod(p0, p1, radius, segs):
    """Capped cylinder between two points, outward normals."""
    _ax, e1, e2 = _frame(p0, p1)
    verts = []
    for p in (p0, p1):
        for k in range(segs):
            a = 2 * math.pi * k / segs
            c, s_ = math.cos(a) * radius, math.sin(a) * radius
            verts.append(tuple(p[i] + c * e1[i] + s_ * e2[i] for i in range(3)))
    faces = []
    for k in range(segs):
        nxt = (k + 1) % segs
        faces.append((k, nxt, segs + nxt, segs + k))
    faces.append(tuple(range(segs - 1, -1, -1)))
    faces.append(tuple(range(segs, 2 * segs)))
    return verts, faces


def _box8(corners):
    """corners: bottom ring (4, counter-clockwise from above) then top ring."""
    faces = [
        (3, 2, 1, 0), (4, 5, 6, 7),
        (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
    ]
    return list(corners), faces


def _beam(p0, p1, width, depth):
    """Deck plate: top surface on the p0->p1 line, ``depth`` thick, level across."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    run = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / run * width / 2.0, dx / run * width / 2.0
    corners = []
    for dz in (-depth, 0.0):
        corners.extend([
            (p0[0] - nx, p0[1] - ny, p0[2] + dz),
            (p1[0] - nx, p1[1] - ny, p1[2] + dz),
            (p1[0] + nx, p1[1] + ny, p1[2] + dz),
            (p0[0] + nx, p0[1] + ny, p0[2] + dz),
        ])
    return _box8(corners)


def _vbox(x, y, z0, z1, length, width, axis_deg):
    a = math.radians(axis_deg)
    ux, uy = math.cos(a) * length / 2.0, math.sin(a) * length / 2.0
    vx, vy = -math.sin(a) * width / 2.0, math.cos(a) * width / 2.0
    ring = [(x - ux - vx, y - uy - vy), (x + ux - vx, y + uy - vy), (x + ux + vx, y + uy + vy), (x - ux + vx, y - uy + vy)]
    corners = [(px, py, z0) for px, py in ring] + [(px, py, z1) for px, py in ring]
    return _box8(corners)


def _place(verts, origin, mode: str):
    if mode != "absolute":
        return verts
    ox, oy, oz = origin
    return [(x + ox, y + oy, z + oz) for x, y, z in verts]


_FLAT_PIECES = ("_foundation", "_handrail", "_wind_girder_", "_base_ring", "_top_rim", "_stair", "_deck", "_support_", "_brace", "_rail",
                "_post_", "_pedestal_", "_platform", "_roof_walkway_", "_column_", "_beam_", "_crossbeam_", "_longbeam_",
                "_flight_", "_landing_", "_top_deck", "_bridge_")


def _object(name, verts, faces, material):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    # Thin rings have 90-degree edges; smooth shading averages the normals
    # across them and renders as dark speckles.
    smooth = not any(tag in name for tag in _FLAT_PIECES)
    for poly in mesh.polygons:
        poly.use_smooth = smooth
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(material)
    return obj


def _stamp(obj, tank: dict, mode: str) -> None:
    origin = tank["origin"]
    obj["epsg"] = str(tank.get("epsg", ""))
    obj["srs_origin_x"] = f"{float(origin[0]):.6f}"
    obj["srs_origin_y"] = f"{float(origin[1]):.6f}"
    obj["srs_origin_z"] = f"{float(origin[2]):.6f}"
    obj["coordinate_mode"] = mode
    obj["tank_id"] = tank["id"]
    obj["radius_m"] = f"{float(tank['radius']):.6f}"
    obj["wall_height_m"] = f"{float(tank['wall_height']):.6f}"
    obj["total_height_m"] = f"{float(tank['total_height']):.6f}"
    obj["roof_type"] = tank.get("roof_type", "")
    obj["rms_m"] = f"{float(tank.get('rms_m') or 0):.6f}"


def _export(path: Path, objects) -> None:
    for obj in bpy.context.scene.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=True,
        use_custom_props=True,
        add_leaf_bones=False,
        bake_anim=False,
        apply_unit_scale=True,
        apply_scale_options="FBX_SCALE_NONE",
        global_scale=1.0,
        axis_forward="Y",
        axis_up="Z",
        use_space_transform=True,
        bake_space_transform=False,
        mesh_smooth_type="FACE",
        path_mode="STRIP",
        object_types={"MESH", "EMPTY"},
    )


def _sample_vertex(objects) -> list[float]:
    """World position of the first vertex of the tank body (parent offset applied)."""
    body = next((obj for obj in objects if obj.name.endswith("_body")), None)
    if body is None:
        body = next(obj for obj in objects if obj.type == "MESH")
    vertex = body.data.vertices[0].co
    loc = body.parent.location if body.parent is not None else (0.0, 0.0, 0.0)
    return [float(vertex.x + loc[0]), float(vertex.y + loc[1]), float(vertex.z + loc[2])]


def _reimport_sample(path: Path, tank_id: str):
    before = {obj.name for obj in bpy.data.objects}
    bpy.ops.import_scene.fbx(filepath=str(path))
    new = [obj for obj in bpy.data.objects if obj.name not in before]
    bpy.context.view_layer.update()
    imported = [obj for obj in new if obj.type == "MESH"]
    names = sorted(obj.name for obj in imported)
    sample = None
    props = {}
    for obj in imported:
        if "_body" in obj.name:
            world = obj.matrix_world @ obj.data.vertices[0].co
            sample = [float(world.x), float(world.y), float(world.z)]
            props = {key: obj[key] for key in obj.keys() if key != "_RNA_UI"}
            props["parent"] = obj.parent.name if obj.parent is not None else ""
            break
    for obj in new:
        bpy.data.objects.remove(obj, do_unlink=True)
    return sample, names, props


def _cylinder(cx, cy, z0, z1, radius, segs, caps=False):
    verts = []
    for z in (z0, z1):
        for k in range(segs):
            a = 2 * math.pi * k / segs
            verts.append((cx + radius * math.cos(a), cy + radius * math.sin(a), z))
    faces = []
    for k in range(segs):
        nxt = (k + 1) % segs
        faces.append((k, nxt, segs + nxt, segs + k))  # outward normals
    if caps:
        faces.append(tuple(range(segs - 1, -1, -1)))
        faces.append(tuple(range(segs, 2 * segs)))
    return verts, faces


def _roof_profile(z1, radius, roof, rise, rings=12):
    """(rho, z) points of the roof from the wall top (excluded) to the centre (included)."""
    if roof == "cone" and rise > 0:
        return [(0.0, z1 + rise)]
    if roof == "dome" and rise > 0:
        rs = (radius * radius + rise * rise) / (2.0 * rise)
        pts = []
        for i in range(1, rings):
            rho = radius * (1.0 - i / rings)
            pts.append((rho, z1 + math.sqrt(max(rs * rs - rho * rho, 0.0)) - (rs - rise)))
        return pts + [(0.0, z1 + rise)]
    return [(0.0, z1)]


def _revolve(cx, cy, profile, segs):
    """Closed surface of revolution. ``profile`` runs from the bottom centre (rho=0) round the
    outside to the top centre (rho=0); points with rho=0 become single pole vertices, so the
    result is a watertight, consistently outward-facing mesh."""
    verts, rings = [], []
    for rho, z in profile:
        if rho <= 1e-9:
            rings.append([len(verts)] * segs)
            verts.append((cx, cy, z))
        else:
            ring = []
            for k in range(segs):
                a = 2 * math.pi * k / segs
                ring.append(len(verts))
                verts.append((cx + rho * math.cos(a), cy + rho * math.sin(a), z))
            rings.append(ring)
    faces = []
    for lo, hi in zip(rings[:-1], rings[1:]):
        for k in range(segs):
            nxt = (k + 1) % segs
            quad = [lo[k], lo[nxt], hi[nxt], hi[k]]
            face = []
            for v in quad:
                if v not in face:
                    face.append(v)
            if len(face) >= 3:
                faces.append(tuple(face))
    return verts, faces


def _closed_body(cx, cy, z0, z1, radius, roof, rise, segs):
    profile = [(0.0, z0), (radius, z0), (radius, z1)] + _roof_profile(z1, radius, roof, rise)
    return _revolve(cx, cy, profile, segs)


def _disk(cx, cy, z, radius, segs):
    verts = [(cx, cy, z)]
    for k in range(segs):
        a = 2 * math.pi * k / segs
        verts.append((cx + radius * math.cos(a), cy + radius * math.sin(a), z))
    faces = [(0, k, k + 1) for k in range(1, segs)]
    faces.append((0, segs, 1))
    return verts, faces


def _annulus(cx, cy, z, r0, r1, segs):
    verts = []
    for radius in (r0, r1):
        for k in range(segs):
            a = 2 * math.pi * k / segs
            verts.append((cx + radius * math.cos(a), cy + radius * math.sin(a), z))
    faces = []
    for k in range(segs):
        nxt = (k + 1) % segs
        faces.append((k, segs + k, segs + nxt, nxt))
    return verts, faces


def _band(cx, cy, z0, height, r_in, r_out, segs):
    verts = []
    for z in (z0, z0 + height):
        for radius in (r_in, r_out):
            for k in range(segs):
                a = 2 * math.pi * k / segs
                verts.append((cx + radius * math.cos(a), cy + radius * math.sin(a), z))
    # Layout per ring of z: inner[0:segs], outer[segs:2segs], then the upper copy.
    # All faces wound counter-clockwise seen from outside, so normals point
    # out of the band (bottom down, top up, outer wall out, inner wall in).
    faces = []
    top = 2 * segs
    for k in range(segs):
        nxt = (k + 1) % segs
        faces.append((k, nxt, segs + nxt, segs + k))  # bottom
        faces.append((top + k, top + segs + k, top + segs + nxt, top + nxt))  # top
        faces.append((segs + k, segs + nxt, top + segs + nxt, top + segs + k))  # outer wall
        faces.append((k, top + k, top + nxt, nxt))  # inner wall
    return verts, faces


def _cone(cx, cy, z_rim, radius, rise, segs):
    verts = [(cx, cy, z_rim + rise)]
    for k in range(segs):
        a = 2 * math.pi * k / segs
        verts.append((cx + radius * math.cos(a), cy + radius * math.sin(a), z_rim))
    faces = [(0, k, k + 1) for k in range(1, segs)]
    faces.append((0, segs, 1))
    return verts, faces


def _uv_sphere(cx, cy, cz, radius, segs, rings):
    verts = [(cx, cy, cz - radius)]
    for i in range(1, rings):
        phi = -math.pi / 2 + math.pi * i / rings
        rho = radius * math.cos(phi)
        z = cz + radius * math.sin(phi)
        for k in range(segs):
            a = 2 * math.pi * k / segs
            verts.append((cx + rho * math.cos(a), cy + rho * math.sin(a), z))
    verts.append((cx, cy, cz + radius))
    faces = []
    for k in range(segs):
        faces.append((0, 1 + (k + 1) % segs, 1 + k))
    for i in range(rings - 2):
        base = 1 + i * segs
        for k in range(segs):
            nxt = (k + 1) % segs
            faces.append((base + k, base + nxt, base + segs + nxt, base + segs + k))
    top = len(verts) - 1
    last = 1 + (rings - 2) * segs
    for k in range(segs):
        faces.append((last + k, last + (k + 1) % segs, top))
    return verts, faces


def _dome(cx, cy, z_rim, radius, rise, segs, rings=12):
    """Spherical-cap roof (拱顶) with the measured rise."""
    verts = []
    rs = (radius * radius + rise * rise) / (2.0 * rise)
    for i in range(rings):
        frac = 1.0 - (i + 1) / rings
        rho = radius * frac
        z = z_rim + math.sqrt(max(rs * rs - rho * rho, 0.0)) - (rs - rise)
        if i == rings - 1:
            verts.append((cx, cy, z_rim + rise))
        else:
            for k in range(segs):
                a = 2 * math.pi * k / segs
                verts.append((cx + rho * math.cos(a), cy + rho * math.sin(a), z))
    # Rim ring is the wall top, duplicated here at theta of the first band... 
    # Include an explicit rim ring at z_rim so the dome meets the wall.
    rim = []
    for k in range(segs):
        a = 2 * math.pi * k / segs
        rim.append((cx + radius * math.cos(a), cy + radius * math.sin(a), z_rim))
    verts = rim + verts
    faces = []
    for k in range(segs):
        nxt = (k + 1) % segs
        faces.append((k, segs + k, segs + nxt, nxt))
    full_rings = rings - 1
    for i in range(full_rings - 1):
        base = segs + i * segs
        for k in range(segs):
            nxt = (k + 1) % segs
            faces.append((base + k, base + segs + k, base + segs + nxt, base + nxt))
    apex = len(verts) - 1
    last = segs + (full_rings - 1) * segs
    for k in range(segs):
        faces.append((last + k, apex, last + (k + 1) % segs))
    faces = [tuple(reversed(face)) for face in faces]  # normals up / outward
    return verts, faces


def _stair(cx, cy, z0, height, r_in, r_out, turns, steps):
    steps = max(8, int(steps))
    verts = []
    faces = []

    def polar(radius, ang, z):
        return (cx + radius * math.cos(ang), cy + radius * math.sin(ang), z)

    for s in range(steps):
        a0 = 2 * math.pi * turns * s / steps
        a1 = 2 * math.pi * turns * (s + 1) / steps
        z_lo = z0 + height * s / steps
        z_hi = z0 + height * (s + 1) / steps
        base = len(verts)
        verts.extend(
            [
                polar(r_in, a0, z_hi),
                polar(r_out, a0, z_hi),
                polar(r_out, a1, z_hi),
                polar(r_in, a1, z_hi),
                polar(r_in, a0, z_lo),
                polar(r_out, a0, z_lo),
            ]
        )
        faces.append((base, base + 1, base + 2, base + 3))
        faces.append((base + 4, base + 5, base + 1, base))
    return verts, faces


if __name__ == "__main__":
    main()
