"""Ground contact: tank bases snap to the measured foundation top or ground (never floating
or sunk), the foundation is its own object, and local-offset Z stays consistent."""

import json
import os
import subprocess
from pathlib import Path

import numpy as np

from tankextract import grounding as G
from tankextract.model import Tank
from tankextract.pipeline import RunOptions, _spec
from tankextract.tools import resolve_blender

BLENDER = os.environ.get("BLENDER")


def _up_ring(cx, cy, r0, r1, z, step=0.1):
    pts = []
    for r in np.arange(r0 + step / 2, r1, step):
        n = max(8, int(2 * np.pi * r / step))
        for a in np.linspace(0, 2 * np.pi, n, endpoint=False):
            pts.append([cx + r * np.cos(a), cy + r * np.sin(a), z, 0, 0, 1, step * step])
    return np.array(pts)


def _wall(cx, cy, r, z0, z1, step=0.1):
    pts = []
    n = int(2 * np.pi * r / step)
    for a in np.linspace(0, 2 * np.pi, n, endpoint=False):
        for z in np.arange(z0 + step / 2, z1, step):
            pts.append([cx + r * np.cos(a), cy + r * np.sin(a), z, np.cos(a), np.sin(a), 0, step * step])
    return np.array(pts)


def _tank(z_base, wall_top, origin=(0.0, 0.0, 0.0), name="Tank_T"):
    return Tank(block="b", srs="EPSG:4548", origin=origin, center_x=origin[0] + 5.0, center_y=origin[1] + 5.0,
                z_base=z_base, z_ground=z_base - 0.5, radius=4.0, wall_height=wall_top - z_base,
                total_height=wall_top - z_base + 1.0, roof_type="cone", roof_rise=1.0, rms=0.0, inlier_ratio=1.0,
                coverage_deg=360.0, name=name)


def _scene(found_top=None, prot=0.3, ground=10.0, R=4.0, cx=5.0, cy=5.0, wall_top=18.0):
    parts = []
    if found_top is not None:
        parts += [_up_ring(cx, cy, R - 0.25, R + prot, found_top), _wall(cx, cy, R + prot, ground, found_top)]
        parts.append(_up_ring(cx, cy, R + prot, R + 6.0, ground))
        parts.append(_wall(cx, cy, R, found_top, wall_top))
    else:
        parts.append(_up_ring(cx, cy, R, R + 6.0, ground))
        parts.append(_wall(cx, cy, R, ground, wall_top))
    return np.concatenate(parts)


def test_tank_on_foundation_gets_foundation_and_base_on_its_top():
    F = _scene(found_top=10.6)
    t = _tank(10.3, 18.0)  # shell bottom measured too low (foundation side read as wall)
    rows = []
    G.ground_tank(F, t, rows)
    assert abs(t.z_base - 10.6) < 0.03
    assert abs((t.z_base + t.wall_height) - 18.0) < 1e-6  # eave unchanged
    fd = t.foundation
    assert fd and abs(fd["z0"] - 10.0) < 0.03 and abs(fd["height"] - 0.6) < 0.05 and abs(fd["protrusion"] - 0.3) < 0.15
    body = [r for r in rows if r["object"] == "Tank_T_body"][0]
    assert body["stands_on"] == "foundation" and abs(body["gap_after_m"]) < 1e-9
    assert abs(body["gap_before_m"] - (-0.3)) < 0.05 and abs(body["gap_to_ground_before_m"] - 0.3) < 0.05


def test_floating_and_sunk_tanks_snap_to_ground():
    F = _scene()
    for z0 in (10.45, 9.7):
        t = _tank(z0, 18.0)
        rows = []
        G.ground_tank(F, t, rows)
        assert abs(t.z_base - 10.0) < 0.03, z0
        assert t.foundation is None
        assert abs(t.wall_height - (18.0 - t.z_base)) < 1e-6
        assert abs(rows[0]["gap_before_m"] - (z0 - 10.0)) < 0.05 and rows[0]["gap_after_m"] == 0.0


def test_ground_ignores_kerbs_and_equipment():
    F = np.concatenate([_up_ring(0, 0, 1.0, 3.0, 5.0), _up_ring(0, 0, 2.0, 2.3, 5.8), _up_ring(0, 0, 2.6, 2.7, 6.5)])
    g, n = G.ring_ground(F, 0, 0, 1.0, 3.0, 5.5)
    assert abs(g - 5.0) < 0.02 and n > 100


def test_foundation_exported_at_consistent_local_z(tmp_path):
    """Non-zero origin Z: the FBX (local offset) must equal absolute Z minus the origin for the
    body, the foundation and every other part."""
    blender = resolve_blender(BLENDER)
    if blender is None:
        return
    origin = (629024.0, 3586203.0, 100.0)
    F = _scene(found_top=110.6, ground=110.0, cx=origin[0] + 5.0, cy=origin[1] + 5.0, wall_top=118.0)
    t = _tank(110.3, 118.0, origin=origin)
    rows = []
    G.ground_tank(F, t, rows)
    fd = t.foundation
    t.extra_parts = [{"name": "Tank_T_foundation", "type": "cylinder", "center": [t.center_x, t.center_y],
                      "z0": fd["z0"], "z1": fd["z1"], "radius": fd["radius"], "segments": 48}]
    opts = RunOptions()
    opts.local_offset = True
    spec = _spec([t], opts, [], [])
    spec["segments"] = 48
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "tankextract" / "blender" / "export_tanks.py"
    out = tmp_path / "fbx"
    out.mkdir()
    subprocess.run([blender, "-b", "--python-exit-code", "1", "-P", str(script), "--", "--spec", str(tmp_path / "spec.json"),
                    "--output", str(out), "--merge"], check=True, capture_output=True)
    check = Path(__file__).resolve().parents[1] / "tankextract" / "blender" / "check_bodies.py"
    res_path = tmp_path / "check.json"
    subprocess.run([blender, "-b", "--python-exit-code", "1", "-P", str(check), "--", str(out / "tanks_all.fbx"), str(res_path),
                    "--all"], check=True, capture_output=True)
    res = {r["object"]: r for r in json.loads(res_path.read_text(encoding="utf-8"))}
    oz = origin[2]
    body, found = res["Tank_T_body"], res["Tank_T_foundation"]
    assert abs(body["z_min"] - (t.z_base - oz)) < 0.005
    assert abs(found["z_min"] - (fd["z0"] - oz)) < 0.005 and abs(found["z_max"] - (fd["z1"] - oz)) < 0.005
    assert abs(found["z_max"] - body["z_min"]) < 0.005  # body stands exactly on the foundation
    assert found["closed"] and found["manifold"]
