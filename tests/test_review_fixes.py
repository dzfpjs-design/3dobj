"""Review items from the 2026-11-21 checklist. No tank ids are taken from the screenshots."""

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from tankextract.deliverable import (
    export_name_for,
    face_budget_row,
    handrail_gaps,
    is_door,
    latin_part,
    plan_shell,
    polygon_counts,
    seat_walkway_ends,
    tessellation_segments,
)
from tankextract.structures import polyline_rods, stair_tower_parts, tube, wall_stair_parts
from tests.test_export_geometry import _module


class _Tank:
    def __init__(self, name, x, y, radius, platform=None, rail=None):
        self.name = name
        self.center_x = x
        self.center_y = y
        self.radius = radius
        self.handrail_height = rail
        self.accessories = {"roof": {"platform": platform}} if platform else {}


class _Conn:
    def __init__(self):
        self.kind = "walkway"
        self.tank_a = "Tank_001"
        self.tank_b = "Tank_002"
        self.x0, self.y0, self.z0 = 10.0, 0.0, 12.0
        self.x1, self.y1, self.z1 = 0.0, 0.0, 12.0
        self.handrail_height = 1.2
        self.handrail_height_measured = None
        self.rail_source = "measured"


def test_precision_sets_shell_chord_and_not_the_rail():
    # quality full stays on the requested segment count.
    assert tessellation_segments(96, 0.50, 2000) == 96
    assert tessellation_segments(96, 0.10, 2000) == 96
    # A large shell gets more sides when the chord target tightens from 50 cm to 10 cm.
    coarse = tessellation_segments(96, 0.50, 8000, quality="budget", radius=40, roof="flat")
    fine = tessellation_segments(96, 0.10, 8000, quality="budget", radius=40, roof="flat")
    assert 16 <= coarse < fine <= 96
    mesh = _module()
    limits_coarse = {"quality": "budget", "precision_m": 0.50, "max_faces": 8000}
    limits_fine = {"quality": "budget", "precision_m": 0.10, "max_faces": 8000}
    assert mesh._shell_plan(limits_coarse, 40, 96, "flat")["segments"] == coarse
    assert mesh._shell_plan(limits_fine, 40, 96, "flat")["segments"] == fine
    rail_a = mesh._ring_pipe(0, 0, 1, 5, 0.025, [], circ=12)
    rail_b = mesh._ring_pipe(0, 0, 1, 5, 0.025, [], circ=12)
    assert len(rail_a[0]) == len(rail_b[0])
    row = face_budget_row("LA0001", 2500, 2000)
    assert row["exceeded"] and row["kept"] and row["polygons"] == 2500


def test_export_name_keeps_tank_number_and_door_is_conditional():
    assert export_name_for("Tank_007") == "LA0007"
    assert export_name_for("Tank_007", "LA") == "LA0007"
    assert export_name_for("Connector_003") == "CN0003"
    assert is_door("hatch") is False
    assert "door" in latin_part("西门")
    assert "door" not in latin_part("body")


def test_bridge_rail_uses_measured_platform_height_only():
    # Numbers from the delivered E sphere FBX index, not a new constant.
    a = _Tank("Tank_015", 0, 0, 7.9, platform={"radius": 2.0, "z": 67.0, "rail_height": 1.32})
    b = _Tank("Tank_017", 20, 0, 7.9, platform={"radius": 2.1, "z": 67.0, "rail_height": 1.33})
    conn = _Conn()
    conn.tank_a, conn.tank_b = "Tank_015", "Tank_017"
    conn.handrail_height = 1.041
    conn.rail_source = "measured"
    seat_walkway_ends([a, b], [conn])
    assert abs(conn.handrail_height - 1.33) < 1e-6
    assert abs(conn.handrail_height_measured - 1.041) < 1e-6
    assert conn.rail_source == "measured_platform_rail"
    # No platform rail measurement: keep the walkway's own measurement.
    bare = _Conn()
    seat_walkway_ends([_Tank("Tank_019", 0, 0, 2.7), _Tank("Tank_022", 8, 0, 2.7)], [bare])
    assert bare.handrail_height == 1.2
    assert bare.handrail_height_measured is None


