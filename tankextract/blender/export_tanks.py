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
    if merge and all_objects:
        # Rebuild so per-tank exports (which reimport) cannot disturb the merge.
        _reset_scene()
        material = _white_material()
        merged = []
        for tank in spec["tanks"]:
            merged.extend(_build_tank(tank, segments, mode, material))
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
        pieces.append((f"{tank['id']}_Sphere", *_uv_sphere(cx, cy, zc, rs, segments, max(12, segments // 2))))
        leg_r = float(sphere.get("leg_radius") or 0.3)
        leg_rho = float(sphere.get("leg_rho") or rs * 0.97)
        bottom = float(sphere.get("leg_bottom", z0))
        for index, azimuth in enumerate(sphere.get("legs") or [], start=1):
            a = math.radians(float(azimuth))
            lx, ly = cx + leg_rho * math.cos(a), cy + leg_rho * math.sin(a)
            pieces.append((f"{tank['id']}_Leg_{index:02d}", *_cylinder(lx, ly, bottom, zc, leg_r, 12, caps=True)))
        return _finish(pieces, tank, mode, material)
    pieces.append((f"{tank['id']}_Wall", *_cylinder(cx, cy, z0, z1, radius, segments)))
    handrail = tank.get("handrail_height")
    if handrail:
        # Lifted 2 cm off the wall top so its bottom does not z-fight with
        # the roof edge and rim annulus.
        pieces.append(
            (f"{tank['id']}_Handrail", *_band(cx, cy, z1 + 0.02, float(handrail) - 0.02, radius - 0.06, radius, segments))
        )
    roof = tank.get("roof_type", "flat")
    rise = float(tank.get("roof_rise") or 0.0)
    if roof == "cone" and rise > 0:
        pieces.append((f"{tank['id']}_Roof", *_cone(cx, cy, z1, radius, rise, segments)))
    elif roof == "dome" and rise > 0:
        pieces.append((f"{tank['id']}_Roof", *_dome(cx, cy, z1, radius, rise, segments)))
    else:
        pieces.append((f"{tank['id']}_Roof", *_disk(cx, cy, z1, radius * 0.998, segments)))
    rim = tank.get("top_rim_width")
    if rim:
        pieces.append(
            (f"{tank['id']}_Rim", *_annulus(cx, cy, z1, max(radius - 0.05, 0.01), radius + float(rim) * 0.5, segments))
        )
    for index, girder in enumerate(tank.get("wind_girders") or [], start=1):
        gz = float(girder["z"])
        prot = float(girder["protrusion"])
        height = float(girder.get("height") or 0.15)
        pieces.append(
            (
                f"{tank['id']}_Girder_{index:02d}",
                *_band(cx, cy, gz - height / 2, height, radius, radius + prot, segments),
            )
        )
    ring = tank.get("base_ring")
    if ring:
        prot = float(ring["protrusion"])
        height = float(ring.get("height") or 0.22)
        pieces.append((f"{tank['id']}_BaseRing", *_band(cx, cy, z0, height, radius, radius + prot, segments)))
    stair = tank.get("spiral_stair")
    if stair:
        outer = float(stair["outer_radius"])
        width = float(stair["width"])
        turns = float(stair["turns"])
        pieces.append(
            (
                f"{tank['id']}_Stair",
                *_stair(cx, cy, z0, float(tank["wall_height"]), max(outer - width, radius + 0.05), outer, turns, segments // 2),
            )
        )
    return _finish(pieces, tank, mode, material)


def _finish(pieces, tank: dict, mode: str, material) -> list:
    origin = tank["origin"]
    objects = []
    for name, verts, faces in pieces:
        placed = _place(verts, origin, mode)
        obj = _object(name, placed, faces, material)
        _stamp(obj, tank, mode)
        objects.append(obj)
    return objects


def _place(verts, origin, mode: str):
    if mode != "absolute":
        return verts
    ox, oy, oz = origin
    return [(x + ox, y + oy, z + oz) for x, y, z in verts]


_FLAT_PIECES = ("_Handrail", "_Girder_", "_BaseRing", "_Rim", "_Stair")


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
        object_types={"MESH"},
    )


def _sample_vertex(objects) -> list[float]:
    wall = next((obj for obj in objects if obj.name.endswith(("_Wall", "_Sphere"))), objects[0])
    vertex = wall.data.vertices[0].co
    return [float(vertex.x), float(vertex.y), float(vertex.z)]


def _reimport_sample(path: Path, tank_id: str):
    before = {obj.name for obj in bpy.data.objects}
    bpy.ops.import_scene.fbx(filepath=str(path))
    imported = [obj for obj in bpy.data.objects if obj.name not in before and obj.type == "MESH"]
    names = sorted(obj.name for obj in imported)
    sample = None
    props = {}
    for obj in imported:
        if "Wall" in obj.name or "Sphere" in obj.name:
            vertex = obj.data.vertices[0].co
            sample = [float(vertex.x), float(vertex.y), float(vertex.z)]
            props = {key: obj[key] for key in obj.keys() if key != "_RNA_UI"}
            break
    for obj in imported:
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
