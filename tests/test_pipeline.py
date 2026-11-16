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
        # Synthetic tanks are not linked: connector files exist and are empty.
        assert (out / "connectors.csv").read_text(encoding="utf-8-sig").startswith("id,kind,tank_a")
        assert json.loads((out / "connectors.geojson").read_text(encoding="utf-8"))["features"] == []
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


def test_blender_sphere_and_handrail(tmp_path):
    """Spherical tanks on legs and handrails come out as their own pieces."""
    blender = resolve_blender(BLENDER)
    if blender is None:
        return
    import subprocess

    block = generate_scene(tmp_path / "scene")
    out = tmp_path / "out"
    run(block, out, RunOptions(reader="python", no_blender=True, local_offset=True), log=lambda *_a, **_k: None)
    spec = json.loads((out / "tanks_spec.json").read_text(encoding="utf-8"))
    base = spec["tanks"][0]
    sphere = dict(base, id="Tank_S", roof_type="sphere", wall_height=0.0, roof_rise=0.0, handrail_height=None)
    sphere["sphere"] = {"center_z": base["z_base"] + 9.0, "radius": 6.0, "legs": [0, 90, 180, 270],
                        "leg_rho": 5.8, "leg_radius": 0.3, "leg_bottom": base["z_base"],
                        "leg_contact_z": base["z_base"] + 7.5, "bracing": "X"}
    railed = dict(base, id="Tank_H", roof_type="dome", roof_rise=1.5, handrail_height=1.1, sphere=None)
    spec["tanks"] = [sphere, railed]
    x, y = base["center_local"]
    z = base["z_base"] + 12.0
    spec["connectors"] = [{
        "id": "Connector_T", "kind": "walkway", "tank_a": "Tank_S", "tank_b": "Tank_H", "origin": base["origin"],
        "start_local": [x, y, z], "end_local": [x + 4.0, y, z + 0.5], "width": 1.0, "depth": 0.25,
        "handrail_height": 1.1, "epsg": base["epsg"],
        "supports": [{"x": x + 2.0, "y": y, "z_bottom": base["z_base"], "z_top": z - 0.25,
                      "length": 0.4, "width": 0.4, "axis_deg": 0.0}],
    }]
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "tankextract" / "blender" / "export_tanks.py"
    fbx = tmp_path / "fbx"
    fbx.mkdir()
    subprocess.run([blender, "-b", "--python-exit-code", "1", "-P", str(script), "--", "--spec", str(spec_path),
                    "--output", str(fbx), "--merge"], check=True, capture_output=True)
    report = json.loads((fbx / "blender_report.json").read_text(encoding="utf-8"))
    objects = {item["path"].rsplit("/", 1)[-1]: item["objects"] for item in report["files"]}
    # Body and every structure part are separate objects under one parent empty.
    s_objs, h_objs = objects["Tank_S.fbx"], objects["Tank_H.fbx"]
    assert s_objs[0] == "Tank_S" and h_objs[0] == "Tank_H"
    assert "Tank_S_body" in s_objs and "Tank_H_body" in h_objs
    assert [n for n in s_objs if n.startswith("Tank_S_leg_")] == [f"Tank_S_leg_{k:02d}" for k in range(1, 5)]
    assert sum(name.startswith("Tank_S_brace_") for name in s_objs) == 8
    assert "Tank_H_handrail" in h_objs
    assert not any(n.endswith(("_Wall", "_Roof", "_Sphere")) for n in s_objs + h_objs)
    conn = report["connector_objects"]
    assert conn[0] == "Connector_T"
    assert "Connector_T_deck" in conn and "Connector_T_support_01" in conn
    assert "Connector_T_rail_L_top" in conn and "Connector_T_post_R_01" in conn
    assert Path(report["connectors_file"]).read_bytes().startswith(b"Kaydara FBX Binary")
    # Read the merged FBX back: hierarchy and real positions survive the file.
    dump = tmp_path / "dump.json"
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import bpy, json, sys\n"
        "bpy.ops.wm.read_factory_settings(use_empty=True)\n"
        f"bpy.ops.import_scene.fbx(filepath={str(report['merged'])!r})\n"
        "bpy.context.view_layer.update()\n"
        "out = {}\n"
        "for o in bpy.data.objects:\n"
        "    v = (o.matrix_world @ o.data.vertices[0].co) if o.type == 'MESH' else o.matrix_world.translation\n"
        "    out[o.name] = {'type': o.type, 'parent': o.parent.name if o.parent else None, 'v': list(v)}\n"
        f"open({str(dump)!r}, 'w').write(json.dumps(out))\n",
        encoding="utf-8",
    )
    subprocess.run([blender, "-b", "--python-exit-code", "1", "-P", str(probe)], check=True, capture_output=True)
    scene = json.loads(dump.read_text(encoding="utf-8"))
    for group in ("Tank_S", "Tank_H", "Connector_T"):
        assert scene[group]["type"] == "EMPTY" and scene[group]["parent"] is None
    for name, rec in scene.items():
        if rec["type"] == "MESH":
            assert rec["parent"] is not None and name.startswith(rec["parent"] + "_"), name
    assert scene["Tank_S_body"]["parent"] == "Tank_S"
    assert scene["Tank_H_handrail"]["parent"] == "Tank_H"
    assert scene["Connector_T_deck"]["parent"] == "Connector_T"
    # World coordinates are the tile-local metres from the spec.
    assert abs(scene["Tank_S"]["v"][0] - x) < 0.01 and abs(scene["Tank_S"]["v"][1] - y) < 0.01
    deck_z = [rec["v"][2] for n, rec in scene.items() if n == "Connector_T_deck"][0]
    assert z - 0.3 < deck_z < z + 0.6
    leg = scene["Tank_S_leg_01"]["v"]
    assert abs(((leg[0] - x) ** 2 + (leg[1] - y) ** 2) ** 0.5 - 5.8) < 0.4
    for item in report["files"]:
        assert item["reimport_vertex"] is not None
