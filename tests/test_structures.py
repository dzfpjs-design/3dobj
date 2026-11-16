"""Stair towers, pipe racks, wall stairs / pipes, sphere supports and roof platforms:
measured from synthetic meshes with known dimensions, and exported as separate objects."""

import json
import math
import os
import subprocess
from pathlib import Path

import numpy as np

from tankextract import structures as S
from tankextract.model import Tank
from tankextract.tools import resolve_blender

BLENDER = os.environ.get("BLENDER")


# ------------------------------------------------------------ mesh helpers ---

def _faces(parts):
    """(N, 7) faces from (verts, tris) parts, triangles oriented by each part's rule."""
    out = []
    for verts, tris, outward in parts:
        v = np.asarray(verts, float)
        t = np.asarray(tris, np.int64)
        a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
        cen = (a + b + c) / 3
        n = np.cross(b - a, c - a)
        L = np.linalg.norm(n, axis=1)
        n = n / np.maximum(L, 1e-12)[:, None]
        want = outward(cen) if callable(outward) else np.broadcast_to(np.asarray(outward, float), n.shape)
        flip = (n * want).sum(axis=1) < 0
        n[flip] *= -1
        out.append(np.column_stack([cen, n, L / 2]))
    return np.concatenate(out)


def _quad(c00, c10, c01, nu, nv, outward):
    c00, c10, c01 = (np.asarray(p, float) for p in (c00, c10, c01))
    verts = [c00 + (c10 - c00) * i / nu + (c01 - c00) * j / nv for j in range(nv + 1) for i in range(nu + 1)]
    tris = []
    for j in range(nv):
        for i in range(nu):
            a = j * (nu + 1) + i
            tris += [(a, a + 1, a + nu + 2), (a, a + nu + 2, a + nu + 1)]
    return verts, tris, outward


def _hplate(x0, x1, y0, y1, z, step=0.1, up=True):
    nu, nv = max(1, int((x1 - x0) / step)), max(1, int((y1 - y0) / step))
    return _quad((x0, y0, z), (x1, y0, z), (x0, y1, z), nu, nv, (0, 0, 1 if up else -1))


def _vplate(a, b, z0, z1, outward, step=0.1):
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    return _quad((a[0], a[1], z0), (b[0], b[1], z0), (a[0], a[1], z1), max(1, int(L / step)), max(1, int((z1 - z0) / step)), outward)


def _tube(p0, p1, r, n=20, step=0.1):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    ax = p1 - p0
    L = np.linalg.norm(ax)
    ax = ax / L
    ref = np.array([0, 0, 1.0]) if abs(ax[2]) < 0.9 else np.array([1.0, 0, 0])
    e1 = np.cross(ax, ref)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(ax, e1)
    nl = max(2, int(L / step))
    verts = [p0 + ax * L * i / nl + r * (math.cos(2 * math.pi * k / n) * e1 + math.sin(2 * math.pi * k / n) * e2)
             for i in range(nl + 1) for k in range(n)]
    tris = []
    for i in range(nl):
        for k in range(n):
            a, b = i * n + k, i * n + (k + 1) % n
            tris += [(a, b, b + n), (a, b + n, a + n)]

    def outward(c):
        rel = c - p0
        return rel - np.outer(rel @ ax, ax)
    return verts, tris, outward


def _tank(radius, wall, cx=0.0, cy=0.0, roof="flat", rise=0.0, sphere=None):
    return Tank(block="T", srs="EPSG:4548", origin=(0.0, 0.0, 0.0), center_x=cx, center_y=cy, z_base=0.0, z_ground=0.0,
                radius=radius, wall_height=wall, total_height=wall + rise, roof_type=roof, roof_rise=rise, rms=0.0,
                inlier_ratio=1.0, coverage_deg=360.0, sphere=sphere, name="Tank_T")


# -------------------------------------------------------------- the tests ---

def _parts_zmin(parts):
    """Lowest point of a list of primitive parts, as the exporter builds them."""
    z = []
    for p in parts:
        k = p["type"]
        if k == "beam":  # top on the p0-p1 line, hangs `depth` below it
            z.append(min(p["p0"][2], p["p1"][2]) - p["depth"])
        elif k == "box":
            z.append(p["center"][2] - p["size"][2] / 2)
        elif k == "rod":  # round end: a sloped rod dips by up to its radius below the lower end
            z.append(min(p["p0"][2], p["p1"][2]) - (p["radius"] if p["p0"][:2] != p["p1"][:2] else 0.0))
        elif k in ("prism", "cylinder"):
            z.append(p["z0"])
    return min(z)


