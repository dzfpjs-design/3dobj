"""Chinese directory and zip paths must be readable. The Windows installer smoke test repeats this."""

import shutil
import zipfile
from pathlib import Path

from tankextract.discover import discover_blocks

FIX = Path(__file__).parent / "fixtures"


def test_unicode_folder_and_zip(tmp_path):
    folder = tmp_path / "来安模型" / "E_OSGB"
    folder.mkdir(parents=True)
    for src in FIX.iterdir():
        if not src.is_file() or src.name.startswith("._"):
            continue
        shutil.copy(src, folder / src.name)
    blocks = discover_blocks(folder)
    assert blocks and blocks[0].osgb_names
    raw = blocks[0].read_bytes(blocks[0].osgb_names[0])
    assert raw[:4] != b""

    archive = tmp_path / "压缩包" / "块.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as handle:
        for src in folder.iterdir():
            if src.is_file():
                handle.write(src, src.name)
    zipped = discover_blocks(archive)
    assert zipped[0].srs
    assert zipped[0].read_bytes(zipped[0].osgb_names[0])[:4] != b""
