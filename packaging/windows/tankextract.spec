# -*- mode: python ; coding: utf-8 -*-
"""One-folder bundle: console tankextract.exe and windowed TankExtract.exe."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).resolve().parents[1]
datas = [
    (str(root / "tankextract" / "blender" / "export_tanks.py"), "tankextract/blender"),
    (str(root / "tankextract" / "blender" / "check_bodies.py"), "tankextract/blender"),
]
datas += collect_data_files("numpy")
datas += collect_data_files("scipy")
datas += collect_data_files("PIL")

hidden = collect_submodules("tankextract") + [
    "numpy",
    "PIL",
    "PIL.Image",
    "scipy",
    "scipy.spatial",
    "scipy.spatial._ckdtree",
    "tkinter",
    "tkinter.filedialog",
    "tkinter.scrolledtext",
    "tkinter.ttk",
]

common = dict(
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(root / "packaging" / "windows" / "rthook_utf8.py")],
    excludes=["matplotlib", "PyQt5", "PySide6"],
    noarchive=False,
)

cli = Analysis([str(root / "packaging" / "windows" / "cli_entry.py")], **common)
gui = Analysis([str(root / "packaging" / "windows" / "gui_entry.py")], **common)
MERGE((cli, "cli", "CLI"), (gui, "gui", "GUI"))

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
    argv_emulation=False,
)
exe_gui = EXE(
    pyz_gui,
    gui.scripts,
    [],
    exclude_binaries=True,
    name="TankExtract",
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
)

coll = COLLECT(
    exe_cli,
    exe_gui,
    cli.binaries,
    gui.binaries,
    cli.datas,
    gui.datas,
    name="TankExtract",
)
