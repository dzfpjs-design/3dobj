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
