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
    material_name = str(spec.get("material_name") or "Standard")
    material = _white_material(material_name)
    mode = spec.get("coordinate_mode", "absolute")
    segments = int(spec.get("segments", 96))
    limits = {
        "precision_m": spec.get("precision_m"),
        "max_faces": spec.get("max_faces"),
        "quality": spec.get("quality") or "full",
        "model_origin": spec.get("model_origin"),
        "material_name": material_name,
    }
    report = {
        "coordinate_mode": mode,
        "model_origin": spec.get("model_origin"),
        "vertical_datum": spec.get("vertical_datum"),
        "precision_m": spec.get("precision_m"),
        "precision_applied": (spec.get("quality") or "full") == "budget",
        "precision_scope": "shell_chord_only",
        "precision_note": (
            "When quality is budget, precision_m is the chord-error target of the tank wall, "
            "dome and sphere only. Rails, stairs, legs, girders and openings do not use it. "
            "quality full ignores it and keeps --segments."
        ),
        "max_faces": spec.get("max_faces"),
        "quality": spec.get("quality") or "full",
        "max_faces_scope": "one exported model (parent empty plus its meshes)",
        "max_faces_note": (
            "2000 is the polygon budget of one model, not of each tiny part and not of the whole site. "
            "Shells are reduced first. A part that is itself over the budget is kept, with its polygon "
            "and triangle counts and the reason."
        ),
        "models": [],
        "face_counts": [],
        "material": {
            "name": material_name,
            "blender_shader": "Principled BSDF",
            "fbx_note": (
                "Blender 4.2's FBX exporter writes this as a Phong/Lambert surface with the given name. "
                "That is not a verified 3ds Max Standard material class."
            ),
        },
        "pipes_note": (
            "Process piping, pipe racks and the orange sphere-hoop / dropper pipes in the review "
            "reference scene are not generated. Structural rails, stair openings and measured "
            "shell rings are. --pipes still only enables measured wall pipes."
        ),
        "face_budget_exceeded": [],
        "files": [],
    }
    all_objects = []
    for tank in spec["tanks"]:
        objects = _build_tank(tank, segments, mode, material, limits, report)
        all_objects.extend(objects)
        path = output / f"{_group_name(tank)}.fbx"
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
        material = _white_material(material_name)
        objects = []
        for conn in links:
            objects.extend(_build_connector(conn, mode, material, limits, report))
        path = output / "connectors.fbx"
        _export(path, objects)
        report["connectors_file"] = str(path)
        report["connector_objects"] = [obj.name for obj in objects]
    extras = spec.get("structures") or []
    if extras:
        _reset_scene()
        material = _white_material(material_name)
        objects = []
        for st in extras:
            objects.extend(_build_structure(st, mode, material, limits, report))
        path = output / "structures.fbx"
        _export(path, objects)
        report["structures_file"] = str(path)
        report["structure_objects"] = [obj.name for obj in objects]
    if merge and all_objects:
        # Rebuild so per-tank exports (which reimport) cannot disturb the merge.
        _reset_scene()
        material = _white_material(material_name)
        merged = []
        for tank in spec["tanks"]:
            merged.extend(_build_tank(tank, segments, mode, material, limits, report))
        for conn in links:
            merged.extend(_build_connector(conn, mode, material, limits, report))
        for st in extras:
            merged.extend(_build_structure(st, mode, material, limits, report))
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


def _white_material(name="Standard"):
    material = bpy.data.materials.new(_latin(name) or "Standard")
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    if principled is not None:
        principled.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        principled.inputs["Roughness"].default_value = 0.55
    material.diffuse_color = (1.0, 1.0, 1.0, 1.0)
    return material


