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


def _uv_sphere(cx, cy, cz, radius, n=120, rings=60):
    verts = [(cx, cy, cz - radius)]
    for i in range(1, rings):
        phi = -np.pi / 2 + np.pi * i / rings
        for k in range(n):
            a = 2 * np.pi * k / n
            verts.append((cx + radius * np.cos(phi) * np.cos(a), cy + radius * np.cos(phi) * np.sin(a), cz + radius * np.sin(phi)))
    verts.append((cx, cy, cz + radius))
    tris = []
    for k in range(n):
        tris.append((0, 1 + (k + 1) % n, 1 + k))
    for i in range(rings - 2):
        base = 1 + i * n
        for k in range(n):
            a, b = base + k, base + (k + 1) % n
            tris.append((a, b, b + n))
            tris.append((a, b + n, a + n))
    top = len(verts) - 1
    last = 1 + (rings - 2) * n
    for k in range(n):
        tris.append((last + k, last + (k + 1) % n, top))
    return np.asarray(verts, dtype=float), np.asarray(tris, dtype=np.int64)


def _cap(cx, cy, z_rim, radius, rise, n=160, rings=30):
    rs = (radius**2 + rise**2) / (2 * rise)
    verts, tris = [], []
    for i in range(rings + 1):
        rho = radius * (1 - i / rings)
        z = z_rim + np.sqrt(rs**2 - rho**2) - (rs - rise)
        if i == rings:
            verts.append((cx, cy, z))
        else:
            for k in range(n):
                a = 2 * np.pi * k / n
                verts.append((cx + rho * np.cos(a), cy + rho * np.sin(a), z))
    for i in range(rings - 1):
        for k in range(n):
            a, b = i * n + k, i * n + (k + 1) % n
            tris.append((a, a + n, b))
            tris.append((b, a + n, b + n))
    apex = len(verts) - 1
    last = (rings - 1) * n
    for k in range(n):
        tris.append((last + k, apex, last + (k + 1) % n))
    return np.asarray(verts, dtype=float), np.asarray(tris, dtype=np.int64)


def test_sphere_on_legs_and_dome_with_handrail():
    ground = 7.5
    parts = [_terrain()]
    # Spherical tank R=6 on 8 legs, bottom 3 m above ground.
    zc = ground + 3.0 + 6.0
    parts.append(_uv_sphere(20.0, 20.0, zc, 6.0))
    for k in range(8):
        a = 2 * np.pi * k / 8
        parts.append(_cylinder(20.0 + 5.85 * np.cos(a), 20.0 + 5.85 * np.sin(a), ground, zc, 0.3, n=16, nh=12))
    # Dome-roof tank: wall 12 m, spherical cap rise 1.6 m, 1.1 m handrail on the eave.
    parts.append(_cylinder(55.0, 20.0, ground, ground + 12.0, 8.0, n=200, nh=24))
    parts.append(_cylinder(55.0, 20.0, ground + 12.0, ground + 13.1, 7.97, n=200, nh=2))
    parts.append(_cap(55.0, 20.0, ground + 12.0, 8.0, 1.6))
    vertices, triangles = _concat(parts)
    config = DetectConfig()
    acc = SceneAccumulator(config)
    acc.add_mesh("t", vertices, triangles, ORIGIN)
    absolute = vertices + np.array(ORIGIN)
    tanks = refine_candidates(acc.candidates(), [(absolute, triangles)], config, acc.ground_near, "T", "EPSG:4548", ORIGIN)
    by_x = {round(t.center_x - ORIGIN[0]): t for t in tanks}
    assert set(by_x) == {20, 55}
    sphere = by_x[20]
    assert sphere.roof_type == "sphere"
    assert abs(sphere.sphere["radius"] - 6.0) < 0.15
    assert abs(sphere.sphere["center_z"] - zc) < 0.2
    assert len(sphere.sphere["legs"]) == 8
    assert abs(sphere.sphere["leg_diameter"] - 0.6) < 0.15
    assert abs(sphere.sphere["leg_rho"] - 5.85) < 0.2
    gaps = np.diff(sorted(sphere.sphere["legs"]))
    assert np.allclose(gaps, 45.0, atol=1.0)
    dome = by_x[55]
    assert dome.roof_type == "dome"
    assert abs(dome.wall_height - 12.0) < 0.15
    assert abs(dome.roof_rise - 1.6) < 0.15
    assert dome.handrail_height is not None and abs(dome.handrail_height - 1.1) < 0.2