def test_walkway_meets_measured_platform_without_changing_rail_height():
    a = _Tank("Tank_001", 0, 0, 5, platform={"radius": 4.0, "z": 11.5, "rail_height": 1.05})
    b = _Tank("Tank_002", 20, 0, 5)
    conn = _Conn()
    seat_walkway_ends([a, b], [conn])
    # End B is already at the second tank's centre distance; end A is pulled in to the platform.
    assert abs(math.hypot(conn.x0, conn.y0) - 4.0) < 1e-6
    assert abs(conn.z0 - 11.5) < 1e-6
    assert conn.handrail_height == 1.2
    bare = _Conn()
    seat_walkway_ends([_Tank("Tank_001", 0, 0, 5), _Tank("Tank_002", 20, 0, 5)], [bare])
    assert (bare.x0, bare.y0, bare.z0) == (10.0, 0.0, 12.0)


def test_handrail_is_a_pipe_and_opens_only_at_a_measured_stair():
    mesh = _module()
    solid_span = 1.1
    verts, _faces = mesh._ring_pipe(0, 0, 10.0 + solid_span, 5.0, 0.025, [])
    zs = [v[2] for v in verts]
    assert max(zs) - min(zs) < 0.08  # pipe thickness, not a solid wall of rail height
    radii = [math.hypot(v[0], v[1]) for v in verts]
    assert max(radii) - min(radii) < 0.08
    gapped, _ = mesh._ring_pipe(0, 0, 11.1, 5.0, 0.025, [(0.0, 12.0)])
    assert gapped
    assert all(not (abs(math.degrees(math.atan2(v[1], v[0])) % 360.0) < 4.0) for v in gapped)


def test_shell_ring_is_a_pipe_not_a_plate():
    mesh = _module()
    verts, _faces = mesh._ring_pipe(0, 0, 6.0, 5.1, 0.1, [])
    zs = [v[2] for v in verts]
    assert max(zs) - min(zs) < 0.25
    radii = [math.hypot(v[0], v[1]) for v in verts]
    assert min(radii) > 4.9 and max(radii) < 5.3


def test_rail_run_is_one_tube_and_stair_landing_reaches_the_wall():
    parts = polyline_rods("rail", [(0, 0, 1), (1, 0, 1), (2, 0, 1)], 0.025)
    assert len(parts) == 1 and parts[0]["type"] == "tube" and len(parts[0]["points"]) == 3
    assert tube("x", [(0, 0, 0), (1, 0, 0)], 0.02)["type"] == "tube"

    class Tank:
        center_x = 0.0
        center_y = 0.0
        radius = 6.0
        z_base = 0.0
        wall_height = 12.0

    stair = {
        "az_bottom_deg": 0.0, "az_top_deg": 90.0, "z_bottom": 0.0, "z_top": 12.0,
        "inner_radius": 6.8, "outer_radius": 7.6, "width": 0.8, "direction": "ccw", "sweep_deg": 90.0,
    }
    landing = [p for p in wall_stair_parts("Tank_T", Tank(), stair) if p["name"].endswith("stair_landing")][0]
    # Box width is the radial span; its inner face is the centre minus half of that span.
    inner = math.hypot(landing["center"][0], landing["center"][1]) - landing["size"][1] / 2
    assert inner < Tank.radius + 0.05


def test_stair_tower_top_opens_toward_the_bridge():
    closed = stair_tower_parts({
        "x": 0, "y": 0, "length": 4, "width": 3, "axis_deg": 0, "z_ground": 0, "z_top": 6,
        "levels": [3, 6], "rise": 3, "rail_height": 1.1, "open_sides": [],
    }, "Stair_T")
    opened = stair_tower_parts({
        "x": 0, "y": 0, "length": 4, "width": 3, "axis_deg": 0, "z_ground": 0, "z_top": 6,
        "levels": [3, 6], "rise": 3, "rail_height": 1.1, "open_sides": [0],
    }, "Stair_T")
    deck = lambda parts: [p for p in parts if p["name"].endswith("top_deck")][0]
    assert len(deck(opened)["polygon"]) == 4
    span = lambda poly, axis: max(p[axis] for p in poly) - min(p[axis] for p in poly)
    assert span(deck(opened)["polygon"], 1) < span(deck(closed)["polygon"], 1) - 0.5


