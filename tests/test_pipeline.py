"""Synthetic block: known tanks, one of them split across two tiles."""

import json
import os
from pathlib import Path

import numpy as np

from tankextract.pipeline import RunOptions, run
from tankextract.synthetic import EXPECTED, ORIGIN, SRS, generate_scene
from tankextract.tools import resolve_blender

BLENDER = os.environ.get("BLENDER") or (
    "/tmp/blender/blender-4.2.14-linux-x64/blender"
    if Path("/tmp/blender/blender-4.2.14-linux-x64/blender").is_file()
    else None
)


def _match(tanks):
    remaining = list(tanks)
    pairs = []
    for spec in EXPECTED:
        abs_x = ORIGIN[0] + spec["x"]
        abs_y = ORIGIN[1] + spec["y"]
        best = min(remaining, key=lambda tank: (tank.center_x - abs_x) ** 2 + (tank.center_y - abs_y) ** 2)
        remaining.remove(best)
        pairs.append((spec, best))
    return pairs, remaining


def test_folder_and_zip_recover_known_tanks(tmp_path):
    block = generate_scene(tmp_path / "scene")
    zip_path = tmp_path / "scene" / "zips" / "E_OSGB.zip"
    for source in (block, zip_path):
        out = tmp_path / ("from-" + source.suffix.strip(".") or "dir")
        tanks = run(
            source,
            out,
            RunOptions(reader="python", no_blender=True, merge=False),
            log=lambda *_args, **_kwargs: None,
        )
        pairs, extra = _match(tanks)
        assert extra == []
        assert len(pairs) == 3
        for spec, tank in pairs:
            assert tank.srs == SRS
            assert tank.origin == ORIGIN
            assert abs(tank.center_x - (ORIGIN[0] + spec["x"])) < 0.05
            assert abs(tank.center_y - (ORIGIN[1] + spec["y"])) < 0.05
            assert abs(tank.radius - spec["r"]) < 0.05
            assert abs(tank.z_base - spec["z_base"]) < 0.08
            assert abs(tank.wall_height - spec["wall_h"]) < 0.10
            assert abs(tank.total_height - spec["total_h"]) < 0.12
            assert tank.roof_type == spec["roof"]
            assert abs(tank.roof_rise - spec["rise"]) < 0.10
            assert tank.rms < 0.05
            assert tank.coverage_deg >= 300
            assert bool(tank.spiral_stair) is spec["stair"]
            assert len(tank.wind_girders) == spec["girders"]
            assert bool(tank.base_ring) is spec["base_ring"]
            assert (tank.top_rim_width is not None) is spec["rim"]
        geo = json.loads((out / "tanks.geojson").read_text(encoding="utf-8"))
        assert geo["crs"]["properties"]["name"] == "EPSG:4548"
        assert "4548" in (out / "tanks.prj").read_text(encoding="utf-8")
        assert "117" in (out / "tanks.prj").read_text(encoding="utf-8")


def test_blender_fbx_local_and_absolute(tmp_path):
    blender = resolve_blender(BLENDER)
    if blender is None:
        return
    block = generate_scene(tmp_path / "scene")
    for local in (True, False):
        out = tmp_path / ("local" if local else "absolute")
        tanks = run(
            block,
            out,
            RunOptions(
                reader="python",
                blender=blender,
                local_offset=local,
                merge=True,
                segments=48,
            ),
            log=lambda *_a, **_k: None,
        )
        assert len(tanks) == 3
        report = json.loads((out / "blender_report.json").read_text(encoding="utf-8"))
        assert len(report["files"]) == 3
        merged = Path(report["merged"])
        assert merged.is_file()
        assert merged.read_bytes().startswith(b"Kaydara FBX Binary")
        for item in report["files"]:
            blob = Path(item["path"]).read_bytes()
            assert blob.startswith(b"Kaydara FBX Binary")
            assert item["reimport_vertex"] is not None
            delta = np.linalg.norm(np.array(item["sample_vertex"]) - np.array(item["reimport_vertex"]))
            # Reimport goes through FBX float32. Local metres stay at centimetres;
            # absolute EPSG:4548 Y is ~3.5e6, where float32 steps are about 0.25 m.
            assert delta < (0.02 if local else 0.5)
            props = item["reimport_props"]
            assert props.get("epsg") == "EPSG:4548"
            assert props.get("coordinate_mode") == ("local" if local else "absolute")
            assert props.get("srs_origin_x") == "629024.000000"
            assert props.get("srs_origin_y") == "3586203.000000"
            if local:
                assert max(abs(v) for v in item["sample_vertex"]) < 100
            else:
                assert item["sample_vertex"][0] > 1000