def _build_tank(tank: dict, segments: int, mode: str, material, limits=None, report=None) -> list:
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
        detail = []
        leg_r = float(sphere.get("leg_radius") or 0.3)
        leg_rho = float(sphere.get("leg_rho") or rs * 0.97)
        bottom = float(sphere.get("leg_bottom", z0))
        for index, azimuth in enumerate(sphere.get("legs") or [], start=1):
            a = math.radians(float(azimuth))
            lx, ly = cx + leg_rho * math.cos(a), cy + leg_rho * math.sin(a)
            leg_sides = 8 if (limits or {}).get("quality") == "budget" else 12
            detail.append((f"leg_{index:02d}", *_cylinder(lx, ly, bottom, zc, leg_r, leg_sides, caps=True)))
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
                    detail.append((f"brace_{index + 1:02d}a", *_rod((x0, y0, lo), (x1, y1, hi), 0.05, 8)))
                    detail.append((f"brace_{index + 1:02d}b", *_rod((x0, y0, hi), (x1, y1, lo), 0.05, 8)))
        detail.extend(_part_pieces(tank.get("extra_parts") or [], tank["id"], limits))
        _append_girders(detail, cx, cy, radius, tank.get("wind_girders") or [], {"quality": (limits or {}).get("quality")})
        plan = _shell_plan(_shell_limits(limits, _piece_polygons(detail)), rs, segments, "sphere")
        pieces = [("body", *_uv_sphere(cx, cy, zc, rs, plan["segments"], plan["rings"]))] + detail
        return _finish(pieces, tank, mode, material, _group_name(tank), (cx, cy, z0), limits, report)
    roof = tank.get("roof_type", "flat")
    rise = float(tank.get("roof_rise") or 0.0)
    # One closed, manifold shell: bottom plate + wall + roof share their rim vertices.
    # Detail first, so the shell can use whatever polygon budget the model has left.
    # Tube sides do not follow precision_m.
    detail = []
    tube_plan = {"quality": (limits or {}).get("quality")}
    handrail = tank.get("handrail_height")
    if handrail:
        _append_handrail(detail, cx, cy, z1, float(handrail), radius, tank.get("handrail_gaps") or [], tube_plan)
    _append_girders(detail, cx, cy, radius, tank.get("wind_girders") or [], tube_plan)
    stair = tank.get("spiral_stair")
    if stair:
        outer = float(stair["outer_radius"])
        width = float(stair["width"])
        turns = float(stair["turns"])
        detail.append(
            (
                "stair",
                *_stair(cx, cy, z0, float(tank["wall_height"]), radius + 0.02, max(outer, radius + width), turns, max(8, int(float(tank["wall_height"]) / 0.2))),
            )
        )
    detail.extend(_part_pieces(tank.get("extra_parts") or [], tank["id"], limits))
    plan = _shell_plan(_shell_limits(limits, _piece_polygons(detail)), radius, segments, roof)
    pieces = [("body", *_closed_body(cx, cy, z0, z1, radius, roof, rise, plan["segments"], plan["rings"]))]
    rim = tank.get("top_rim_width")
    if rim:
        pieces.append(
            ("top_rim", *_annulus(cx, cy, z1, max(radius - 0.05, 0.01), radius + float(rim) * 0.5, plan["segments"]))
        )
    ring = tank.get("base_ring")
    if ring:
        prot = float(ring["protrusion"])
        height = float(ring.get("height") or 0.22)
        pieces.append(("base_ring", *_band(cx, cy, z0, height, radius, radius + prot, plan["segments"])))
    pieces.extend(detail)
    return _finish(pieces, tank, mode, material, _group_name(tank), (cx, cy, z0), limits, report)


def _finish(pieces, tank: dict, mode: str, material, group: str, anchor, limits=None, report=None) -> list:
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
    model_origin = (limits or {}).get("model_origin")
    ax, ay, az = _place([tuple(anchor)], origin, mode, model_origin)[0]
    empty = bpy.data.objects.new(group, None)
    empty.empty_display_type = "PLAIN_AXES"
    empty.location = (ax, ay, az)
    bpy.context.scene.collection.objects.link(empty)
    _stamp(empty, tank, mode, limits)
    empty["part"] = "group"
    objects = [empty]
    for name, (verts, faces) in merged.items():
        placed = _place(verts, origin, mode, model_origin)
        rel = [(x - ax, y - ay, z - az) for x, y, z in placed]
        obj = _object(f"{group}_{_latin(name)}", rel, faces, material)
        _note_faces(obj, faces, (limits or {}).get("max_faces"), report)
        obj.parent = empty
        _stamp(obj, tank, mode, limits)
        obj["part"] = name
        objects.append(obj)
    _note_model(group, merged, limits, report)
    return objects