def _draw_mesh(draw, verts, faces, color, origin, scale):
    pts = [((origin[0] + v[0] * scale), (origin[1] - v[2] * scale)) for v in verts]
    for face in faces:
        loop = [pts[i] for i in face]
        draw.line(loop + [loop[0]], fill=color, width=1)


def test_budget_lowers_shell_faces_and_keeps_the_rail(tmp_path):
    mesh = _module()
    budget = {"quality": "budget", "max_faces": 2000}
    for roof, radius in (("flat", 8.0), ("dome", 12.0), ("sphere", 6.0)):
        planned = plan_shell(radius, 96, 2000, roof, "budget", 0.50)
        mirrored = mesh._shell_plan(budget, radius, 96, roof)
        assert planned["segments"] == mirrored["segments"]
        assert planned["rings"] == mirrored["rings"]
    full_body = mesh._closed_body(0, 0, 0, 12, 8, "dome", 1.5, 96, 12)
    dome = mesh._shell_plan(budget, 8, 96, "dome")
    budget_body = mesh._closed_body(0, 0, 0, 12, 8, "dome", 1.5, dome["segments"], dome["rings"])
    full_poly, full_tri = polygon_counts(full_body[1])
    budget_poly, budget_tri = polygon_counts(budget_body[1])
    assert budget_poly < full_poly * 0.5
    assert budget_tri < full_tri * 0.5
    full_sphere = mesh._uv_sphere(30, 0, 6, 6, 96, 48)
    sphere = mesh._shell_plan(budget, 6, 96, "sphere")
    budget_sphere = mesh._uv_sphere(30, 0, 6, 6, sphere["segments"], sphere["rings"])
    assert polygon_counts(budget_sphere[1])[0] < polygon_counts(full_sphere[1])[0] * 0.25
    # Same rail, girder and opening exist after the shell is coarsened.
    circ = 12
    rail, rail_faces = mesh._ring_pipe(0, 0, 12.0, 7.9, 0.025, [(40.0, 12.0)], circ=circ)
    assert rail and max(v[2] for v in rail) - min(v[2] for v in rail) < 0.08
    assert all(abs(((math.degrees(math.atan2(v[1], v[0])) - 40.0 + 180) % 360) - 180) > 4 for v in rail)
    girder, _faces = mesh._ring_pipe(0, 0, 6.0, 8.15, 0.1, [], circ=circ)
    radii = [math.hypot(v[0], v[1]) for v in girder]
    assert min(radii) > 8.0 and max(radii) < 8.4
    loose = mesh._shell_plan({"quality": "budget", "precision_m": 0.05, "max_faces": 5000}, 15, 96, "sphere")["segments"]
    tight = mesh._shell_plan({"quality": "budget", "precision_m": 0.05, "max_faces": 180}, 15, 96, "sphere")["segments"]
    assert tight < loose
    assert mesh._shell_plan({"quality": "full", "max_faces": 180}, 15, 96, "sphere")["segments"] == 96
    image = Image.new("RGB", (960, 420), (24, 26, 28))
    draw = ImageDraw.Draw(image)
    draw.text((24, 12), f"full dome {full_poly} polys / {full_tri} tris", fill=(220, 220, 220))
    draw.text((500, 12), f"budget dome {budget_poly} polys / {budget_tri} tris", fill=(220, 220, 220))
    full_rail = mesh._ring_pipe(0, 0, 12.0, 7.9, 0.04, [(40.0, 12.0)], circ=16)
    _draw_mesh(draw, full_body[0], full_body[1], (180, 190, 200), (220, 360), 16)
    _draw_mesh(draw, full_rail[0], full_rail[1], (80, 220, 140), (220, 360), 16)
    budget_rail = (rail, rail_faces)
    _draw_mesh(draw, budget_body[0], budget_body[1], (180, 190, 200), (700, 360), 16)
    _draw_mesh(draw, budget_rail[0], budget_rail[1], (80, 220, 140), (700, 360), 16)
    path = tmp_path / "face-budget-compare.png"
    image.save(path)
    assert path.stat().st_size > 1000
    art = Path("/opt/cursor/artifacts")
    if art.is_dir():
        image.save(art / "face-budget-compare.png")


