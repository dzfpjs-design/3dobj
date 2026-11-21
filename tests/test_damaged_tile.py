"""A truncated tile in the source data is skipped and reported, not fatal (BC_osgb_Clip)."""

import json

from tankextract.pipeline import RunOptions, run
from tankextract.synthetic import EXPECTED, generate_scene


def test_truncated_tile_is_skipped(tmp_path):
    block = generate_scene(tmp_path / "scene")
    good = sorted((block / "Data").rglob("*.osgb"))[0]
    bad_dir = block / "Data" / "Tile_+009_+009"
    bad_dir.mkdir()
    bad = bad_dir / "Tile_+009_+009.osgb"
    bad.write_bytes(good.read_bytes()[:300])  # cut off mid-geometry
    out = tmp_path / "out"
    tanks = run(block, out, RunOptions(reader="python", no_blender=True, merge=False), log=lambda *a: None)
    assert len(tanks) == len(EXPECTED)
    stats = json.loads((out / "read_stats.json").read_text(encoding="utf-8"))
    assert stats["unreadable"] == 1
    assert stats["unreadable_tiles"] == ["Data/Tile_+009_+009/Tile_+009_+009.osgb"]