def test_stair_tower_frame_and_landings():
    L, W, deg = 3.0, 2.6, 30.0
    corners = S.rect_corners(0.0, 0.0, L, W, deg)
    parts = [_hplate(-8, 8, -8, 8, 0.0, step=0.4)]
    for k in range(4):
        a, b = corners[k], corners[(k + 1) % 4]
        parts.append(_vplate(a, b, 0.0, 15.0, lambda c: np.column_stack([c[:, 0], c[:, 1], 0 * c[:, 2]])))
    u, v = S._rot(deg)
    for lv in np.arange(1.5, 15.01, 1.5):
        quad = _quad((*(-u * L / 2 - v * W / 2), lv), (*(u * L / 2 - v * W / 2), lv), (*(-u * L / 2 + v * W / 2), lv), 30, 26, (0, 0, 1))
        parts.append(quad)
    F = _faces(parts)
    t = S.detect_stair_tower(F, 0.0, 0.0, 0.0, 15.2, L, W, deg)
    assert t is not None
    assert abs(t["length"] - L) < 0.2 and abs(t["width"] - W) < 0.2
    assert abs(((t["axis_deg"] - deg + 90) % 180) - 90) < 2.0
    assert abs(t["rise"] - 1.5) < 0.1 and abs(t["z_top"] - 15.0) < 0.15
    assert t["levels_measured"] >= 8
    tparts = S.stair_tower_parts(t, "Stair_T")
    names = {p["name"] for p in tparts}
    for want in ("Stair_T_column_01", "Stair_T_flight_01", "Stair_T_landing_01", "Stair_T_rail_top_top", "Stair_T_top_deck"):
        assert want in names
    # nothing sinks below the tower's ground (the first flight's stringers used to hang 0.2 m into it)
    assert abs(_parts_zmin(tparts) - t["z_ground"]) < 0.01


def test_pipe_rack_pipes_tiers_and_bents():
    parts = [_hplate(-16, 16, -6, 6, 0.0, step=0.4)]
    bents = [-12.0, -6.0, 0.0, 6.0, 12.0]
    for x in bents:
        for y in (-1.0, 1.0):
            for (dx, dy) in ((0.15, 0), (-0.15, 0), (0, 0.15), (0, -0.15)):
                a = (x + dx - abs(dy), y + dy - abs(dx))
                b = (x + dx + abs(dy), y + dy + abs(dx))
                parts.append(_vplate(a, b, 0.0, 7.2, (np.sign(dx), np.sign(dy), 0), step=0.05))
        for z in (5.0, 7.0):
            parts.append(_hplate(x - 0.15, x + 0.15, -1.0, 1.0, z, step=0.05))
    for z in (5.0, 7.0):
        for y in (-0.9, 0.9):
            parts.append(_hplate(-12, 12, y - 0.1, y + 0.1, z, step=0.1))
    pipes = [(-0.5, 0.15, 5.0), (0.3, 0.25, 5.0), (0.6, 0.10, 7.0)]
    for y, r, zb in pipes:
        parts.append(_tube((-12, y, zb + r), (12, y, zb + r), r, n=24, step=0.2))
    F = _faces(parts)
    gfun = S.ground_grid(F)
    ground = gfun(F[:, 0], F[:, 1])
    elev = S.elevated_mask(F, [], ground)
    segs = S.rack_segments(F, elev, S.site_axis(F))
    long = [s for s in segs if s["length"] > 15]
    assert len(long) == 1, segs
    seg = long[0]
    assert abs(seg["length"] - 24.3) < 1.2 and abs(seg["width"] - 2.0) < 0.5
    m = S.measure_rack(F, ground, elev, seg)
    m["pipes"] = [p for p in m["pipes"] if p["r"] >= 0.05]
    u, v = S._rot(seg["axis_deg"])
    found = sorted((2 * p["r"], float((u * 0 + v * p["v"])[1]) if abs(v[1]) > 0.5 else p["v"], p["z"]) for p in m["pipes"])
    for y, r, zb in pipes:
        assert any(abs(d - 2 * r) < 0.07 and abs(z - (zb + r)) < 0.06 for d, _vv, z in found), (y, r, found)
    assert any(abs(z - 5.0) < 0.15 for z in m["tiers"]) and any(abs(z - 7.0) < 0.15 for z in m["tiers"]), m["tiers"]
    assert len(m["bents"]) >= 4
    names = {p["name"] for p in S.rack_parts("PipeRack_T", seg, m)}
    assert {"PipeRack_T_column_01A", "PipeRack_T_crossbeam_t1", "PipeRack_T_longbeam_t1A", "PipeRack_T_pipe_01"} <= names


