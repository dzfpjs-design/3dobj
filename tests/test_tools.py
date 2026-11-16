"""osgconv discovery and the OBJ path used when --reader osgconv is selected."""

import os
import stat
from pathlib import Path

import numpy as np

from tankextract.osgb_writer import write_geode_osgb
from tankextract.tools import osgconv_to_mesh, parse_obj, resolve_osgconv


def test_resolve_osgconv_prefers_path_then_micromamba_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("OSGCONV", raising=False)
    monkeypatch.setattr("tankextract.tools.shutil.which", lambda name: None)
    assert resolve_osgconv(None) is None

    home_bin = tmp_path / "3dobj_env" / "osg" / "bin"
    home_bin.mkdir(parents=True)
    home_osg = home_bin / "osgconv"
    home_osg.write_text("#!/bin/sh\n", encoding="utf-8")
    home_osg.chmod(home_osg.stat().st_mode | stat.S_IEXEC)
    assert resolve_osgconv(None) == str(home_osg)

    path_bin = tmp_path / "elsewhere" / "osgconv"
    path_bin.parent.mkdir()
    path_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    path_bin.chmod(path_bin.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr("tankextract.tools.shutil.which", lambda name: str(path_bin))
    assert resolve_osgconv(None) == str(path_bin)

    explicit = tmp_path / "explicit"
    explicit.write_text("#!/bin/sh\n", encoding="utf-8")
    explicit.chmod(explicit.stat().st_mode | stat.S_IEXEC)
    assert resolve_osgconv(str(explicit)) == str(explicit)


def test_parse_obj_quads_and_negative_indices():
    text = "\n".join(
        [
            "v 0 0 0",
            "v 1 0 0",
            "v 1 1 0",
            "v 0 1 0",
            "f 1 2 3 4",
            "f -4 -3 -2",
        ]
    )
    mesh = parse_obj(text)
    assert mesh is not None
    assert len(mesh.vertices) == 4
    assert len(mesh.triangles) == 3


def test_writer_roundtrip_and_osgconv_if_present(tmp_path):
    angles = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    ring0 = np.column_stack([np.cos(angles), np.sin(angles), np.zeros(16)])
    ring1 = np.column_stack([np.cos(angles), np.sin(angles), np.ones(16)])
    vertices = np.vstack([ring0, ring1])
    tris = []
    for k in range(16):
        nxt = (k + 1) % 16
        tris.append((k, 16 + k, nxt))
        tris.append((nxt, 16 + k, 16 + nxt))
    triangles = np.asarray(tris, dtype=np.int64)
    path = tmp_path / "cyl.osgb"
    write_geode_osgb(path, vertices, triangles, name="cyl")

    from tankextract.osgb_reader import read_osgb_file

    mesh = read_osgb_file(path).merged()
    assert mesh is not None
    assert len(mesh.triangles) == len(triangles)
    assert np.allclose(np.sort(mesh.vertices, axis=0), np.sort(vertices, axis=0), atol=1e-5)

    osgconv = os.environ.get("TANKEXTRACT_OSGCONV")
    if not osgconv:
        candidate = Path("/tmp/osg-env/bin/osgconv")
        if candidate.is_file():
            osgconv = str(candidate)
    if not osgconv:
        return
    converted = osgconv_to_mesh(osgconv, path)
    got = converted.merged()
    assert got is not None
    assert len(got.vertices) == len(vertices)
    assert np.allclose(np.sort(got.vertices, axis=0), np.sort(vertices, axis=0), atol=1e-4)
