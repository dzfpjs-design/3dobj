"""Offline tree-crown filter: texture colour from the OSGB tiles + geometric irregularity."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from tankextract.model import Tank
from tankextract.osgb_reader import read_osgb_file
from tankextract.pipeline import _apply_tree_filter, _write_tree_report
from tankextract.treefilter import colour_stats, green_mask, judge, roof_roughness, sample_colours

FIX = Path(__file__).parent / "fixtures"


def _meshes(name):
    contents = read_osgb_file(str(FIX / name), textures=True)
    return [(m.vertices, m.triangles, m.uv, m.image) for m in contents.meshes]


def test_reader_keeps_texture_only_when_asked():
    plain = read_osgb_file(str(FIX / "tile_l17.osgb"))
    assert all(m.uv is None and m.image is None for m in plain.meshes)
    for name in ("tile_l17.osgb", "blockx_l19.osgb"):  # SO 91 (no brackets) and SO 131 (brackets)
        textured = read_osgb_file(str(FIX / name), textures=True)
        mesh = textured.meshes[0]
        assert mesh.uv is not None and mesh.uv.shape == (len(mesh.vertices), 2)
        assert mesh.image[:2] == b"\xff\xd8"  # embedded JPEG
        assert textured.trailing_bytes == 0
        # geometry is identical with and without textures
        np.testing.assert_allclose(mesh.vertices, read_osgb_file(str(FIX / name)).meshes[0].vertices)


def test_texture_colour_vegetation_vs_buildings():
    veg = colour_stats(sample_colours(_meshes("blockx_l19.osgb"), 0, 0, 1e9, -1e9, 1e9))
    town = colour_stats(sample_colours(_meshes("tile_l17.osgb"), 0, 0, 1e9, -1e9, 1e9))
    assert veg["samples"] > 100 and veg["green_fraction"] > 0.9
    assert town["samples"] > 100 and town["green_fraction"] < 0.1


def test_green_mask_classes():
    px = np.array([
        [72, 81, 58],    # olive foliage
        [40, 110, 45],   # bright leaves
        [135, 137, 140],  # grey steel
        [200, 200, 195],  # white paint
        [150, 110, 70],  # rust / soil
        [10, 14, 9],     # shadow
    ])
    assert green_mask(px).tolist() == [True, True, False, False, False, False]


def test_judge_needs_colour_and_irregular_geometry():
    rough = {"rms": 0.057, "wall_fill": 0.66, "roof_rms": 0.13}
    clean = {"rms": 0.035, "wall_fill": 0.95, "roof_rms": 0.012}
    green = {"samples": 500, "green_fraction": 0.95}
    grey = {"samples": 500, "green_fraction": 0.02}
    assert judge(rough, green, 2.3)[0] is True  # tree crown
    tree, why = judge(clean, green, 3.0)  # green-painted tank (BC Tank_064 type)
    assert tree is False and "绿色" in why
    assert judge(rough, grey, 2.3)[0] is False  # weathered grey tank
    # a single irregular cue is not enough
    assert judge({"rms": 0.06, "wall_fill": 0.95, "roof_rms": 0.01}, green, 2.3)[0] is False
    # no texture: never dropped, only flagged
    tree, why = judge(rough, None, 2.3)
    assert tree is False and "人工核对" in why
    assert judge(rough, {"samples": 3, "green_fraction": 1.0}, 2.3)[0] is False


def test_roof_roughness_smooth_cone_vs_lumpy_crown():
    rng = np.random.default_rng(1)
    r = 3.0
    ang = rng.uniform(0, 2 * np.pi, 800)
    rho = np.sqrt(rng.uniform(0, 1, 800)) * r * 0.9
    x, y = rho * np.cos(ang), rho * np.sin(ang)
    cone = np.column_stack([x, y, 10 + 0.3 * (r - rho)])
    crown = np.column_stack([x, y, 10 + 1.5 * (1 - (rho / r) ** 2) + rng.normal(0, 0.25, 800)])
    assert roof_roughness(cone, 0, 0, r) < 0.01
    assert roof_roughness(crown, 0, 0, r) > 0.1
    assert roof_roughness(cone[:10], 0, 0, r) is None


def _tank(name, x, geom, colour):
    t = Tank(block="A", srs="EPSG:4548", origin=(0.0, 0.0, 0.0), center_x=x, center_y=0.0, z_base=0.0,
             z_ground=0.0, radius=2.5, wall_height=6.0, total_height=7.0, roof_type="dome", roof_rise=1.0,
             rms=geom["rms"], inlier_ratio=1.0, coverage_deg=360.0, name=name)
    t.tree_check = {"geometry": geom, "colour": colour}
    return t


def test_apply_tree_filter_keeps_ids_and_drops_attached_parts(tmp_path):
    rough = {"rms": 0.057, "wall_fill": 0.66, "roof_rms": 0.13}
    clean = {"rms": 0.035, "wall_fill": 0.95, "roof_rms": 0.012}
    tanks = [
        _tank("Tank_001", 0, rough, {"samples": 400, "green_fraction": 0.97}),  # tree
        _tank("Tank_002", 20, clean, {"samples": 400, "green_fraction": 0.95}),  # green tank
        _tank("Tank_003", 40, rough, {"samples": 400, "green_fraction": 0.01}),  # grey tank
    ]
    conns = [SimpleNamespace(tank_a="Tank_001", tank_b="Tank_002"), SimpleNamespace(tank_a="Tank_002", tank_b="Tank_003")]
    structs = [SimpleNamespace(parent="Tank_001"), SimpleNamespace(parent="Tank_003")]
    rows = []
    kept, conns, structs = _apply_tree_filter(tanks, conns, structs, rows, lambda *_: None)
    assert [t.name for t in kept] == ["Tank_002", "Tank_003"]
    assert len(conns) == 1 and conns[0].tank_a == "Tank_002"
    assert [s.parent for s in structs] == ["Tank_003"]
    _write_tree_report(rows, tmp_path)
    text = (tmp_path / "tree_filter_report.csv").read_text(encoding="utf-8-sig").splitlines()
    assert text[1].startswith("A,Tank_001,") and ",True," in text[1]
    assert len(text) == 4


# Cues measured on the 来安 survey (verify_treefilter run): radius, green share, wall rms, wall fill, roof rms.
_LAIAN = {
    "A Tank_001": (2.020, 0.970, 0.0560, 0.66, 0.1013, True),
    "A Tank_002": (2.173, 0.989, 0.0577, 0.69, 0.1678, True),
    "A Tank_003": (2.369, 0.729, 0.0558, 0.64, 0.2566, True),
    "A Tank_011": (2.264, 0.868, 0.0565, 0.63, 0.1343, True),
    "A Tank_059": (2.098, 0.732, 0.0533, 0.71, 0.0989, True),
    "A Tank_167": (2.558, 0.796, 0.0560, 0.81, 0.0824, True),
    "A Tank_178": (2.878, 0.856, 0.0588, 0.60, 0.2831, True),
    "BC Tank_047": (2.189, 0.790, 0.0548, 0.61, 0.0521, True),
    "BC Tank_064 (green-painted tank)": (2.979, 0.756, 0.0445, 0.88, 0.0405, False),
    "A Tank_096 (tank beside trees)": (2.097, 0.427, 0.0506, 0.84, 0.0217, False),
    "A Tank_093 (tank beside trees)": (3.080, 0.381, 0.0566, 0.95, 0.0182, False),
}


def test_laian_reviewed_trees_and_tanks():
    for label, (radius, green, rms, fill, roof, tree) in _LAIAN.items():
        got, _ = judge({"rms": rms, "wall_fill": fill, "roof_rms": roof},
                       {"samples": 5000, "green_fraction": green}, radius)
        assert got is tree, label
