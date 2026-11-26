"""macOS archive debris (AppleDouble ``._*`` forks, ``__MACOSX/``) must not be read as tiles."""

import zipfile

from tankextract.discover import discover_blocks

META = ('<?xml version="1.0" encoding="UTF-8"?><ModelMetadata version="1"><SRS>EPSG:4548</SRS>'
        "<SRSOrigin>629024,3586203,0</SRSOrigin></ModelMetadata>")
REAL = "Data/Tile_+000_+000/Tile_+000_+000.osgb"
JUNK = ["Data/Tile_+000_+000/._Tile_+000_+000.osgb", "__MACOSX/Data/Tile_+000_+000/._Tile_+000_+000.osgb", "Data/.DS_Store"]


def test_folder_skips_appledouble(tmp_path):
    blk = tmp_path / "BC_osgb_Clip"
    for name in [REAL] + JUNK:
        (blk / name).parent.mkdir(parents=True, exist_ok=True)
        (blk / name).write_bytes(b"\x00\x05\x16\x07junk" if "._" in name else b"osgb")
    (blk / "metadata.xml").write_text(META, encoding="utf-8")
    (blk / "._metadata.xml").write_bytes(b"\x00\x05\x16\x07")
    (b,) = discover_blocks(blk)
    assert b.osgb_names == [REAL]
    assert b.origin == (629024.0, 3586203.0, 0.0)


def test_zip_skips_appledouble(tmp_path):
    z = tmp_path / "BC_osgb_Clip.zip"
    with zipfile.ZipFile(z, "w") as archive:
        archive.writestr("BC_osgb_Clip/metadata.xml", META)
        archive.writestr("__MACOSX/BC_osgb_Clip/._metadata.xml", b"\x00\x05\x16\x07")
        archive.writestr("BC_osgb_Clip/" + REAL, b"osgb")
        for name in JUNK:
            archive.writestr("BC_osgb_Clip/" + name, b"\x00\x05\x16\x07")
    (b,) = discover_blocks(z)
    assert b.osgb_names == ["BC_osgb_Clip/" + REAL]
    assert b.metadata_name == "BC_osgb_Clip/metadata.xml"