def test_wall_stair_and_wall_pipe():
    R = 6.0
    tank = _tank(R, 12.0)
    parts = [_hplate(-12, 12, -12, 12, 0.0, step=0.4)]
    n = 120
    verts = [(R * math.cos(2 * math.pi * k / n), R * math.sin(2 * math.pi * k / n), 12.0 * j / 60) for j in range(61) for k in range(n)]
    tris = [(j * n + k, j * n + (k + 1) % n, (j + 1) * n + (k + 1) % n) for j in range(60) for k in range(n)]
    tris += [(j * n + k, (j + 1) * n + (k + 1) % n, (j + 1) * n + k) for j in range(60) for k in range(n)]
    parts.append((verts, tris, lambda c: np.column_stack([c[:, 0], c[:, 1], 0 * c[:, 2]])))
    # helical stair: 45 deg, from az 30 deg, ccw, treads 0.8 m wide outside the wall
    rm, a0 = R + 0.6, math.radians(30)
    steps = 60
    for i in range(steps):
        z = 12.0 * (i + 1) / steps
        a = a0 + (z / 1.0) / rm  # slope 1 -> 45 deg
        c = np.array([math.cos(a), math.sin(a)])
        tvec = np.array([-c[1], c[0]])
        p0 = c * (R + 0.2) - tvec * 0.12
        parts.append(_quad((*p0, z), (*(p0 + c * 0.8), z), (*(p0 + tvec * 0.24), z), 8, 3, (0, 0, 1)))
    # a 0.3 m pipe on the wall at az 200 deg, 0.25 m off the shell
    ap = math.radians(200)
    rp = R + 0.25 + 0.15
    parts.append(_tube((rp * math.cos(ap), rp * math.sin(ap), 0.3), (rp * math.cos(ap), rp * math.sin(ap), 11.6), 0.15, n=24, step=0.1))
    F = _faces(parts)
    st = S.wall_stair(F, tank)
    assert st is not None
    assert abs(st["slope_deg"] - 45) < 3 and st["direction"] == "ccw"
    assert abs(st["z_top"] - 12.0) < 0.5 and abs(((st["az_bottom_deg"] - 30 + 180) % 360) - 180) < 6
    assert abs(st["width"] - 0.8) < 0.2
    ex = [((st["az_bottom_deg"] + st["sweep_deg"] * f) % 360, 4.0) for f in np.linspace(0, 1, 12)]
    pipes = S.wall_pipes(F, tank, ex)
    assert len(pipes) == 1, pipes
    assert abs(((pipes[0]["azimuth_deg"] - 200 + 180) % 360) - 180) < 3
    assert abs(pipes[0]["diameter"] - 0.3) < 0.07
    sparts = S.wall_stair_parts("Tank_T", tank, st)
    names = {p["name"] for p in sparts}
    assert {"Tank_T_stair_treads", "Tank_T_stair_stringer_outer", "Tank_T_stair_rail", "Tank_T_stair_landing"} <= names
    assert abs(_parts_zmin(sparts) - st["z_bottom"]) < 0.01  # the stair foot stands on its bottom level


