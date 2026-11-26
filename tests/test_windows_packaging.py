"""The Windows launcher and the CLI must be different files on a case-insensitive volume."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = (ROOT / "packaging" / "windows" / "tankextract.spec").read_text(encoding="utf-8")
ISS = (ROOT / "packaging" / "windows" / "tankextract.iss").read_text(encoding="utf-8")


def test_windows_executables_differ_by_more_than_case():
    names = re.findall(r'^\s*name="([^"]+)"', SPEC, re.M)
    assert names == ["tankextract", "TankExtractGUI", "TankExtract"]
    assert "TankExtractGUI.exe" in ISS
    # tankextract.exe and TankExtract.exe are one file on Windows.
    cli, gui = names[0], names[1]
    assert cli.casefold() != gui.casefold()
