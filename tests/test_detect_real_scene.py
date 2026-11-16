"""Regressions found on the real E block: tanks linked to clutter, rooftop fans, chimneys."""

import numpy as np

from tankextract.detect import DetectConfig, SceneAccumulator, refine_candidates
from tankextract.synthetic import _box, _concat, _cylinder, _disk, _terrain

ORIGIN = (629024.0, 3586203.0, 0.0)


def _walkway(x0, y0, x1, y1, z, width=1.0, rail=1.1, n=200):
    """Vertical railings on both sides of a walkway, plus its floor."""
    direction = np.array([x1 - x0, y1 - y0], dtype=float)
    length = float(np.linalg.norm(direction))
    direction /= length
    side = np.array([-direction[1], direction[0]]) * width / 2
    parts = []
    for offset in (side, -side):
        verts = []
        tris = []
        for k in range(n + 1):
            p = np.array([x0, y0]) + direction * length * k / n + offset
            verts.append((p[0], p[1], z))
            verts.append((p[0], p[1], z + rail))
        for k in range(n):
            a, b, c, d = 2 * k, 2 * k + 1, 2 * k + 2, 2 * k + 3
            tris.append((a, c, b))
            tris.append((b, c, d))
        parts.append((np.asarray(verts, dtype=float), np.asarray(tris, dtype=np.int64)))
    return _concat(parts)


def _scene(tall_link=False):
    ground = 7.5
    parts = [_terrain()]
    # Two tanks 2 m apart, linked at the top by a walkway (and a pipe rack
    # wall touching both) so their wall cells form one component.
    for cx in (20.0, 34.0):
        parts.append(_cylinder(cx, 20.0, ground, ground + 10.0, 6.0, n=160, nh=20))
        parts.append(_disk(cx, 20.0, ground + 10.0, 6.0, n=160))
    parts.append(_walkway(14.0, 20.0, 40.0, 20.0, ground + 10.0))
    parts.append(_walkway(10.0, 12.0, 44.0, 12.0, ground, rail=3.0))
    if tall_link:
        # A full-height pipe rack wall touching both tanks: every
        # wall-extent level keeps it, so only the RANSAC split separates them.
        parts.append(_walkway(26.0, 20.0, 28.0, 20.0, ground, width=3.0, rail=10.0, n=20))
        parts.append(_walkway(20.0, 26.0, 34.0, 26.0, ground, width=0.4, rail=10.0, n=120))
    # Fan stack on a 9 m building roof, and a chimney.
    parts.append(_box(cx=60.0, cy=20.0, z0=ground, sx=14.0, sy=14.0, sz=9.0))
    parts.append(_cylinder(60.0, 20.0, ground + 9.0, ground + 12.5, 3.0, n=120, nh=6))
    parts.append(_cylinder(70.0, 40.0, ground, ground + 35.0, 2.2, n=100, nh=30))
    return _concat(parts)


def _detect(config, tall_link=False):
    vertices, triangles = _scene(tall_link)
    acc = SceneAccumulator(config)
    acc.add_mesh("t", vertices, triangles, ORIGIN)
    rough = acc.candidates()
    absolute = vertices + np.array(ORIGIN)
    return refine_candidates(rough, [(absolute, triangles)], config, acc.ground_near, "T", "EPSG:4548", ORIGIN)


import pytest


@pytest.mark.parametrize("tall_link", [False, True])
def test_linked_tanks_are_split_and_clutter_rejected(tall_link):
    tanks = _detect(DetectConfig(), tall_link)
    centres = sorted((round(t.center_x - ORIGIN[0], 1), round(t.center_y - ORIGIN[1], 1)) for t in tanks)
    assert centres == [(20.0, 20.0), (34.0, 20.0)]
    for tank in tanks:
        assert abs(tank.radius - 6.0) < 0.05
        assert abs(tank.wall_height - 10.0) < 0.2
        assert abs(tank.z_ground - 7.5) < 0.3


def test_single_component_fit_alone_misses_linked_tanks():
    config = DetectConfig(wall_extent_levels=(0.0,), split_components=False)
    tanks = _detect(config, tall_link=True)
    assert len([t for t in tanks if abs(t.radius - 6.0) < 0.1]) < 2