def test_sphere_search_accepts_a_low_cylinder_fit_and_rejects_a_dome_cap():
    from tankextract.detect import _round_section, _sphere_check

    rng = np.random.default_rng(0)
    count = 5000
    theta = 2 * np.pi * rng.random(count)
    phi = np.arccos(2 * rng.random(count) - 1)
    radius = 8.0
    sphere = np.column_stack([
        radius * np.sin(phi) * np.cos(theta),
        radius * np.sin(phi) * np.sin(theta),
        20.0 + radius * np.cos(phi),
    ])
    found = _sphere_check(sphere, np.zeros(2), 7.4, 10.0, 28.0)
    assert found["sphere"]
    assert abs(found["radius"] - 8.0) < 0.35
    wall = []
    for z in np.linspace(0.0, 12.0, 40):
        ang = np.linspace(0, 2 * np.pi, 80, endpoint=False)
        wall.append(np.column_stack([8 * np.cos(ang), 8 * np.sin(ang), np.full(80, z)]))
    cap_z = 12.0 + 1.6 * np.sqrt(np.clip(1 - (np.linspace(0, 0.95, 12) ** 2), 0, 1))
    cap = []
    for zz, frac in zip(cap_z, np.linspace(0, 0.95, 12)):
        ang = np.linspace(0, 2 * np.pi, 80, endpoint=False)
        cap.append(np.column_stack([8 * frac * np.cos(ang), 8 * frac * np.sin(ang), np.full(80, zz)]))
    dome = _sphere_check(np.vstack(wall + cap), np.zeros(2), 8.0, 0.0, 12.0)
    assert not dome["sphere"]
    ang = rng.random(800) * 2 * np.pi
    section = _round_section(0.32 + 0.12 * np.cos(ang), 10.0 + 0.12 * np.sin(ang))
    assert abs(section["diameter"] - 0.24) < 0.03
    assert abs(section["diameter"] - 0.44) > 0.1
    plate = _round_section(rng.uniform(0.05, 0.50, 800), rng.uniform(9.92, 10.08, 800))
    assert plate is None
    from tankextract.detect import _dense_core

    core_ang = rng.random(600) * 2 * np.pi
    core = _dense_core(0.55 + 0.07 * np.cos(core_ang), 10.0 + 0.07 * np.sin(core_ang))
    assert core and abs(core["diameter"] - 0.14) < 0.08
    assert _dense_core(rng.uniform(0.05, 0.55, 800), rng.uniform(9.92, 10.08, 800)) is None


def test_opening_width_follows_the_measurement_on_a_coarse_ring():
    mesh = _module()
    ring_r = 8.5
    width = 0.80
    half = math.degrees(width / (2.0 * ring_r))
    verts, _faces = mesh._ring_pipe(0, 0, 1.0, ring_r, 0.03, [(10.0, half)], circ=12)
    angles = [
        math.degrees(math.atan2(v[1], v[0])) % 360.0
        for v in verts
        if abs(math.hypot(v[0], v[1]) - ring_r) < 0.05
    ]
    assert angles
    assert all(abs(((angle - 10.0 + 180.0) % 360.0) - 180.0) >= half - 0.6 for angle in angles)
    edges = sorted(abs(((angle - 10.0 + 180.0) % 360.0) - 180.0) for angle in angles)
    assert edges[0] < half + 0.6
    arc = ring_r * math.radians(2.0 * half)
    assert abs(arc - width) < 0.05


def test_girder_pipe_uses_the_fitted_diameter_only():
    mesh = _module()
    pieces = []
    mesh._append_girders(
        pieces, 0, 0, 8.0,
        [{"z": 6.0, "protrusion": 0.50, "diameter": 0.24, "center_rho": 0.32}],
        {"quality": "budget"},
    )
    radii = [math.hypot(v[0], v[1]) for v in pieces[0][1]]
    assert min(radii) > 8.0 + 0.32 - 0.14
    assert max(radii) < 8.0 + 0.32 + 0.14
    skipped = []
    mesh._append_girders(skipped, 0, 0, 8.0, [{"z": 6.0, "protrusion": 0.50}], {"quality": "budget"})
    assert skipped == []


