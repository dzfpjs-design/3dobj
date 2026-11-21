"""The built-in parser must consume real OSGB tiles, not only files it wrote."""

from pathlib import Path

import numpy as np

from tankextract.osgb_reader import read_osgb_file

FIXTURES = Path(__file__).parent / "fixtures"


def test_contextcapture_v91_root_and_leaf():
    root = read_osgb_file(FIXTURES / "tile_root.osgb")
    leaf = read_osgb_file(FIXTURES / "tile_l17.osgb")
    assert root.version == 91 and root.attributes == 0 and root.trailing_bytes == 0
    assert leaf.version == 91 and leaf.trailing_bytes == 0
    assert root.child_files == ["Tile_+000_+000_L16_0.osgb"]
    assert leaf.child_files == ["Tile_+000_+000_L18_000.osgb"]
    for contents, verts, tris in ((root, 200, 114), (leaf, 466, 314)):
        mesh = contents.merged()
        assert mesh is not None
        assert len(mesh.vertices) == verts
        assert len(mesh.triangles) == tris
        assert np.isfinite(mesh.vertices).all()
        # Dayanta local heights sit a few hundred metres above the ENU origin.
        assert mesh.vertices[:, 2].min() > 400


def test_version_131_with_binary_brackets():
    contents = read_osgb_file(FIXTURES / "blockx_l19.osgb")
    assert contents.version == 131
    assert contents.attributes & 0x4
    assert contents.trailing_bytes == 0
    assert contents.child_files == ["BlockX_L20_73.osgb"]
    mesh = contents.merged()
    assert mesh is not None
    assert len(mesh.vertices) == 263
    assert len(mesh.triangles) == 355
    assert np.isfinite(mesh.vertices).all()
