# -*- mode: python ; coding: utf-8 -*-
"""One-folder bundle.

The console program carries numpy/scipy/Pillow. The windowed launcher is only
Tk and starts tankextract.exe, so those libraries are not analyzed twice.
"""

from pathlib import Path

root = Path(SPECPATH).resolve().parents[1]
datas = [
    (str(root / "tankextract" / "blender" / "export_tanks.py"), "tankextract/blender"),
    (str(root / "tankextract" / "blender" / "check_bodies.py"), "tankextract/blender"),
]
hidden = [
    "numpy",
    "PIL",
    "PIL.Image",
    "scipy",
    "scipy.spatial",
    "scipy.spatial._ckdtree",
    "tankextract.cli",
    "tankextract.pipeline",
    "tankextract.synthetic",
    "tankextract.detect",
    "tankextract.discover",
    "tankextract.treefilter",
    "tankextract.structures",
    "tankextract.grounding",
    "tankextract.connectors",
]

cli = Analysis(
    [str(root / "packaging" / "windows" / "cli_entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[str(root / "packaging" / "windows" / "rthook_utf8.py")],
    excludes=["matplotlib", "PyQt5", "PySide6", "tkinter"],
    noarchive=False,
)
gui = Analysis(
    [str(root / "packaging" / "windows" / "gui_entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[],
    hiddenimports=["tkinter", "tkinter.filedialog", "tkinter.scrolledtext", "tkinter.ttk"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["numpy", "scipy", "PIL", "matplotlib", "tankextract", "PyQt5", "PySide6"],
    noarchive=False,
)

pyz_cli = PYZ(cli.pure)
pyz_gui = PYZ(gui.pure)

exe_cli = EXE(
    pyz_cli,
    cli.scripts,
    [],
    exclude_binaries=True,
    name="tankextract",
    console=True,
    disable_windowed_traceback=False,
)
# Windows filesystems ignore case, so this cannot be TankExtract.exe:
# that name is the same file as tankextract.exe and the GUI would replace the CLI.
exe_gui = EXE(
    pyz_gui,
    gui.scripts,
    [],
    exclude_binaries=True,
    name="TankExtractGUI",
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe_cli,
    exe_gui,
    cli.binaries,
    cli.datas,
    gui.binaries,
    gui.datas,
    name="TankExtract",
)