def test_a_local_spike_is_not_a_ring_rail():
    from tankextract.detect import _upper_ring_coverage

    radius = 3.0
    ring = []
    for step in range(24):
        ang = 2 * math.pi * step / 24
        ring.append([radius * math.cos(ang), radius * math.sin(ang), 12.2])
    covered = _upper_ring_coverage({"all": np.asarray(ring)}, np.zeros(2), radius, 11.5, 12.5)
    assert covered >= 200
    local = np.asarray([[radius, 0.0, 12.4], [radius + 0.1, 0.2, 12.5], [radius - 0.1, -0.1, 12.3]])
    assert _upper_ring_coverage({"all": local}, np.zeros(2), radius, 11.5, 12.5) < 200


def test_bridge_end_is_compared_with_the_measured_roof_not_a_cutoff():
    tank = _Tank("Tank_001", 0, 0, 3.0)
    tank.z_base = 50.0
    tank.wall_height = 10.0
    tank.total_height = 11.0
    tank.roof_type = "cone"
    # Measured plate is 0.37 m above the straight cone at this radius.
    tank.roof_profile_z = [62.5, 62.4, 62.39, 62.3, 62.2, 62.1, 62.0, 61.9, 61.8, 61.7]
    conn = _Conn()
    conn.x0, conn.y0, conn.z0 = 0.91, 0.0, 62.48
    seat_walkway_ends([tank, _Tank("Tank_002", 20, 0, 5)], [conn])
    ends = {row["end"]: row for row in conn.junctions}
    assert ends["a"]["platform"] == "not_measured"
    assert ends["a"]["confidence"] == "measured_roof_profile"
    assert abs(ends["a"]["dz_measured_m"] - (62.48 - 62.3)) < 0.02
    assert ends["a"]["dz_formula_m"] > 0.3
    assert ends["a"]["relation"] == "deck_above_measured_roof"
    assert ends["a"]["accepted"] is False


def test_budget_tubes_stay_round_unless_sides_are_set():
    mesh = _module()
    part = {"name": "T_rail", "type": "tube", "points": [[0, 0, 0], [1, 0, 0]], "radius": 0.025, "segments": 8}
    default = mesh._part_pieces([part], "T", {"quality": "budget"})
    coarse = mesh._part_pieces([part], "T", {"quality": "budget", "tube_sides": 4})
    assert len(coarse[0][2]) < len(default[0][2])
    assert len(default[0][2]) >= 8


def test_parent_tile_is_not_meshed_when_its_child_is_present():
    from tankextract.pipeline import use_tile_mesh

    assert use_tile_mesh([]) is True
    assert use_tile_mesh(["Tile_L22_child.osgb"]) is False


def test_inside_the_shell_is_not_a_platform_connection():
    tank = _Tank("Tank_001", 0, 0, 8.0)
    conn = _Conn()
    conn.x0, conn.y0, conn.z0 = 7.5, 0.0, 12.0
    seat_walkway_ends([tank, _Tank("Tank_002", 20, 0, 5)], [conn])
    ends = {row["end"]: row for row in conn.junctions}
    assert ends["a"]["platform"] == "not_measured"
    assert ends["a"]["inside_shell_m"] > 0
    seated = _Conn()
    seat_walkway_ends([
        _Tank("Tank_001", 0, 0, 5, platform={"radius": 4.0, "z": 11.5, "rail_height": 1.05}),
        _Tank("Tank_002", 20, 0, 5),
    ], [seated])
    ends = {row["end"]: row for row in seated.junctions}
    assert ends["a"]["platform"] == "meets_platform"
    assert ends["b"]["platform"] == "not_measured"


def test_finest_lod_keeps_a_coarser_leaf_beside_a_deeper_one():
    from tankextract.pipeline import _select_tiles

    names = ["Tile/Tile_L21_0uuuu734.osgb", "Tile/Tile_L22_0uuuu6302.osgb"]
    assert set(_select_tiles(names, "finest")) == set(names)
    assert _select_tiles(names, "22") == ["Tile/Tile_L22_0uuuu6302.osgb"]