def test_sphere_legs_pedestals_and_measured_bracing():
    R, zc, rho, legs = 6.0, 9.0, 5.8, [k * 45.0 for k in range(8)]
    sph = {"center_z": zc, "radius": R, "legs": legs, "leg_rho": rho, "leg_radius": 0.45,
           "leg_contact_z": zc - math.sqrt(R * R - rho * rho)}
    tank = _tank(R, 0.0, roof="sphere", sphere=sph)
    parts = [_hplate(-12, 12, -12, 12, 0.0, step=0.3)]
    pts = []
    for a in legs:
        ar = math.radians(a)
        x, y = rho * math.cos(ar), rho * math.sin(ar)
        pts.append((x, y))
        parts.append(_tube((x, y, 1.0), (x, y, zc), 0.25, n=32, step=0.1))
        h = 0.6
        for (dx, dy) in ((h, 0), (-h, 0), (0, h), (0, -h)):
            a2 = (x + dx - abs(dy), y + dy - abs(dx))
            b2 = (x + dx + abs(dy), y + dy + abs(dx))
            parts.append(_vplate(a2, b2, 0.0, 1.0, (np.sign(dx), np.sign(dy), 0), step=0.05))
        parts.append(_hplate(x - h, x + h, y - h, y + h, 1.0, step=0.05))
    for i in range(len(pts)):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % len(pts)]
        parts.append(_tube((x0, y0, 1.3), (x1, y1, 6.3), 0.04, n=8, step=0.05))
        parts.append(_tube((x0, y0, 6.3), (x1, y1, 1.3), 0.04, n=8, step=0.05))
    F = _faces(parts)
    out = S.sphere_supports(F, tank)
    assert abs(out["leg_diameter"] - 0.5) < 0.08, out
    ped = out["pedestal"]
    assert abs(ped["height"] - 1.0) < 0.1 and abs(ped["size_radial"] - 1.2) < 0.25 and abs(ped["size_tangential"] - 1.2) < 0.25
    assert abs(out["brace_z0"] - 1.3) < 0.4 and abs(out["brace_z1"] - 6.3) < 0.4, out


def test_dome_roof_platform_and_radial_walkway():
    R, wall, rise = 10.0, 15.0, 2.0
    tank = _tank(R, wall, roof="dome", rise=rise)

    def dome(r):
        return wall + rise * (1 - (r / R) ** 2)
    parts = []
    nr, na = 50, 180
    verts = [((R * i / nr) * math.cos(2 * math.pi * k / na), (R * i / nr) * math.sin(2 * math.pi * k / na), dome(R * i / nr))
             for i in range(nr + 1) for k in range(na)]
    tris = [(i * na + k, i * na + (k + 1) % na, (i + 1) * na + (k + 1) % na) for i in range(nr) for k in range(na)]
    tris += [(i * na + k, (i + 1) * na + (k + 1) % na, (i + 1) * na + k) for i in range(nr) for k in range(na)]
    parts.append((verts, tris, (0, 0, 1)))
    zp = dome(0) + 0.1
    # central platform r = 1.3 with a 1.05 m rail ring
    parts.append(_hplate(-0.9, 0.9, -0.9, 0.9, zp, step=0.05))
    ring = [(1.3 * math.cos(2 * math.pi * k / 48), 1.3 * math.sin(2 * math.pi * k / 48)) for k in range(49)]
    for k in range(48):
        parts.append(_vplate(ring[k], ring[k + 1], zp, zp + 1.05, lambda c: np.column_stack([c[:, 0], c[:, 1], 0 * c[:, 2]]), step=0.05))
    # radial walkway at az 60 deg, 0.8 m wide, 1.1 m rails, on the dome
    a = math.radians(60)
    uvec = np.array([math.cos(a), math.sin(a)])
    nvec = np.array([-uvec[1], uvec[0]])
    for i in range(40):
        r0, r1 = 1.3 + (R - 1.3) * i / 40, 1.3 + (R - 1.3) * (i + 1) / 40
        z0, z1 = dome(r0) + 0.12, dome(r1) + 0.12
        for sgn in (-1, 1):
            p = uvec * r0 + nvec * sgn * 0.4
            q = uvec * r1 + nvec * sgn * 0.4
            parts.append(_quad((*p, z0), (*q, z1), (*p, z0 + 1.1), 3, 11, (*(nvec * sgn), 0)))
        p0, p1 = uvec * r0 - nvec * 0.4, uvec * r1 - nvec * 0.4
        parts.append(_quad((*p0, z0), (*p1, z1), (*(p0 + nvec * 0.8), z0), 3, 8, (0, 0, 1)))
    F = _faces(parts)
    info = S.roof_platform(F, tank)
    assert info and info.get("platform"), info
    assert abs(info["platform"]["radius"] - 1.3) < 0.35
    assert info["platform"]["rail_height"] and abs(info["platform"]["rail_height"] - 1.05) < 0.2
    assert len(info["walkways"]) == 1, info["walkways"]
    w = info["walkways"][0]
    assert abs(((w["azimuth_deg"] - 60 + 180) % 360) - 180) < 4
    assert abs(w["width"] - 0.8) < 0.3 and abs(w["rail_height"] - 1.1) < 0.2
    names = {p["name"] for p in S.platform_parts("Tank_T", tank, info)}
    assert {"Tank_T_platform", "Tank_T_platform_rail_top", "Tank_T_roof_walkway_01", "Tank_T_roof_walkway_01_rail_L_top"} <= names