def _piece_polygons(pieces) -> int:
    total = 0
    for _name, _verts, faces in pieces:
        total += sum(1 for face in faces if len(face) >= 3)
    return total


def _shell_limits(limits, detail_polygons: int):
    """Leave the model's polygon budget for the shell after detail parts are counted."""
    if not limits or (limits.get("quality") or "full") != "budget":
        return limits
    cap = limits.get("max_faces")
    if not cap:
        return limits
    narrowed = dict(limits)
    narrowed["max_faces"] = max(1, int(cap) - int(detail_polygons))
    return narrowed


def _note_model(group, merged, limits, report) -> None:
    if report is None:
        return
    polygons = 0
    triangles = 0
    for _name, (_verts, faces) in merged.items():
        for face in faces:
            size = len(face)
            if size < 3:
                continue
            polygons += 1
            triangles += size - 2
    limit = (limits or {}).get("max_faces")
    exceeded = bool(limit) and polygons > int(limit)
    reason = ""
    if exceeded:
        reason = (
            f"model has {polygons} polygons / {triangles} triangles, over {int(limit)}. "
            "Shell segments are already at the precision chord or the 16-side floor. "
            "Rails, stairs and legs were kept."
        )
    report.setdefault("models", []).append({
        "name": group,
        "scope": "one exported model",
        "polygons": polygons,
        "triangles": triangles,
        "limit": int(limit) if limit else None,
        "exceeded": exceeded,
        "kept": True,
        "reason": reason,
    })


def _build_connector(conn: dict, mode: str, material, limits=None, report=None) -> list:
    """Walkway deck (box), handrails (top + mid rail, posts) and supports; or a pipe."""
    cid = _group_name(conn)
    source_id = conn["id"]
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
        "id": source_id, "export_name": cid, "origin": conn["origin"], "epsg": conn.get("epsg", ""), "radius": 0.0,
        "wall_height": 0.0, "total_height": 0.0, "roof_type": conn.get("kind", ""), "rms_m": 0.0,
    }
    objects = _finish(pieces, meta, mode, material, cid, p0, limits, report)
    for obj in objects:
        obj["connector_id"] = source_id
        obj["tank_a"] = conn.get("tank_a", "")
        obj["tank_b"] = conn.get("tank_b", "")
    return objects


def _part_pieces(parts, group: str, limits=None) -> list:
    """Primitive parts from the spec (rod / beam / box / prism) -> (name, verts, faces).

    Part names carry the group prefix (``Tank_015_pedestal_01``); the piece name is the
    remainder so ``_finish`` names the object ``{group}_{rest}``."""
    out = []
    for part in parts:
        name = part["name"]
        rest = name[len(group) + 1:] if name.startswith(group + "_") else name
        kind = part["type"]
        if _mentions_door(name, kind) and "door" not in rest.lower():
            rest = f"{rest}_door"
        rest = _latin(rest)
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
            segs = int(part.get("segments", 96))
            if (limits or {}).get("quality") == "budget":
                cap = _shell_plan(limits, float(part.get("radius") or 1.0), segs, "flat")["segments"]
                segs = max(12, min(segs, cap))
            out.append((rest, *_cylinder(cx, cy, part["z0"], part["z1"], part["radius"], segs, caps=True)))
        elif kind == "tube":
            out.append((rest, *_tube_mesh(part["points"], part["radius"], int(part.get("segments", 6)), bool(part.get("closed")))))
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


def _build_structure(st: dict, mode: str, material, limits=None, report=None) -> list:
    """Free-standing structure (Stair_###, PipeRack_###, Pipe_###): one Empty, one object per part."""
    source_id = st["id"]
    sid = _group_name(st)
    pieces = _part_pieces(st.get("parts") or [], source_id)
    meta = {
        "id": source_id, "export_name": sid, "origin": st["origin"], "epsg": st.get("epsg", ""), "radius": 0.0,
        "wall_height": 0.0, "total_height": 0.0, "roof_type": st.get("kind", ""), "rms_m": 0.0,
    }
    objects = _finish(pieces, meta, mode, material, sid, st["anchor_local"], limits, report)
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