def test_sphere_platform_uses_the_wider_deck_and_skips_an_unmeasured_rail():
    from types import SimpleNamespace

    from tankextract.structures import platform_parts, roof_platform

    rng = np.random.default_rng(0)
    rows = []
    for _ in range(500):
        ang = rng.random() * 2 * np.pi
        radius = 0.65 * np.sqrt(rng.random())
        rows.append([radius * np.cos(ang), radius * np.sin(ang), 10.0, 0, 0, 1, 0.04])
    for radius in np.arange(0.8, 2.35, 0.15):
        for step in range(36):
            ang = 2 * np.pi * step / 36
            rows.append([radius * np.cos(ang), radius * np.sin(ang), 10.18, 0, 0, 1, 0.04])
    tank = SimpleNamespace(
        center_x=0.0, center_y=0.0, radius=8.0, z_base=0.0, total_height=10.0,
        sphere={"center_z": 2.0, "radius": 8.0},
    )
    info = roof_platform(np.asarray(rows, float), tank)
    assert info["platform"]["radius"] > 1.8
    assert info["platform"]["rail_height"] is None
    parts = platform_parts("T", tank, info)
    assert any(part["name"].endswith("_platform") for part in parts)
    assert not any("platform_rail" in part["name"] for part in parts)


def test_handrail_gaps_come_only_from_a_measured_stair():
    class Bare:
        accessories = {}
        spiral_stair = None

    class WithStair:
        accessories = {"wall_stair": {"az_top_deg": 40.0, "width": 0.9}}
        spiral_stair = None

    assert handrail_gaps(Bare()) == []
    gaps = handrail_gaps(WithStair())
    assert len(gaps) == 1 and abs(gaps[0]["azimuth_deg"] - 40.0) < 1e-6
    assert not np.isnan(gaps[0]["width_m"])


def test_a_solid_shell_band_is_wall_not_a_handrail():
    from tankextract.detect import _band_is_solid_shell, _handrail_from_band

    radius = 3.0
    rows = []
    for z in np.arange(10.2, 11.2, 0.2):
        for step in range(36):
            ang = 2 * math.pi * step / 36
            rows.append([radius * math.cos(ang), radius * math.sin(ang), z])
    samples = {"all": np.asarray(rows)}
    assert _band_is_solid_shell(samples, np.zeros(2), radius, 10.2, 11.1)
    height, kept = _handrail_from_band(samples, np.zeros(2), radius, 10.0, 11.1)
    assert height is None and kept == 11.1
    rail = [[radius * math.cos(a), radius * math.sin(a), 11.05] for a in np.linspace(0, 2 * math.pi, 36, endpoint=False)]
    rail_samples = {"all": np.asarray(rail)}
    assert not _band_is_solid_shell(rail_samples, np.zeros(2), radius, 10.2, 11.1)
    height, kept = _handrail_from_band(rail_samples, np.zeros(2), radius, 10.0, 11.1)
    assert height is not None and abs(height - 1.1) < 1e-6
    assert kept == 10.0
    # A plate at the top of a solid band is the wall, even if the band goes around.
    height, kept = _handrail_from_band(samples, np.zeros(2), radius, 10.0, 11.1, plate_z=11.05)
    assert height is None and kept == 11.1
    # A plate on the eave leaves the ring above it as a rail.
    height, kept = _handrail_from_band(samples, np.zeros(2), radius, 10.0, 11.1, plate_z=10.05)
    assert height is not None and kept == 10.0


