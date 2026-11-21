"""Roof-to-roof walkways between tanks: found, measured, exported."""

import numpy as np

from tankextract.connectors import find_connectors
from tankextract.detect import DetectConfig, SceneAccumulator, refine_candidates
from tankextract.synthetic import _concat, _cylinder, _disk, _terrain

ORIGIN = (629024.0, 3586203.0, 0.0)
GROUND = 7.5


def _rails(x0, x1, y, z, width, rail, n=60, nh=8):
    """Two vertical railing strips along x at y +- width/2 (finely meshed)."""
    parts = []
    for off in (-width / 2, width / 2):
        verts, tris = [], []
        for k in range(n + 1):
            x = x0 + (x1 - x0) * k / n
            for h in range(nh + 1):
                verts.append((x, y + off, z + rail * h / nh))
        for k in range(n):
            for h in range(nh):
                a = k * (nh + 1) + h
                b, c, d = a + 1, a + nh + 1, a + nh + 2
                tris += [(a, c, b), (b, c, d)]
        parts.append((np.asarray(verts, float), np.asarray(tris, np.int64)))
    return _concat(parts)


def _plate(x0, x1, y0, y1, z, up=True, nx=36, ny=10):
    """Finely triangulated horizontal plate (normals up or down)."""
    xs = np.linspace(x0, x1, nx + 1)
    ys = np.linspace(y0, y1, ny + 1)
    verts = [(x, y, z) for y in ys for x in xs]
    tris = []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            b, c, d = a + 1, a + nx + 1, a + nx + 2
            tris += [(a, b, d), (a, d, c)] if up else [(a, d, b), (a, c, d)]
    return np.asarray(verts, float), np.asarray(tris, np.int64)


def _scene():
    parts = [_terrain()]
    roof = GROUND + 12.0
    for cx, cy in ((20.0, 20.0), (33.0, 20.0), (20.0, 34.0)):
        parts.append(_cylinder(cx, cy, GROUND, roof, 5.0, n=160, nh=24))
        parts.append(_disk(cx, cy, roof, 5.0, n=160))
    # Walkway A-B: 1.0 m wide, 0.25 m deck, 1.1 m railing, one column in the gap.
    deck = roof + 0.3
    parts.append(_plate(24.7, 28.3, 19.5, 20.5, deck, up=True))
    parts.append(_plate(24.7, 28.3, 19.5, 20.5, deck - 0.25, up=False))
    parts.append(_rails(24.7, 28.3, 20.0, deck, 1.0, 1.1))
    parts.append(_cylinder(26.5, 20.0, GROUND, deck - 0.25, 0.2, n=16, nh=30))
    # A pipe-rack deck between A and C, 2 m below the roofs: not a roof link.
    rack = roof - 2.0
    parts.append(_plate(19.5, 20.5, 24.7, 29.3, rack, up=True, nx=10, ny=36))
    parts.append(_plate(19.5, 20.5, 24.7, 29.3, rack - 0.2, up=False, nx=10, ny=36))
    return _concat(parts)


def _tanks_and_tiles():
    vertices, triangles = _scene()
    config = DetectConfig()
    acc = SceneAccumulator(config)
    acc.add_mesh("t", vertices, triangles, ORIGIN)
    absolute = vertices + np.array(ORIGIN)
    tiles = [(absolute, triangles)]
    tanks = refine_candidates(acc.candidates(), tiles, config, acc.ground_near, "T", "EPSG:4548", ORIGIN)
    return tanks, tiles, acc


def test_walkway_between_roofs_is_measured():
    tanks, tiles, acc = _tanks_and_tiles()
    assert len(tanks) == 3
    debug = []
    links = find_connectors(tanks, tiles, acc.ground_near, debug)
    assert len(links) == 1, debug
    reasons = {(d["tank_a"], d["tank_b"]): d["reason"] for d in debug}
    assert "below roof level (pipe rack / side platform)" in reasons.values()
    link = links[0]
    ends = sorted(round(t.center_x - ORIGIN[0]) for t in link.tank_refs)
    assert ends == [20, 33]
    assert link.kind == "walkway"
    assert abs(link.width - 1.0) < 0.15
    assert abs(link.depth - 0.25) < 0.1
    assert link.handrail_height is not None and abs(link.handrail_height - 1.1) < 0.15
    deck = GROUND + 12.3
    assert abs(link.z0 - deck) < 0.1 and abs(link.z1 - deck) < 0.1
    assert abs(link.y0 - ORIGIN[1] - 20.0) < 0.15
    # Ends at the two roof edges (25.3 .. 27.7 plus at most a step over a roof).
    assert 24.0 < min(link.x0, link.x1) - ORIGIN[0] < 25.6
    assert 27.4 < max(link.x0, link.x1) - ORIGIN[0] < 29.0
    assert len(link.supports) == 1
    sp = link.supports[0]
    assert abs(sp["x"] - ORIGIN[0] - 26.5) < 0.3
    assert abs(sp["z_bottom"] - GROUND) < 0.3
    assert sp["length"] < 1.0 and sp["width"] < 1.0