def _place(verts, origin, mode: str, model_origin=None):
    if mode == "absolute":
        ox, oy, oz = origin
        return [(x + ox, y + oy, z + oz) for x, y, z in verts]
    if not model_origin:
        return verts
    dx = float(origin[0]) - float(model_origin[0])
    dy = float(origin[1]) - float(model_origin[1])
    dz = float(origin[2]) - float(model_origin[2])
    return [(x + dx, y + dy, z + dz) for x, y, z in verts]


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


def _stamp(obj, tank: dict, mode: str, limits=None) -> None:
    origin = tank["origin"]
    obj["epsg"] = str(tank.get("epsg", ""))
    obj["srs_origin_x"] = f"{float(origin[0]):.6f}"
    obj["srs_origin_y"] = f"{float(origin[1]):.6f}"
    obj["srs_origin_z"] = f"{float(origin[2]):.6f}"
    obj["coordinate_mode"] = mode
    obj["tank_id"] = tank["id"]
    obj["export_name"] = str(tank.get("export_name") or tank["id"])
    agreed = (limits or {}).get("model_origin")
    if agreed:
        obj["model_origin_x"] = f"{float(agreed[0]):.6f}"
        obj["model_origin_y"] = f"{float(agreed[1]):.6f}"
        obj["model_origin_z"] = f"{float(agreed[2]):.6f}"
    obj["vertical_datum"] = "" if tank.get("vertical_datum") in (None, "") else str(tank["vertical_datum"])
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


def _latin(text: str) -> str:
    raw = str(text).replace("门", "door")
    chars = []
    for ch in raw:
        if ch.isascii() and (ch.isalnum() or ch in "_-"):
            chars.append(ch)
        else:
            chars.append("_")
    token = "".join(chars).strip("_")
    while "__" in token:
        token = token.replace("__", "_")
    return token or "part"


def _mentions_door(name: str, kind: str = "") -> bool:
    blob = f"{name} {kind}"
    return "门" in blob or "door" in blob.lower()


def _group_name(record: dict) -> str:
    return _latin(record.get("export_name") or record.get("id") or "part")