def test_bridge_stops_at_the_wall_when_the_roof_plate_continues():
    from types import SimpleNamespace

    from tankextract.connectors import _extend

    # Deck runs along +x toward a tank at x=10. The roof plate fills a wide
    # band inside the shell at the same height as the deck.
    radius = 3.0
    center = 10.0
    xs = np.linspace(5.5, 9.6, 40)
    deck = np.column_stack([xs, np.zeros(len(xs)), np.full(len(xs), 20.0), np.ones(len(xs)), np.full(len(xs), 0.2)])
    roof_x = np.linspace(7.2, 10.0, 30)
    roof_y = np.linspace(-1.4, 1.4, 12)
    roof = np.array([[x, y, 20.0, 1.0, 0.3] for x in roof_x for y in roof_y])
    faces = np.vstack([deck, roof])
    tank = SimpleNamespace(
        center_x=center, center_y=0.0, radius=radius, sphere=None,
        roof_profile_z=[20.0] * 10, z_base=8.0, wall_height=12.0, total_height=12.2, roof_type="cone",
    )
    # s is distance from x=0, so the wall is at s=7. Start just outside it.
    pos, reason = _extend(
        6.4, 0.5, 12.0, np.ones(len(faces), dtype=bool), faces[:, 0], faces[:, 1], faces[:, 2], faces[:, 4],
        np.array([0.0, 20.0]), tank, np.zeros(2), np.array([1.0, 0.0]), 0.5,
    )
    assert "at_wall" in reason
    assert abs(pos - (center - radius)) < 0.2


def test_corrections_do_not_invent_a_missing_size():
    from types import SimpleNamespace

    from tankextract.corrections import apply_corrections

    tank = SimpleNamespace(name="Tank_028", export_name="LA0007", handrail_height=None, handrail_source="")
    conn = SimpleNamespace(
        name="Connector_004", export_name="CN0004", x0=0, y0=0, z0=1, x1=2, y1=0, z1=1,
        handrail_height=None, rail_source="", supports=[], end_sources={},
    )
    apply_corrections([tank], [conn], {
        "tanks": {"Tank_028": {"source": "unmeasured", "handrail_height_m": 1.05}},
        "connectors": {"Connector_004": {"source": "manual", "supports": [{"x": 1}]}},
    }, log=lambda *_a, **_k: None)
    assert tank.handrail_height is None
    assert conn.supports == []
    apply_corrections([tank], [conn], {
        "tanks": {"LA0007": {"source": "manual", "handrail_height_m": 1.05}},
        "connectors": {"CN0004": {"source": "assumed", "ends": {"a": {"z": 62.4}}}},
    }, log=lambda *_a, **_k: None)
    assert tank.handrail_height == 1.05 and tank.handrail_source == "manual"
    assert conn.z0 == 62.4 and conn.end_sources["a"] == "assumed"


def test_local_deck_is_kept_apart_from_the_straight_line_and_the_roof():
    from tankextract.connectors import _local_deck_z
    from tankextract.deliverable import apply_junction_rule, classify_end_fit

    s = np.array([3.2, 3.4, 3.6, 3.8, 6.0, 6.2])
    z = np.array([62.15, 62.12, 62.18, 62.20, 63.4, 63.5])
    lateral = np.zeros(len(s))
    near = np.ones(len(s), dtype=bool)
    local = _local_deck_z(3.0, 1.0, s, lateral, z, near, 0.0, 0.6)
    assert abs(local - 62.16) < 0.05
    assert classify_end_fit(62.40, local, 62.10) == "line_bias"
    assert classify_end_fit(63.4, local, 62.10) == "local_conflict"
    assert classify_end_fit(62.65, 62.60, 62.10) == "source_step"
    assert classify_end_fit(62.12, 62.15, 62.10) == "within_0.15_not_a_pass"
    tank = _Tank("Tank_001", 0, 0, 3.0)
    tank.z_base = 50.0
    tank.wall_height = 10.0
    tank.total_height = 12.0
    tank.roof_type = "cone"
    tank.roof_profile_z = [62.1] * 10
    conn = _Conn()
    conn.end_sources = {}
    conn.x0, conn.y0, conn.z0 = 3.0, 0.0, 62.6
    assert apply_junction_rule([tank], [conn], "none") == 0
    assert conn.z0 == 62.6
    assert apply_junction_rule([tank], [conn], "seat_to_measured_roof") == 1
    assert abs(conn.z0 - 62.1) < 0.05
    assert conn.z0_before_rule == 62.6
    assert conn.end_sources["a"] == "rule:seat_to_measured_roof"


