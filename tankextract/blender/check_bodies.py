"""blender -b -P check_bodies.py -- tanks_all.fbx report.json

Imports an exported FBX and checks every ``Tank_###_body``: one closed manifold shell
(every edge shared by exactly two faces, no boundary edges, no non-manifold vertices),
outward normals (positive signed volume), and its world-space radius / height / top."""

import json
import sys

import bpy  # noqa: I001  (bpy first: bmesh ships inside it)
import bmesh

argv = sys.argv[sys.argv.index("--") + 1:]
src, out = argv[0], argv[1]
ALL = "--all" in argv[2:]  # every mesh object, not only the tank bodies
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=src)
bpy.context.view_layer.update()
rows = []
for obj in sorted(bpy.data.objects, key=lambda o: o.name):
    if obj.type != "MESH" or not (ALL or obj.name.endswith("_body")):
        continue
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    boundary = sum(1 for e in bm.edges if e.is_boundary)
    nonman_e = sum(1 for e in bm.edges if not e.is_manifold)
    nonman_v = sum(1 for v in bm.verts if not v.is_manifold)
    xs = [v.co.x for v in bm.verts]
    ys = [v.co.y for v in bm.verts]
    zs = [v.co.z for v in bm.verts]
    # connected shells
    seen, shells = set(), 0
    for f in bm.faces:
        if f.index in seen:
            continue
        shells += 1
        stack = [f]
        while stack:
            g = stack.pop()
            if g.index in seen:
                continue
            seen.add(g.index)
            for e in g.edges:
                stack.extend(h for h in e.link_faces if h.index not in seen)
    rows.append({
        "id": obj.name[: -len("_body")] if obj.name.endswith("_body") else obj.name, "object": obj.name, "parent": obj.parent.name if obj.parent else "",
        "faces": len(bm.faces), "shells": shells, "boundary_edges": boundary,
        "nonmanifold_edges": nonman_e, "nonmanifold_verts": nonman_v,
        "manifold": nonman_e == 0 and nonman_v == 0, "closed": boundary == 0,
        "volume": bm.calc_volume(signed=True),
        "radius": (max(xs) - min(xs) + max(ys) - min(ys)) / 4, "z_min": min(zs), "z_max": max(zs),
        "center": [(max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2],
    })
    bm.free()
with open(out, "w", encoding="utf-8") as fh:
    json.dump(rows, fh, indent=1)
print("CHECKED", len(rows), "bodies;", sum(1 for r in rows if not (r["closed"] and r["manifold"])), "open")