def _shell_plan(limits, radius, explicit, roof):
    """Keep in step with tankextract.deliverable.plan_shell. Blender cannot import the package."""
    quality = (limits or {}).get("quality") or "full"
    explicit = max(3, int(explicit))
    if quality != "budget":
        rings = max(12, explicit // 2) if roof == "sphere" else (12 if roof == "dome" else 1)
        return {"segments": explicit, "rings": rings, "quality": "full", "reason": "quality full keeps the requested segment count"}
    raw = (limits or {}).get("precision_m")
    chord = 0.50 if raw is None or float(raw) <= 0 else float(raw)
    count = _chord_sides(max(radius, 0.1), chord, minimum=16, maximum=explicit)
    if roof == "sphere":
        rings = _chord_sides(max(radius, 0.1), chord, minimum=8, maximum=max(8, explicit // 2))
        steps = rings
    elif roof == "dome":
        rings = _chord_sides(max(radius, 0.1), chord, minimum=4, maximum=8)
        steps = 2 + rings
    else:
        rings = 1
        steps = 3
    limit = (limits or {}).get("max_faces")
    reason = f"shell chord target {chord:.2f} m from precision_m; rails, stairs, legs and openings ignore it"
    while limit and count > 16 and count * steps > int(limit):
        count -= 1
        reason = f"shell segments reduced to fit {int(limit)} polygons; 16-side floor keeps a cylinder"
    if roof == "sphere":
        while limit and rings > 8 and count * rings > int(limit):
            rings -= 1
            steps = rings
            reason = f"sphere rings reduced to fit {int(limit)} polygons; legs and rails stay"
    estimate = count * (rings if roof == "sphere" else steps)
    if limit and estimate > int(limit):
        reason = f"shell still estimates {estimate} polygons above {int(limit)} at the floor; the shell was kept"
    return {"segments": count, "rings": rings, "quality": "budget", "reason": reason, "chord_target_m": chord}


def _chord_sides(radius, tolerance, minimum, maximum):
    ratio = 1.0 - min(tolerance / radius, 0.999)
    angle = math.acos(max(-1.0, min(1.0, ratio)))
    count = maximum if angle <= 1e-6 else math.ceil(math.pi / angle)
    return int(max(minimum, min(maximum, count)))


def _role(name: str) -> str:
    text = name.lower()
    if any(key in text for key in ("handrail", "wind_girder", "rail", "brace")):
        return "tube"
    if "leg" in text:
        return "leg"
    if any(key in text for key in ("stair", "tread", "flight", "landing")):
        return "stair"
    if any(key in text for key in ("body", "top_rim", "base_ring", "foundation")):
        return "shell"
    return "other"


def _note_faces(obj, faces, limit, report) -> None:
    polygons = 0
    triangles = 0
    for face in faces:
        size = len(face)
        if size < 3:
            continue
        polygons += 1
        triangles += size - 2
    role = _role(obj.name)
    obj["polygon_count"] = polygons
    obj["triangle_count"] = triangles
    obj["mesh_role"] = role
    exceeded = bool(limit) and polygons > int(limit)
    if exceeded:
        obj["face_budget_exceeded"] = 1
    reason = ""
    if exceeded:
        reason = {
            "shell": "shell is already at its roundness floor; cutting further would facet the wall, so the shell was kept",
            "tube": "pipe section stays 6-sided so it remains a tube; the rail or girder was kept",
            "leg": "each leg is kept; the section stays round",
            "stair": "treads and rails are kept; deleting steps would remove the stair",
            "other": "part kept; the budget does not delete objects",
        }[role]
    if report is not None:
        row = {
            "object": obj.name, "role": role, "polygons": polygons, "triangles": triangles,
            "limit": int(limit) if limit else None, "exceeded": exceeded, "kept": True, "reason": reason,
        }
        report.setdefault("face_counts", []).append(row)
        if exceeded:
            report.setdefault("face_budget_exceeded", []).append(row)


def _append_handrail(pieces, cx, cy, z_deck, height, radius, gaps, plan=None) -> None:
    """Pipe ring (top, mid, posts) around a measured roof rail. No solid wall."""
    pipe_r = 0.025
    ring_r = max(radius - 0.08, 0.2)
    openings = []
    for gap in gaps or []:
        width = max(float(gap.get("width_m") or 0.8), 0.6)
        half = math.degrees(width / (2.0 * max(ring_r, 0.2)))
        openings.append((float(gap["azimuth_deg"]) % 360.0, half))
    circ = 12 if plan and plan.get("quality") == "budget" else 16
    pieces.append(("handrail", *_ring_pipe(cx, cy, z_deck + height, ring_r, pipe_r, openings, circ=circ)))
    pieces.append(("handrail", *_ring_pipe(cx, cy, z_deck + height * 0.5, ring_r, pipe_r * 0.8, openings, circ=circ)))
    circ = 2.0 * math.pi * ring_r
    count = max(4, int(circ / 1.5))
    for index in range(count):
        deg = 360.0 * index / count
        if any(_angle_delta(deg, center) <= half for center, half in openings):
            continue
        ang = math.radians(deg)
        x = cx + ring_r * math.cos(ang)
        y = cy + ring_r * math.sin(ang)
        pieces.append(("handrail", *_rod((x, y, z_deck), (x, y, z_deck + height), pipe_r, 6)))


def _append_girders(pieces, cx, cy, radius, girders, plan=None) -> None:
    """Shell rings as pipes only when the cross-section fitted a circle.

    Radial stick-out is not a diameter: a plate and a pipe can share it.
    """
    circ = 12 if plan and plan.get("quality") == "budget" else 16
    for index, girder in enumerate(girders or [], start=1):
        diameter = girder.get("diameter")
        if not diameter:
            continue
        pipe_r = max(float(diameter) / 2.0, 0.04)
        center_rho = girder.get("center_rho")
        ring_r = radius + (float(center_rho) if center_rho is not None else pipe_r)
        pieces.append((
            f"wind_girder_{index:02d}",
            *_ring_pipe(cx, cy, float(girder["z"]), ring_r, pipe_r, [], circ=circ),
        ))


def _angle_delta(a, b) -> float:
    return abs(((a - b + 180.0) % 360.0) - 180.0)


def _ring_pipe(cx, cy, z, ring_r, pipe_r, openings, circ=16, radial=6):
    """Torus-like pipe. ``openings`` are (center_deg, half_width_deg) gaps.

    The gap is the measured angle, not one coarse station. Edge points are
    inserted on the opening boundary and only stations that fall inside it
    are left out.
    """
    def at(deg):
        ang = math.radians(deg)
        return (cx + ring_r * math.cos(ang), cy + ring_r * math.sin(ang), z)

    def inside(deg):
        return any(_angle_delta(deg, center) < half - 0.05 for center, half in openings)

    degrees = [360.0 * index / circ for index in range(circ) if not inside(360.0 * index / circ)]
    for center, half in openings or []:
        degrees.append((center - half) % 360.0)
        degrees.append((center + half) % 360.0)
    degrees = sorted({round(deg, 4) for deg in degrees})
    if not openings or len(degrees) < 3:
        return _tube_mesh([at(360.0 * index / circ) for index in range(circ)], pipe_r, radial, True)
    pairs = []
    count = len(degrees)
    for index, start in enumerate(degrees):
        end = degrees[(index + 1) % count]
        delta = (end - start) % 360.0
        mid = (start + delta / 2.0) % 360.0
        pairs.append((start, end, inside(mid)))
    if any(gap for _a, _b, gap in pairs):
        cut = next(index for index, item in enumerate(pairs) if item[2])
        pairs = pairs[cut:] + pairs[:cut]
    runs = []
    current = []
    for start, end, gap in pairs:
        if gap:
            if len(current) >= 2:
                runs.append(current)
            current = []
            continue
        if not current:
            current = [at(start)]
        current.append(at(end))
    if len(current) >= 2:
        runs.append(current)
    verts, faces = [], []
    for run in runs:
        v, f = _tube_mesh(run, pipe_r, radial, False)
        base = len(verts)
        verts.extend(v)
        faces.extend(tuple(index + base for index in face) for face in f)
    return verts, faces


def _tube_mesh(points, radius, segs, closed=False):
    pts = [tuple(float(v) for v in p) for p in points]
    if len(pts) < 2:
        return [], []
    rings = []
    verts = []
    previous = None
    samples = pts
    for index, point in enumerate(samples):
        if index == 0:
            direction = _sub(samples[1], samples[0])
        elif index == len(samples) - 1:
            direction = _sub(samples[-1], samples[-2])
        else:
            direction = _sub(samples[index + 1], samples[index - 1])
        _axis, e1, e2, previous = _frame_along(direction, previous)
        ring = []
        for k in range(segs):
            ang = 2 * math.pi * k / segs
            offset = [math.cos(ang) * radius * e1[i] + math.sin(ang) * radius * e2[i] for i in range(3)]
            ring.append(len(verts))
            verts.append(tuple(point[i] + offset[i] for i in range(3)))
        rings.append(ring)
    faces = []
    pairs = list(zip(rings, rings[1:]))
    if closed and len(rings) > 2:
        pairs.append((rings[-1], rings[0]))
    for a, b in pairs:
        for k in range(segs):
            nxt = (k + 1) % segs
            faces.append((a[k], a[nxt], b[nxt], b[k]))
    if not closed:
        faces.append(tuple(reversed(rings[0])))
        faces.append(tuple(rings[-1]))
    return verts, faces


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _frame_along(direction, previous):
    length = math.sqrt(sum(c * c for c in direction)) or 1.0
    axis = [c / length for c in direction]
    if previous is None:
        ref = (0.0, 0.0, 1.0) if abs(axis[2]) < 0.9 else (1.0, 0.0, 0.0)
        e1 = [
            axis[1] * ref[2] - axis[2] * ref[1],
            axis[2] * ref[0] - axis[0] * ref[2],
            axis[0] * ref[1] - axis[1] * ref[0],
        ]
    else:
        e1 = [previous[i] - axis[i] * sum(previous[j] * axis[j] for j in range(3)) for i in range(3)]
    n1 = math.sqrt(sum(c * c for c in e1)) or 1.0
    e1 = [c / n1 for c in e1]
    e2 = [
        axis[1] * e1[2] - axis[2] * e1[1],
        axis[2] * e1[0] - axis[0] * e1[2],
        axis[0] * e1[1] - axis[1] * e1[0],
    ]
    return axis, e1, e2, e1


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


def _closed_body(cx, cy, z0, z1, radius, roof, rise, segs, roof_rings=12):
    profile = [(0.0, z0), (radius, z0), (radius, z1)] + _roof_profile(z1, radius, roof, rise, rings=roof_rings)
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
