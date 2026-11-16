"""Locate osgconv and Blender, and turn an OSGB tile into a triangle mesh.

The default reader is the built-in parser (streaming, textures skipped). When
``--reader osgconv`` is set, or the built-in parser rejects a tile, the file
is converted with OpenSceneGraph's ``osgconv``. On the user's Mac that binary
lives at ``~/3dobj_env/osg/bin/osgconv`` (micromamba, no sudo) and is used
automatically when it is not already on ``PATH``.
"""

from __future__ import annotations

import os
import posixpath
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from tankextract.osgb_reader import Mesh, OsgbContents, OsgbError, read_osgb_bytes

_MAC_BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
_HOME_OSGCONV = ("3dobj_env", "osg", "bin", "osgconv")


def resolve_osgconv(explicit: str | None = None) -> str | None:
    """Return an executable osgconv path, or None.

    Order: ``--osgconv``, ``$OSGCONV``, ``osgconv`` on ``PATH``, then
    ``~/3dobj_env/osg/bin/osgconv``.
    """
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("OSGCONV")
    if env:
        candidates.append(env)
    found = shutil.which("osgconv")
    if found:
        candidates.append(found)
    home_bin = Path.home().joinpath(*_HOME_OSGCONV)
    candidates.append(str(home_bin))
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def resolve_blender(explicit: str | None = None) -> str | None:
    """Return a Blender executable, including the macOS app bundle."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("BLENDER")
    if env:
        candidates.append(env)
    found = shutil.which("blender")
    if found:
        candidates.append(found)
    candidates.append(_MAC_BLENDER)
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _osg_env(osgconv: str) -> dict[str, str]:
    env = os.environ.copy()
    prefix = Path(osgconv).resolve().parent.parent
    lib_dirs = [prefix / "lib", prefix / "lib64"]
    extra = os.pathsep.join(str(path) for path in lib_dirs if path.is_dir())
    if not extra:
        return env
    # conda-forge rpath usually suffices; this covers a relocated prefix.
    for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        current = env.get(key, "")
        env[key] = extra + (os.pathsep + current if current else "")
    return env


def osgconv_to_mesh(osgconv: str, osgb_path: str | Path) -> OsgbContents:
    """Convert one tile with osgconv and parse the OBJ it writes.

    Vertices are the tile-local metres osgconv emits (relative to SRSOrigin).
    External PagedLOD children are not pulled in; the caller selects the file.
    """
    source = Path(osgb_path)
    with tempfile.TemporaryDirectory(prefix="tankextract-osg-") as folder:
        obj_path = Path(folder) / (source.stem + ".obj")
        proc = subprocess.run(
            [osgconv, str(source), str(obj_path)],
            capture_output=True,
            text=True,
            env=_osg_env(osgconv),
            check=False,
        )
        if not obj_path.is_file() or obj_path.stat().st_size == 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise OsgbError(
                f"osgconv 未能写出 OBJ（退出码 {proc.returncode}）: {detail[:500]}"
            )
        mesh = parse_obj(obj_path.read_text(encoding="utf-8", errors="replace"))
        if proc.returncode != 0 and (mesh is None or len(mesh.vertices) == 0):
            detail = (proc.stderr or proc.stdout or "").strip()
            raise OsgbError(f"osgconv 失败（退出码 {proc.returncode}）: {detail[:500]}")
    meshes = [mesh] if mesh is not None and len(mesh.vertices) else []
    return OsgbContents(version=-1, attributes=0, meshes=meshes, child_files=[], bytes_consumed=0, byte_length=0)


def parse_obj(text: str) -> Mesh | None:
    """Parse a triangle/quad OBJ. Indices may be negative. vt/vn are ignored."""
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        head, _, rest = line.partition(" ")
        if head == "v":
            parts = rest.split()
            if len(parts) < 3:
                continue
            vertices.append((float(parts[0]), float(parts[1]), float(parts[2])))
        elif head == "f":
            indices = []
            for token in rest.split():
                raw = token.split("/")[0]
                if not raw:
                    continue
                index = int(raw)
                if index < 0:
                    index = len(vertices) + index
                else:
                    index -= 1
                indices.append(index)
            if len(indices) < 3:
                continue
            for i in range(1, len(indices) - 1):
                faces.append((indices[0], indices[i], indices[i + 1]))
    if not vertices or not faces:
        return None
    coords = np.asarray(vertices, dtype=np.float64)
    triangles = np.asarray(faces, dtype=np.int32)
    valid = (
        (triangles >= 0).all(axis=1)
        & (triangles[:, 0] < len(coords))
        & (triangles[:, 1] < len(coords))
        & (triangles[:, 2] < len(coords))
    )
    triangles = triangles[valid]
    if len(triangles) == 0:
        return None
    return Mesh(coords, np.ascontiguousarray(triangles))


def load_osgb_bytes(
    data: bytes,
    *,
    reader: str,
    osgconv: str | None,
    filename: str = "tile.osgb",
) -> tuple[OsgbContents, str]:
    """Read one OSGB blob. ``reader`` is ``auto``, ``python`` or ``osgconv``."""
    mode = reader.lower().strip()
    if mode not in ("auto", "python", "osgconv"):
        raise OsgbError(f"未知读取方式 {reader}")

    def via_python() -> OsgbContents:
        contents = read_osgb_bytes(data)
        if contents.byte_length and contents.trailing_bytes > 64:
            raise OsgbError(
                f"解析未消费文件末尾 {contents.trailing_bytes} 字节，可能不完整",
                contents.bytes_consumed,
            )
        return contents

    def via_osgconv() -> OsgbContents:
        if not osgconv:
            raise OsgbError(
                "未找到 osgconv。请安装 OpenSceneGraph，或把 "
                "~/3dobj_env/osg/bin 加入 PATH，或传入 --osgconv。"
            )
        with tempfile.TemporaryDirectory(prefix="tankextract-tile-") as folder:
            path = Path(folder) / Path(filename).name
            path.write_bytes(data)
            return osgconv_to_mesh(osgconv, path)

    if mode == "osgconv":
        return via_osgconv(), "osgconv"
    if mode == "python":
        return via_python(), "python"
    try:
        return via_python(), "python"
    except (OsgbError, ValueError, struct.error) as exc:
        if not osgconv:
            raise
        try:
            contents = via_osgconv()
        except OsgbError:
            raise exc
        return contents, "osgconv"


def normalize_child(parent: str, child: str) -> str:
    """Join a PagedLOD filename onto its parent using forward slashes."""
    child = child.replace("\\", "/").strip()
    if not child:
        return ""
    parent_dir = posixpath.dirname(parent.replace("\\", "/"))
    return posixpath.normpath(posixpath.join(parent_dir, child) if parent_dir else child)


def library_hint() -> str:
    """One line describing which osgconv would be used, for the log."""
    path = resolve_osgconv(None)
    if path:
        return path
    return "未找到（仅使用内置解析器）"