def test_blender_structures_are_separate_objects(tmp_path):
    """Stair / rack parts and tank accessories each become one object under their Empty."""
    blender = resolve_blender(BLENDER)
    if blender is None:
        return
    origin = [629024.0, 3586203.0, 0.0]
    t = {"x": 10.0, "y": 20.0, "length": 3.0, "width": 2.6, "axis_deg": 30.0, "z_ground": 48.0, "z_top": 57.0,
         "levels": [49.5, 51.0, 52.5, 54.0, 55.5, 57.0], "rise": 1.5, "rail_height": 1.1, "open_sides": [0], "bridges": []}
    stair_parts = S.stair_tower_parts(t, "Stair_T")
    tank = {"id": "Tank_P", "origin": origin, "center_local": [0.0, 0.0], "z_base": 48.0, "radius": 5.0, "wall_height": 10.0,
            "total_height": 10.0, "roof_type": "flat", "roof_rise": 0.0, "top_rim_width": None, "wind_girders": [],
            "base_ring": None, "spiral_stair": None, "handrail_height": None, "sphere": None, "epsg": "EPSG:4548", "rms_m": 0.0,
            "extra_parts": [S.box("Tank_P_pedestal_01", (6.0, 0.0, 48.5), (1.2, 1.2, 1.0), 0.0),
                            S.rod("Tank_P_pipe_01", (5.4, 0.0, 48.5), (5.4, 0.0, 57.5), 0.15)]}
    spec = {"coordinate_mode": "local", "segments": 32, "tanks": [tank], "connectors": [],
            "structures": [{"id": "Stair_T", "kind": "stair_tower", "parent": "", "origin": origin,
                            "anchor_local": [10.0, 20.0, 48.0], "parts": stair_parts, "epsg": "EPSG:4548"}]}
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "tankextract" / "blender" / "export_tanks.py"
    out = tmp_path / "fbx"
    out.mkdir()
    subprocess.run([blender, "-b", "--python-exit-code", "1", "-P", str(script), "--", "--spec", str(spec_path),
                    "--output", str(out), "--merge"], check=True, capture_output=True)
    report = json.loads((out / "blender_report.json").read_text(encoding="utf-8"))
    objs = report["structure_objects"]
    assert objs[0] == "Stair_T"
    for want in ("Stair_T_column_01", "Stair_T_flight_01", "Stair_T_landing_01", "Stair_T_rail_top_top", "Stair_T_beam_01"):
        assert want in objs
    tank_objs = report["files"][0]["objects"]
    assert {"Tank_P", "Tank_P_body", "Tank_P_pedestal_01", "Tank_P_pipe_01"} <= set(tank_objs)
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import bpy, json, sys\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        f"bpy.ops.import_scene.fbx(filepath={str(out / 'tanks_all.fbx')!r})\n"
        "bpy.context.view_layer.update()\n"
        "res = {}\n"
        "for o in bpy.data.objects:\n"
        "    w = [list(o.matrix_world @ v.co) for v in o.data.vertices] if o.type == 'MESH' else None\n"
        "    res[o.name] = {'type': o.type, 'parent': o.parent.name if o.parent else '',\n"
        "                   'center': ([sum(c[i] for c in w) / len(w) for i in range(3)] if w else list(o.location))}\n"
        f"open({str(tmp_path / 'probe.json')!r}, 'w').write(json.dumps(res))\n",
        encoding="utf-8",
    )
    subprocess.run([blender, "-b", "--python-exit-code", "1", "-P", str(probe)], check=True, capture_output=True)
    res = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    assert res["Stair_T"]["type"] == "EMPTY" and res["Stair_T"]["parent"] == ""
    for name, item in res.items():
        if item["type"] == "MESH" and name.startswith("Stair_T_"):
            assert item["parent"] == "Stair_T", name
        if item["type"] == "MESH" and name.startswith("Tank_P_"):
            assert item["parent"] == "Tank_P", name
    # real (local) coordinates: pedestal centre and the stair tower centre
    ped = res["Tank_P_pedestal_01"]["center"]
    assert abs(ped[0] - 6.0) < 0.02 and abs(ped[1]) < 0.02 and abs(ped[2] - 48.5) < 0.02
    col = res["Stair_T_column_01"]["center"]
    c0 = S.rect_corners(10.0, 20.0, 3.0, 2.6, 30.0)[0]
    assert abs(col[0] - c0[0]) < 0.02 and abs(col[1] - c0[1]) < 0.02
