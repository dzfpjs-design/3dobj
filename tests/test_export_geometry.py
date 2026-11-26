"""White-model primitives have outward normals (no dark, inside-out faces)."""

import importlib.util
import sys
import types
from pathlib import Path

import numpy as np


def _module():
    sys.modules.setdefault("bpy", types.ModuleType("bpy"))
    path = Path(__file__).resolve().parents[1] / "tankextract" / "blender" / "export_tanks.py"
    spec = importlib.util.spec_from_file_location("export_tanks_geom", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bad_faces(verts, faces, outward):
    v = np.array(verts)
    bad = 0
    for face in faces:
        p = v[list(face)]
        n = np.cross(p[1] - p[0], p[2] - p[0])
        if np.linalg.norm(n) > 1e-12 and np.dot(n, outward(p.mean(0))) <= 0:
            bad += 1
    return bad


def test_primitive_normals_point_out():
    m = _module()
    up = lambda c: np.array([0.0, 0.0, 1.0])  # noqa: E731
    assert _bad_faces(*m._dome(0, 0, 5, 2, 0.8, 16), up) == 0
    assert _bad_faces(*m._cone(0, 0, 5, 2, 0.8, 16), up) == 0
    assert _bad_faces(*m._disk(0, 0, 5, 2, 16), up) == 0
    assert _bad_faces(*m._uv_sphere(0, 0, 0, 2, 16, 8), lambda c: c) == 0

    def cyl_out(c):
        return np.array([c[0], c[1], 0.0]) if 0.01 < c[2] < 4.99 else np.array([0.0, 0.0, c[2] - 2.5])

    assert _bad_faces(*m._cylinder(0, 0, 0, 5, 2, 16, caps=True), cyl_out) == 0

    def band_out(c):
        ang = np.arctan2(c[1], c[0])
        return c - np.array([0.95 * np.cos(ang), 0.95 * np.sin(ang), 0.5])

    assert _bad_faces(*m._band(0, 0, 0, 1, 0.9, 1.0, 16), band_out) == 0
