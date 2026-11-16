"""Find ContextCapture blocks (folder, zip, or a directory of both) and metadata."""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

_OSGB_SUFFIXES = (".osgb",)


@dataclass
class Block:
    """One oblique-photogrammetry block sharing a single SRSOrigin."""

    label: str
    srs: str
    origin: tuple[float, float, float]
    kind: str  # "dir" or "zip"
    root: Path
    metadata_name: str
    osgb_names: list[str]

    def read_bytes(self, name: str) -> bytes:
        if self.kind == "dir":
            return (self.root / name).read_bytes()
        with zipfile.ZipFile(self.root) as archive:
            return archive.read(name)

    def __contains__(self, name: str) -> bool:
        return name in self._name_set

    @property
    def _name_set(self) -> set[str]:
        cached = getattr(self, "_names", None)
        if cached is None:
            cached = set(self.osgb_names)
            self._names = cached
        return cached


def discover_blocks(path: Path) -> list[Block]:
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"输入路径不存在: {path}")
    if path.is_file() and path.suffix.lower() == ".zip":
        return [_block_from_zip(path)]
    if path.is_dir():
        if _looks_like_block(path):
            return [_block_from_dir(path)]
        blocks: list[Block] = []
        for child in sorted(path.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_file() and child.suffix.lower() == ".zip":
                blocks.append(_block_from_zip(child))
            elif child.is_dir() and _looks_like_block(child):
                blocks.append(_block_from_dir(child))
        if blocks:
            return blocks
        if any(path.rglob("*.osgb")):
            return [_block_from_dir(path)]
    raise FileNotFoundError(f"未在 {path} 下找到 OSGB 块（metadata.xml / Data / .zip）")


def _looks_like_block(folder: Path) -> bool:
    if (folder / "metadata.xml").is_file() or (folder / "Data").is_dir():
        return True
    return any(folder.glob("*.osgb")) or any(folder.glob("*/*.osgb"))


def _block_from_dir(folder: Path) -> Block:
    metadata = _find_metadata_file(folder)
    srs, origin = _read_metadata(metadata.read_text(encoding="utf-8")) if metadata else ("", (0.0, 0.0, 0.0))
    if metadata is None:
        raise FileNotFoundError(f"{folder} 中没有 metadata.xml，无法读取 SRS / SRSOrigin")
    names = []
    for file in folder.rglob("*"):
        if file.is_file() and file.suffix.lower() in _OSGB_SUFFIXES:
            names.append(file.relative_to(folder).as_posix())
    if not names:
        raise FileNotFoundError(f"{folder} 中没有 .osgb 瓦片")
    rel_meta = metadata.relative_to(folder).as_posix()
    return Block(
        label=folder.name,
        srs=srs,
        origin=origin,
        kind="dir",
        root=folder,
        metadata_name=rel_meta,
        osgb_names=sorted(names),
    )


def _block_from_zip(archive_path: Path) -> Block:
    with zipfile.ZipFile(archive_path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        meta_candidates = [name for name in names if posixpath.basename(name).lower() == "metadata.xml"]
        if not meta_candidates:
            raise FileNotFoundError(f"{archive_path} 中没有 metadata.xml")
        meta_candidates.sort(key=lambda name: (name.count("/"), len(name)))
        metadata_name = meta_candidates[0]
        srs, origin = _read_metadata(archive.read(metadata_name).decode("utf-8"))
        prefix = posixpath.dirname(metadata_name)
        osgb_names = []
        for name in names:
            if not name.lower().endswith(".osgb"):
                continue
            if prefix and not (name.startswith(prefix + "/") or name == prefix):
                # Keep OSGB files that live beside this metadata or under it.
                # A zip of one block has a single metadata; include every osgb.
                pass
            osgb_names.append(name)
        if not osgb_names:
            raise FileNotFoundError(f"{archive_path} 中没有 .osgb 瓦片")
    return Block(
        label=archive_path.stem,
        srs=srs,
        origin=origin,
        kind="zip",
        root=archive_path,
        metadata_name=metadata_name,
        osgb_names=sorted(osgb_names),
    )


def _find_metadata_file(folder: Path) -> Path | None:
    direct = folder / "metadata.xml"
    if direct.is_file():
        return direct
    found = sorted(folder.glob("*/metadata.xml"))
    if found:
        return found[0]
    deeper = sorted(folder.glob("*/*/metadata.xml"))
    return deeper[0] if deeper else None


def _read_metadata(text: str) -> tuple[str, tuple[float, float, float]]:
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise ValueError(f"metadata.xml 无法解析: {exc}") from exc
    srs_node = _find(root, "SRS")
    origin_node = _find(root, "SRSOrigin")
    if srs_node is None or not (srs_node.text or "").strip():
        raise ValueError("metadata.xml 缺少 <SRS>")
    if origin_node is None or not (origin_node.text or "").strip():
        raise ValueError("metadata.xml 缺少 <SRSOrigin>")
    srs = srs_node.text.strip()
    parts = [piece.strip() for piece in origin_node.text.split(",")]
    if len(parts) != 3:
        raise ValueError(f"SRSOrigin 应为 x,y,z，实际为 {origin_node.text!r}")
    origin = (float(parts[0]), float(parts[1]), float(parts[2]))
    return srs, origin


def _find(root: ElementTree.Element, tag: str) -> ElementTree.Element | None:
    if root.tag == tag:
        return root
    for element in root.iter():
        if element.tag == tag or element.tag.endswith("}" + tag):
            return element
    return None