def test_manual_roof_seat_keeps_the_measured_deck():
    from tankextract.corrections import apply_corrections
    from tankextract.deliverable import _junctions

    tank = _Tank("Tank_001", 0, 0, 3.0)
    tank.z_base = 50.0
    tank.wall_height = 10.0
    tank.total_height = 12.0
    tank.roof_type = "cone"
    tank.roof_profile_z = [62.1] * 10
    other = _Tank("Tank_002", 20, 0, 3.0)
    other.z_base = 50.0
    other.wall_height = 10.0
    other.total_height = 12.0
    other.roof_type = "cone"
    other.roof_profile_z = [62.1] * 10
    conn = _Conn()
    conn.name = "Connector_001"
    conn.end_sources = {}
    conn.end_fit = {"a": {"line_z": 62.65, "local_z": 62.60, "used": "local"}, "b": {}}
    conn.x0, conn.y0, conn.z0 = 3.0, 0.0, 62.60
    conn.x1, conn.y1, conn.z1 = 17.0, 0.0, 62.60
    apply_corrections([tank, other], [conn], {
        "connectors": {"Connector_001": {"source": "manual", "ends": {"a": {"rule": "seat_to_measured_roof"}}}},
    }, log=lambda *_a, **_k: None)
    assert conn.x0 == 3.0 and conn.y0 == 0.0
    assert abs(conn.z0 - 62.1) < 0.05
    assert conn.z0_before_rule == 62.60
    assert conn.z1 == 62.60
    ends = {row["end"]: row for row in _junctions(conn, {"Tank_001": tank, "Tank_002": other})}
    assert ends["a"]["relation"] == "manual_seated_on_measured_roof"
    assert ends["a"]["source_mesh"] is False
    assert ends["a"]["deck_z_before_rule_m"] == 62.60
    assert abs(ends["a"]["dz_before_rule_m"] - 0.5) < 0.05
    assert ends["a"]["seat_rule"] == "manual:seat_to_measured_roof"
    assert ends["b"]["seat_rule"] == "off"
    assert ends["b"]["source_mesh"] is True


def test_kept_detail_on_tank_031_already_exceeds_2000():
    from tankextract.deliverable import detail_floor

    # Counts from the 1.3.6 LA0002 export. Treads, rails and round tubes only.
    rows = [
        {"object": "LA0002_handrail", "polygons": 562},
        {"object": "LA0002_stair_treads", "polygons": 516},
        {"object": "LA0002_stair_rail", "polygons": 256},
        {"object": "LA0002_body", "polygons": 160},
        {"object": "LA0002_stair_stringer_inner", "polygons": 126},
        {"object": "LA0002_stair_stringer_outer", "polygons": 126},
        {"object": "LA0002_wind_girder_01", "polygons": 96},
        {"object": "LA0002_wind_girder_02", "polygons": 96},
        {"object": "LA0002_platform_rail_top", "polygons": 128},
        {"object": "LA0002_platform_rail_mid", "polygons": 128},
        {"object": "LA0002_platform_rail_posts", "polygons": 90},
    ]
    floor = detail_floor(rows)
    assert floor["above_2000"]
    assert floor["polygons"] > 2000
    assert floor["polygons"] == 2124


def test_post_keeps_a_round_section_without_end_caps():
    mesh = _module()
    capped = mesh._rod((0, 0, 0), (0, 0, 1), 0.025, 8, caps=True)
    open_post = mesh._rod((0, 0, 0), (0, 0, 1), 0.025, 8, caps=False)
    assert len(capped[1]) == 10
    assert len(open_post[1]) == 8
    radii = [math.hypot(v[0], v[1]) for v in open_post[0]]
    assert max(radii) < 0.03 and min(radii) > 0.02


def test_missing_walkway_rail_is_not_filled_with_the_site_median():
    from types import SimpleNamespace

    from tankextract.pipeline import _record_unmeasured_rails

    seen = SimpleNamespace(name="Connector_001", kind="walkway", handrail_height=1.1, rail_source="measured")
    missing = SimpleNamespace(name="Connector_002", kind="walkway", handrail_height=None, rail_source="")
    median = _record_unmeasured_rails([seen, missing], log=lambda *_a, **_k: None)
    assert abs(median - 1.1) < 1e-9
    assert missing.handrail_height is None
