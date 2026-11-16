"""Every tank body in the FBX must be one closed, manifold, outward-facing shell, whatever the
roof type, and pipes / pipe racks are off unless asked for."""

import json
import math
import os
import subprocess
from pathlib import Path

from tankextract.cli import build_parser
from tankextract.pipeline import RunOptions
from tankextract.tools import resolve_blender

BLENDER = os.environ.get("BLENDER")
ORIGIN = [629024.0, 3586203.0, 0.0]


def _tank(tid, x, roof, rise, sphere=None):
    return {"id": tid, "origin": ORIGIN, "center_local": [x, 0.0], "z_base": 48.0, "radius": 5.0,
            "wall_height": 0.0 if sphere else 10.0, "total_height": 10.0 + rise, "roof_type": roof, "roof_rise": rise,
            "top_rim_width": None, "wind_girders": [], "base_ring": None, "spiral_stair": None,
            "handrail_height": 1.1, "sphere": sphere, "epsg": "EPSG:4548", "rms_m": 0.0, "extra_parts": []}


def test_pipes_are_off_by_default():
    assert RunOptions().pipes is False
    args = build_parser().parse_args(["-i", "a", "-o", "b"])
    assert args.pipes is False
    assert build_parser().parse_args(["-i", "a", "-o", "b", "--pipes"]).pipes is True


def test_blender_tank_bodies_are_closed_manifolds(tmp_path):
    blender = resolve_blender(BLENDER)
    if blender is None:
        return
    sphere = {"center_z": 58.0, "radius": 5.0, "legs": [0, 90, 180, 270], "leg_rho": 4.8, "leg_radius": 0.25,
              "leg_bottom": 48.0, "bracing": False}
    tanks = [_tank("Tank_F", 0.0, "flat", 0.0), _tank("Tank_C", 20.0, "cone", 1.5),
             _tank("Tank_D", 40.0, "dome", 1.2), _tank("Tank_S", 60.0, "sphere", 0.0, sphere)]
    spec = {"coordinate_mode": "local", "segments": 48, "tanks": tanks, "connectors": [], "structures": []}
    (tmp_path / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "tankextract" / "blender" / "export_tanks.py"
    out = tmp_path / "fbx"
    out.mkdir()
    subprocess.run([blender, "-b", "--python-exit-code", "1", "-P", str(script), "--", "--spec", str(tmp_path / "spec.json"),
                    "--output", str(out), "--merge"], check=True, capture_output=True)
    check = Path(__file__).resolve().parents[1] / "tankextract" / "blender" / "check_bodies.py"
    res_path = tmp_path / "check.json"
    subprocess.run([blender, "-b", "--python-exit-code", "1", "-P", str(check), "--", str(out / "tanks_all.fbx"), str(res_path)],
                   check=True, capture_output=True)
    res = {r["id"]: r for r in json.loads(res_path.read_text(encoding="utf-8"))}
    want = {"Tank_F": math.pi * 25 * 10, "Tank_C": math.pi * 25 * (10 + 1.5 / 3),
            "Tank_S": 4 / 3 * math.pi * 125}
    for tid in ("Tank_F", "Tank_C", "Tank_D", "Tank_S"):
        r = res[tid]
        assert r["closed"] and r["manifold"] and r["boundary_edges"] == 0, r
        assert r["volume"] > 0, r  # outward normals
        assert abs(r["radius"] - 5.0) < 0.05, r
    for tid, vol in want.items():
        assert abs(res[tid]["volume"] - vol) / vol < 0.03, (tid, res[tid]["volume"], vol)
    assert res["Tank_D"]["volume"] > want["Tank_F"]
