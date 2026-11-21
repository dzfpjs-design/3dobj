"""Review items from the 2026-11-21 checklist. No tank ids are taken from the screenshots."""

import math

import numpy as np

from tankextract.deliverable import (
    export_name_for,
    face_budget_row,
    handrail_gaps,
    is_door,
    latin_part,
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


def test_precision_and_face_budget_do_not_drop_detail():
    assert tessellation_segments(96, 0.50, 2000) == 96
    row = face_budget_row("LA0001_body", 2500, 2000)
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
