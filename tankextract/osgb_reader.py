"""Pure-Python reader for the OpenSceneGraph binary (.osgb) subset used by
oblique-photogrammetry exports (ContextCapture / Smart3D / DJI Terra).

The layout follows osgDB InputStream and the osg serializers (OSG 3.0–3.6,
SO versions roughly 77–160). Textures are skipped; only triangle meshes are
returned. Binary brackets (attribute bit 0x4, common on version >= ~98) allow
unknown classes to be skipped. ContextCapture 91 files have no brackets, so
every class they write is implemented here.

Vertices stay in the tile's local frame (metres, Z up). Callers add SRSOrigin.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

import numpy as np

MAGIC_LOW = 0x6C910EA1
MAGIC_HIGH = 0x1AFB4545

# osgDB/DataTypes array / primitive identifiers (not the same order as osg::Array::Type).
_ID_BYTE = 0
_ID_UBYTE = 1
_ID_SHORT = 2
_ID_USHORT = 3
_ID_INT = 4
_ID_UINT = 5
_ID_FLOAT = 6
_ID_DOUBLE = 7
_ID_VEC2B = 8
_ID_VEC3B = 9
_ID_VEC4B = 10
_ID_VEC4UB = 11
_ID_VEC2S = 12
_ID_VEC3S = 13
_ID_VEC4S = 14
_ID_VEC2F = 15
_ID_VEC3F = 16
_ID_VEC4F = 17
_ID_VEC2D = 18
_ID_VEC3D = 19
_ID_VEC4D = 20
_ID_VEC2UB = 21
_ID_VEC3UB = 22
_ID_VEC2US = 23
_ID_VEC3US = 24
_ID_VEC4US = 25
_ID_VEC2I = 26
_ID_VEC3I = 27
_ID_VEC4I = 28
_ID_VEC2UI = 29
_ID_VEC3UI = 30
_ID_VEC4UI = 31

_ID_DRAWARRAYS = 50
_ID_DRAWARRAY_LENGTH = 51
_ID_DRAWELEMENTS_UBYTE = 52
_ID_DRAWELEMENTS_USHORT = 53
_ID_DRAWELEMENTS_UINT = 54

GL_TRIANGLES = 0x0004
GL_TRIANGLE_STRIP = 0x0005
GL_TRIANGLE_FAN = 0x0006
GL_QUADS = 0x0007
GL_QUAD_STRIP = 0x0008
GL_POLYGON = 0x0009

# (numpy dtype, components). itemsize is derived.
_ARRAY_SPEC: dict[int, tuple[str, int]] = {
    _ID_BYTE: ("i1", 1),
    _ID_UBYTE: ("u1", 1),
    _ID_SHORT: ("<i2", 1),
    _ID_USHORT: ("<u2", 1),
    _ID_INT: ("<i4", 1),
    _ID_UINT: ("<u4", 1),
    _ID_FLOAT: ("<f4", 1),
    _ID_DOUBLE: ("<f8", 1),
    _ID_VEC2B: ("i1", 2),
    _ID_VEC3B: ("i1", 3),
    _ID_VEC4B: ("i1", 4),
    _ID_VEC4UB: ("u1", 4),
    _ID_VEC2S: ("<i2", 2),
    _ID_VEC3S: ("<i2", 3),
    _ID_VEC4S: ("<i2", 4),
    _ID_VEC2F: ("<f4", 2),
    _ID_VEC3F: ("<f4", 3),
    _ID_VEC4F: ("<f4", 4),
    _ID_VEC2D: ("<f8", 2),
    _ID_VEC3D: ("<f8", 3),
    _ID_VEC4D: ("<f8", 4),
    _ID_VEC2UB: ("u1", 2),
    _ID_VEC3UB: ("u1", 3),
    _ID_VEC2US: ("<u2", 2),
    _ID_VEC3US: ("<u2", 3),
    _ID_VEC4US: ("<u2", 4),
    _ID_VEC2I: ("<i4", 2),
    _ID_VEC3I: ("<i4", 3),
    _ID_VEC4I: ("<i4", 4),
    _ID_VEC2UI: ("<u4", 2),
    _ID_VEC3UI: ("<u4", 3),
    _ID_VEC4UI: ("<u4", 4),
}


class OsgbError(Exception):
    def __init__(self, message: str, offset: int | None = None, stack: list[str] | None = None):
        self.offset = offset
        self.stack = list(stack or [])
        where = ""
        if offset is not None:
            where += f" at byte {offset}"
        if self.stack:
            where += " in " + "/".join(self.stack)
        super().__init__(message + where)


@dataclass
class Mesh:
    vertices: np.ndarray  # (N, 3) float64, local metres
    triangles: np.ndarray  # (M, 3) int32
    final: bool = False
    # Only filled when the tile is read with ``textures=True``: per-vertex texture
    # coordinates (N, 2) and the encoded texture file (JPEG/PNG bytes) they index.
    uv: np.ndarray | None = None
    image: bytes | None = None


@dataclass
class OsgbContents:
    version: int
    attributes: int
    meshes: list[Mesh] = field(default_factory=list)
    child_files: list[str] = field(default_factory=list)
    bytes_consumed: int = 0
    byte_length: int = 0

    @property
    def trailing_bytes(self) -> int:
        return max(0, self.byte_length - self.bytes_consumed)

    def merged(self) -> Mesh | None:
        parts = [m for m in self.meshes if len(m.vertices) and len(m.triangles)]
        if not parts:
            return None
        if len(parts) == 1:
            return parts[0]
        verts = []
        tris = []
        offset = 0
        for mesh in parts:
            verts.append(mesh.vertices)
            tris.append(mesh.triangles + offset)
            offset += len(mesh.vertices)
        return Mesh(np.vstack(verts), np.vstack(tris))


def read_osgb_bytes(data: bytes, textures: bool = False) -> OsgbContents:
    """Parse one OSGB blob. With ``textures`` each mesh also keeps its texture
    coordinates and the embedded texture image bytes (used for colour checks)."""
    reader = _Reader(data)
    reader.textures = textures
    reader.parse()
    return OsgbContents(
        version=reader.version,
        attributes=reader.attributes,
        meshes=reader.meshes,
        child_files=reader.child_files,
        bytes_consumed=reader.pos,
        byte_length=len(data),
    )


def read_osgb_file(path: str, textures: bool = False) -> OsgbContents:
    with open(path, "rb") as handle:
        return read_osgb_bytes(handle.read(), textures=textures)


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.swap = False
        self.version = 0
        self.attributes = 0
        self.brackets = False
        self.meshes: list[Mesh] = []
        self.child_files: list[str] = []
        self.obj_cache: dict[int, object] = {}
        self.array_cache: dict[int, np.ndarray] = {}
        self._bracket_stack: list[tuple[int, int] | None] = []
        self.stack: list[str] = []
        self.built: object = None
        self._prim_mode = 0
        self._pending_matrix: np.ndarray | None = None
        self._pending_rf = 0
        self.textures = False
        # Last embedded texture seen. Photogrammetry tiles put the StateSet (with its
        # Texture2D / Image) before the Geometry arrays it textures.
        self.current_image: bytes | None = None
        self._texcoords: np.ndarray | None = None

    # --- scalars ---------------------------------------------------------

    def _need(self, n: int) -> None:
        if self.pos + n > len(self.data):
            raise OsgbError(
                f"unexpected end of file (need {n} bytes, have {len(self.data) - self.pos})",
                self.pos,
                self.stack,
            )

    def bytes(self, n: int) -> bytes:
        self._need(n)
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def skip(self, n: int) -> None:
        self._need(n)
        self.pos += n

    def _unpack(self, fmt_le: str, fmt_be: str, n: int):
        self._need(n)
        fmt = fmt_be if self.swap else fmt_le
        value = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += n
        return value

    def u32(self) -> int:
        return int(self._unpack("<I", ">I", 4))

    def i32(self) -> int:
        return int(self._unpack("<i", ">i", 4))

    def u64(self) -> int:
        return int(self._unpack("<Q", ">Q", 8))

    def f32(self) -> float:
        return float(self._unpack("<f", ">f", 4))

    def f64(self) -> float:
        return float(self._unpack("<d", ">d", 8))

    def bool(self) -> bool:
        self._need(1)
        value = self.data[self.pos]
        self.pos += 1
        return value != 0

    def string(self) -> str:
        size = self.i32()
        if size < 0 or size > 20_000_000:
            raise OsgbError(f"invalid string length {size}", self.pos, self.stack)
        raw = self.bytes(size) if size else b""
        return raw.decode("utf-8", errors="surrogateescape")

    def size(self, what: str, limit: int = 50_000_000) -> int:
        count = self.u32()
        if count > limit:
            raise OsgbError(f"{what} count {count} is unreasonably large", self.pos, self.stack)
        return count

    def begin_bracket(self) -> None:
        if not self.brackets:
            self._bracket_stack.append(None)
            return
        start = self.pos
        if self.version > 148:
            span = self.u64()
        else:
            span = self.u32()
        self._bracket_stack.append((start, int(span)))

    def end_bracket(self) -> None:
        if not self._bracket_stack:
            raise OsgbError("unbalanced end bracket", self.pos, self.stack)
        self._bracket_stack.pop()

    def skip_rest_of_bracket(self) -> None:
        if not self._bracket_stack:
            raise OsgbError("no bracket to skip", self.pos, self.stack)
        item = self._bracket_stack.pop()
        if item is None:
            raise OsgbError(
                f"cannot skip unknown class {'/'.join(self.stack)} without binary brackets",
                self.pos,
                self.stack,
            )
        start, span = item
        end = start + span
        if end < self.pos or end > len(self.data):
            raise OsgbError(f"corrupt bracket size {span}", self.pos, self.stack)
        self.pos = end

    # --- header ----------------------------------------------------------

    def parse(self) -> None:
        low = self.u32()
        high = self.u32()
        if low == _bswap32(MAGIC_LOW) and high == _bswap32(MAGIC_HIGH):
            self.swap = True
            self.pos = 0
            low = self.u32()
            high = self.u32()
        if low != MAGIC_LOW or high != MAGIC_HIGH:
            raise OsgbError(f"not an osgb file (magic {low:#x} {high:#x})", 0)
        file_type = self.u32()
        self.version = self.u32()
        self.attributes = self.u32()
        if file_type not in (1, 2, 3):
            raise OsgbError(f"unsupported osgb record type {file_type}", self.pos)
        if self.attributes & 0x1:
            domains = self.u32()
            for _ in range(domains):
                self.string()
                self.i32()
        self.brackets = bool(self.attributes & 0x4)
        compressor = self.string()
        if compressor not in ("0", ""):
            self._inflate(compressor)
        if self.attributes & 0x2:
            # Inbuilt schema. Field order then depends on the schema text.
            # Photogrammetry exports seen in the wild do not set this bit.
            _schema = self.string()
            raise OsgbError(
                "osgb uses an inline schema (attribute bit 0x2); convert with osgconv",
                self.pos,
            )
        if file_type == 2:
            self.read_image()
        else:
            self.read_object()

    def _inflate(self, name: str) -> None:
        rest = self.data[self.pos :]
        decoded = None
        errors = []
        for mode in (zlib.MAX_WBITS, -zlib.MAX_WBITS, zlib.MAX_WBITS | 16):
            try:
                decoded = zlib.decompress(rest, mode)
                break
            except zlib.error as exc:
                errors.append(str(exc))
        if decoded is None and len(rest) > 4:
            # Some OSG compressors prefix the uncompressed size.
            try:
                decoded = zlib.decompress(rest[4:])
            except zlib.error as exc:
                errors.append(str(exc))
        if decoded is None:
            raise OsgbError(
                f"cannot decompress osgb body ({name}): {errors[-1] if errors else 'unknown'}. "
                "Install OpenSceneGraph and retry; osgconv is the fallback.",
                self.pos,
            )
        self.data = self.data[: self.pos] + decoded
        # pos stays at the start of the inflated payload

    # --- objects ---------------------------------------------------------

    def read_object(self):
        name = self.string()
        if name in ("NULL", ""):
            return None
        self.begin_bracket()
        oid = self.u32()
        if oid != 0 and oid in self.obj_cache:
            if self.brackets:
                self.skip_rest_of_bracket()
            else:
                self.end_bracket()
            cached = self.obj_cache[oid]
            _note_image(self, cached)
            return cached

        saved_built = self.built
        saved_mode = self._prim_mode
        saved_matrix = self._pending_matrix
        saved_rf = self._pending_rf
        self.built = None
        self._pending_matrix = None
        self._pending_rf = 0
        mesh_start = len(self.meshes)
        self.stack.append(name)
        try:
            spec = _CLASS_SPEC.get(name)
            if spec is None:
                if self.brackets:
                    self.skip_rest_of_bracket()
                    return None
                raise OsgbError(f"unsupported class {name}", self.pos, self.stack)
            if oid != 0:
                self.obj_cache[oid] = None
            for reader, vmin, vmax in spec:
                if vmin <= self.version <= vmax:
                    reader(self)
            obj = self.built if self.built is not None else name
            if oid != 0:
                self.obj_cache[oid] = obj
            if self._pending_matrix is not None:
                _apply_matrix(
                    self.meshes[mesh_start:],
                    self._pending_matrix,
                    absolute=self._pending_rf != 0,
                )
            self.end_bracket()
            return obj
        except OsgbError:
            raise
        except Exception as exc:
            raise OsgbError(str(exc), self.pos, self.stack) from exc
        finally:
            self.stack.pop()
            self.built = saved_built
            self._prim_mode = saved_mode
            self._pending_matrix = saved_matrix
            self._pending_rf = saved_rf

    def read_obj_ref(self) -> None:
        if self.bool():
            self.read_object()

    # --- arrays ----------------------------------------------------------

    def read_numeric(self, count: int, components: int, dtype: str) -> np.ndarray:
        if count == 0:
            return np.zeros((0, components), dtype=np.float64)
        item = np.dtype(dtype).itemsize
        raw = self.bytes(count * components * item)
        array = np.frombuffer(raw, dtype=np.dtype(dtype)).reshape(count, components)
        if self.swap and array.dtype.itemsize > 1:
            array = array.byteswap().newbyteorder()
        return np.array(array, dtype=np.float64, copy=True)

    def read_indices(self, count: int, dtype: str) -> np.ndarray:
        if count == 0:
            return np.zeros((0,), dtype=np.int64)
        item = np.dtype(dtype).itemsize
        raw = self.bytes(count * item)
        array = np.frombuffer(raw, dtype=np.dtype(dtype))
        if self.swap and array.dtype.itemsize > 1:
            array = array.byteswap().newbyteorder()
        return np.array(array, dtype=np.int64, copy=True)

    def read_inline_array(self) -> np.ndarray | None:
        """InputStream::readArray (SO version < 112)."""
        aid = self.u32()
        if aid != 0 and aid in self.array_cache:
            return self.array_cache[aid]
        type_id = self.i32()
        spec = _ARRAY_SPEC.get(type_id)
        if spec is None:
            raise OsgbError(f"unsupported inline array type {type_id}", self.pos, self.stack)
        dtype, components = spec
        count = self.i32()
        if count < 0 or count > 50_000_000:
            raise OsgbError(f"inline array length {count}", self.pos, self.stack)
        self.begin_bracket()
        if components == 1 and dtype in ("<u4", "<u2", "u1", "<i4", "<i2"):
            # Still stored as a numeric row; callers that need indices use elements path.
            values = self.read_numeric(count, 1, dtype)
        else:
            values = self.read_numeric(count, components, dtype)
        self.end_bracket()
        if aid != 0:
            self.array_cache[aid] = values
        return values

    def read_geometry_array_struct(self) -> np.ndarray | None:
        """Geometry.cpp readArray: Array / Indices / Binding / Normalize."""
        has_array = self.bool()
        array = self.read_inline_array() if has_array else None
        if self.bool():
            self.read_inline_array()
        self.i32()  # binding
        self.i32()  # normalize
        return array

    def read_old_single_array(self) -> np.ndarray | None:
        if not self.bool():
            return None
        self.begin_bracket()
        array = self.read_geometry_array_struct()
        self.end_bracket()
        return array

    def read_old_array_list(self) -> np.ndarray | None:
        if not self.bool():
            return None
        count = self.size("texcoord list", limit=64)
        first = None
        self.begin_bracket()
        for _ in range(count):
            self.begin_bracket()
            array = self.read_geometry_array_struct()
            if first is None and isinstance(array, np.ndarray):
                first = array
            self.end_bracket()
        self.end_bracket()
        return first

    def read_inline_primitive(self):
        ptype = self.i32()
        mode = self.i32()
        if self.version > 96:
            self.u32()  # numInstances
        if ptype == _ID_DRAWARRAYS:
            first = self.i32()
            count = self.i32()
            return ("arrays", mode, first, count)
        if ptype == _ID_DRAWARRAY_LENGTH:
            first = self.i32()
            count = self.size("draw array lengths")
            self.begin_bracket()
            lengths = [self.i32() for _ in range(count)]
            self.end_bracket()
            return ("lengths", mode, first, lengths)
        if ptype == _ID_DRAWELEMENTS_UBYTE:
            return ("elements", mode, self._read_index_block("u1"))
        if ptype == _ID_DRAWELEMENTS_USHORT:
            return ("elements", mode, self._read_index_block("<u2"))
        if ptype == _ID_DRAWELEMENTS_UINT:
            return ("elements", mode, self._read_index_block("<u4"))
        raise OsgbError(f"unsupported primitive type {ptype}", self.pos, self.stack)

    def _read_index_block(self, dtype: str) -> np.ndarray:
        count = self.i32()
        if count < 0 or count > 80_000_000:
            raise OsgbError(f"index count {count}", self.pos, self.stack)
        self.begin_bracket()
        indices = self.read_indices(count, dtype)
        self.end_bracket()
        return indices

    def read_primitive_vector_inline(self) -> list:
        count = self.size("primitives", limit=1_000_000)
        return [self.read_inline_primitive() for _ in range(count)]

    def read_primitive_vector_objects(self) -> list:
        count = self.size("primitives", limit=1_000_000)
        prims = []
        for _ in range(count):
            obj = self.read_object()
            if isinstance(obj, tuple):
                prims.append(obj)
        return prims

    # --- images ----------------------------------------------------------

    def read_image(self):
        if self.version > 94:
            self.string()  # class name, property tag is omitted in binary
        oid = self.u32()
        if oid != 0 and oid in self.obj_cache:
            cached = self.obj_cache[oid]
            _note_image(self, cached)
            return cached
        _filename = self.string()
        payload = None
        self.i32()  # write hint
        decision = self.i32()
        if decision == 0:  # inline pixels
            self.i32()  # origin
            self.i32()
            self.i32()
            self.i32()  # s, t, r
            self.i32()  # internal format
            self.i32()
            self.i32()
            self.i32()
            self.i32()  # pixel format, data type, packing, allocation
            nbytes = self.u32()
            if nbytes:
                self.skip(nbytes)
            levels = self.u32()
            for _ in range(levels):
                self.u32()
        elif decision == 1:  # embedded file (jpeg/png/dds)
            nbytes = self.u32()
            if nbytes:
                if self.textures:
                    self._need(nbytes)
                    payload = bytes(self.data[self.pos : self.pos + nbytes])
                self.skip(nbytes)
        elif decision in (2, 3):
            pass
        else:
            raise OsgbError(f"unknown image storage mode {decision}", self.pos, self.stack)
        # writeImage appends only osg::Object fields, not the Image wrapper.
        if oid != 0:
            self.obj_cache[oid] = ("image",)
        _read_object_fields(self)
        marker = ("image", decision, payload)
        _note_image(self, marker)
        if oid != 0:
            self.obj_cache[oid] = marker
        return marker

    def read_image_ref(self):
        if self.bool():
            return self.read_image()
        return None

    # --- geometry emit ---------------------------------------------------

    def emit_geometry(self, vertices: np.ndarray | None, prims: list, texcoords: np.ndarray | None = None) -> None:
        if vertices is None:
            return
        coords = np.asarray(vertices, dtype=np.float64)
        if coords.ndim != 2 or coords.shape[1] < 3 or len(coords) == 0:
            return
        coords = np.ascontiguousarray(coords[:, :3])
        triangles = []
        for prim in prims:
            if not isinstance(prim, tuple):
                continue
            kind = prim[0]
            mode = int(prim[1])
            if kind == "elements":
                indices = prim[2]
            elif kind == "arrays":
                first, count = int(prim[2]), int(prim[3])
                if count <= 0:
                    continue
                indices = np.arange(first, first + count, dtype=np.int64)
            elif kind == "lengths":
                first = int(prim[2])
                cursor = first
                for length in prim[3]:
                    length = int(length)
                    if length > 0:
                        indices = np.arange(cursor, cursor + length, dtype=np.int64)
                        part = _triangulate(mode, indices)
                        if len(part):
                            triangles.append(part)
                    cursor += max(length, 0)
                continue
            else:
                continue
            part = _triangulate(mode, np.asarray(indices, dtype=np.int64))
            if len(part):
                triangles.append(part)
        if not triangles:
            return
        merged = np.concatenate(triangles, axis=0)
        valid = (
            (merged >= 0).all(axis=1)
            & (merged[:, 0] < len(coords))
            & (merged[:, 1] < len(coords))
            & (merged[:, 2] < len(coords))
        )
        merged = merged[valid]
        if len(merged) == 0:
            return
        mesh = Mesh(coords, np.ascontiguousarray(merged.astype(np.int32)))
        if (
            self.textures
            and self.current_image is not None
            and isinstance(texcoords, np.ndarray)
            and texcoords.ndim == 2
            and texcoords.shape[1] >= 2
            and len(texcoords) == len(coords)
        ):
            mesh.uv = np.ascontiguousarray(texcoords[:, :2], dtype=np.float32)
            mesh.image = self.current_image
        self.meshes.append(mesh)


def _bswap32(value: int) -> int:
    return struct.unpack(">I", struct.pack("<I", value & 0xFFFFFFFF))[0]


def _apply_matrix(meshes: list[Mesh], matrix: np.ndarray, absolute: bool) -> None:
    if matrix is None:
        return
    linear = matrix[:3, :3]
    translation = matrix[3, :3]
    for mesh in meshes:
        if mesh.final:
            continue
        mesh.vertices = mesh.vertices @ linear + translation
        if absolute:
            mesh.final = True


def _triangulate(mode: int, indices: np.ndarray) -> np.ndarray:
    indices = np.asarray(indices, dtype=np.int64).reshape(-1)
    n = len(indices)
    if n < 3:
        return np.zeros((0, 3), dtype=np.int64)
    if mode == GL_TRIANGLES:
        usable = n - (n % 3)
        return indices[:usable].reshape(-1, 3)
    if mode == GL_TRIANGLE_STRIP:
        tris = []
        for i in range(n - 2):
            if i % 2 == 0:
                tris.append((indices[i], indices[i + 1], indices[i + 2]))
            else:
                tris.append((indices[i + 1], indices[i], indices[i + 2]))
        return np.asarray(tris, dtype=np.int64)
    if mode in (GL_TRIANGLE_FAN, GL_POLYGON):
        root = indices[0]
        tris = [(root, indices[i], indices[i + 1]) for i in range(1, n - 1)]
        return np.asarray(tris, dtype=np.int64)
    if mode == GL_QUADS:
        tris = []
        usable = n - (n % 4)
        for i in range(0, usable, 4):
            a, b, c, d = indices[i : i + 4]
            tris.append((a, b, c))
            tris.append((a, c, d))
        return np.asarray(tris, dtype=np.int64) if tris else np.zeros((0, 3), dtype=np.int64)
    if mode == GL_QUAD_STRIP:
        tris = []
        for i in range(0, n - 3, 2):
            a, b, c, d = indices[i], indices[i + 1], indices[i + 2], indices[i + 3]
            # GL quad strip quad is (v0, v1, v3, v2).
            tris.append((a, b, d))
            tris.append((a, d, c))
        return np.asarray(tris, dtype=np.int64) if tris else np.zeros((0, 3), dtype=np.int64)
    return np.zeros((0, 3), dtype=np.int64)


# --- serializer field readers (associate order, base class first) -------


def _read_object_fields(reader: _Reader) -> None:
    reader.string()  # name
    reader.i32()  # data variance
    if reader.version >= 77:
        reader.read_obj_ref()  # UserDataContainer
    elif reader.bool():
        reader.begin_bracket()
        reader.read_object()
        reader.end_bracket()


def _read_node_fields(reader: _Reader) -> None:
    if reader.bool():  # initial bound
        reader.begin_bracket()
        for _ in range(4):
            reader.f64()
        reader.end_bracket()
    reader.read_obj_ref()  # compute bound callback
    reader.read_obj_ref()  # update
    reader.read_obj_ref()  # event
    reader.read_obj_ref()  # cull
    reader.bool()  # culling active
    reader.u32()  # node mask
    if reader.version < 77 and reader.bool():
        count = reader.size("descriptions", limit=10_000)
        reader.begin_bracket()
        for _ in range(count):
            reader.string()
        reader.end_bracket()
    reader.read_obj_ref()  # stateset


def _read_group_children(reader: _Reader) -> None:
    if not reader.bool():
        return
    count = reader.size("children")
    reader.begin_bracket()
    for _ in range(count):
        reader.read_object()
    reader.end_bracket()


def _read_drawable_fields(reader: _Reader) -> None:
    if reader.version < 156:
        reader.read_obj_ref()  # stateset
    if reader.bool():  # initial bounding box
        reader.begin_bracket()
        for _ in range(6):
            reader.f64()
        reader.end_bracket()
    reader.read_obj_ref()  # compute bbox callback
    reader.read_obj_ref()  # shape
    reader.bool()
    reader.bool()
    reader.bool()
    if reader.version < 156:
        reader.read_obj_ref()
        reader.read_obj_ref()
        reader.read_obj_ref()
        reader.read_obj_ref()
    if reader.version >= 142:
        reader.u32()  # node mask
    if reader.version >= 145:
        reader.bool()  # culling active


def _read_geometry_fields(reader: _Reader) -> None:
    if reader.version < 112:
        prims = reader.read_primitive_vector_inline()
        vertices = reader.read_old_single_array()
        reader.read_old_single_array()  # normals
        reader.read_old_single_array()  # colors
        reader.read_old_single_array()  # secondary colors
        reader.read_old_single_array()  # fog coords
        texcoords = reader.read_old_array_list()  # texcoords
        reader.read_old_array_list()  # vertex attribs
        reader.bool()  # fast-path hint (checker is always false)
    else:
        prims = reader.read_primitive_vector_objects()
        vertices = _read_optional_object_array(reader)
        _read_optional_object_array(reader)
        _read_optional_object_array(reader)
        _read_optional_object_array(reader)
        _read_optional_object_array(reader)
        texcoords = _consume_object_vector(reader)
        _consume_object_vector(reader)
    reader.emit_geometry(vertices if isinstance(vertices, np.ndarray) else None, prims, texcoords)


def _read_optional_object_array(reader: _Reader) -> np.ndarray | None:
    if not reader.bool():
        return None
    obj = reader.read_object()
    return obj if isinstance(obj, np.ndarray) else None


def _consume_object_vector(reader: _Reader) -> np.ndarray | None:
    count = reader.size("array list", limit=64)
    first = None
    for _ in range(count):
        obj = reader.read_object()
        if first is None and isinstance(obj, np.ndarray):
            first = obj
    return first


def _note_image(reader: _Reader, obj) -> None:
    """Remember an embedded texture (also when a Texture2D / Image is a shared reference)."""
    if reader.textures and isinstance(obj, tuple) and len(obj) == 3 and obj[0] == "image" and obj[2]:
        reader.current_image = obj[2]


def _read_lod_fields(reader: _Reader) -> None:
    reader.i32()  # center mode
    if reader.bool():
        for _ in range(4):
            reader.f64()
    reader.i32()  # range mode
    if reader.bool():
        count = reader.size("lod ranges", limit=64)
        reader.begin_bracket()
        for _ in range(count):
            reader.f32()
            reader.f32()
        reader.end_bracket()


def _read_pagedlod_fields(reader: _Reader) -> None:
    # DatabasePath user serializer: checker is always true, then an inner bool.
    if reader.bool():
        if reader.bool():
            reader.string()
    if reader.version < 70:
        reader.u32()
    reader.u32()  # num children that cannot be expired
    reader.bool()  # disable external children paging
    if reader.bool():
        count = reader.size("paged filenames", limit=10_000)
        reader.begin_bracket()
        names = [reader.string() for _ in range(count)]
        reader.end_bracket()
        priorities = reader.size("priorities", limit=10_000)
        reader.begin_bracket()
        for _ in range(priorities):
            reader.f32()
            reader.f32()
        reader.end_bracket()
        for name in names:
            if name:
                reader.child_files.append(name)
    if reader.bool():
        count = reader.u32()
        if count:
            if count > 100_000:
                raise OsgbError("too many paged children", reader.pos, reader.stack)
            reader.begin_bracket()
            for _ in range(count):
                reader.read_object()
            reader.end_bracket()


def _read_geode_fields(reader: _Reader) -> None:
    if not reader.bool():
        return
    count = reader.size("drawables")
    reader.begin_bracket()
    for _ in range(count):
        reader.read_object()
    reader.end_bracket()


def _read_transform_fields(reader: _Reader) -> None:
    reader._pending_rf = reader.i32()


def _read_matrix_fields(reader: _Reader) -> None:
    reader.begin_bracket()
    matrix = np.zeros((4, 4), dtype=np.float64)
    for row in range(4):
        for col in range(4):
            matrix[row, col] = reader.f64()
    reader.end_bracket()
    reader._pending_matrix = matrix


def _read_stateset_fields(reader: _Reader) -> None:
    _read_user_modes(reader)  # mode list
    _read_user_attributes(reader)  # attribute list
    if reader.bool():  # texture modes
        units = reader.size("texture mode units", limit=32)
        reader.begin_bracket()
        for _ in range(units):
            _read_modes(reader)
        reader.end_bracket()
    if reader.bool():  # texture attributes
        units = reader.size("texture attribute units", limit=32)
        reader.begin_bracket()
        for _ in range(units):
            _read_attributes(reader)
        reader.end_bracket()
    if reader.bool():  # uniforms
        count = reader.size("uniforms", limit=10_000)
        reader.begin_bracket()
        for _ in range(count):
            reader.read_object()
            reader.i32()
        reader.end_bracket()
    reader.i32()  # rendering hint
    reader.i32()  # render bin mode
    reader.i32()  # bin number
    reader.string()  # bin name
    reader.bool()  # nest render bins
    reader.read_obj_ref()
    reader.read_obj_ref()
    if reader.version >= 151 and reader.bool():
        count = reader.size("defines", limit=10_000)
        reader.begin_bracket()
        for _ in range(count):
            reader.string()
            reader.string()
            reader.i32()
        reader.end_bracket()


def _read_user_modes(reader: _Reader) -> None:
    if reader.bool():
        _read_modes(reader)


def _read_modes(reader: _Reader) -> None:
    count = reader.size("modes", limit=64)
    if count:
        reader.begin_bracket()
        for _ in range(count):
            reader.u32()
            reader.i32()
        reader.end_bracket()


def _read_user_attributes(reader: _Reader) -> None:
    if reader.bool():
        _read_attributes(reader)


def _read_attributes(reader: _Reader) -> None:
    count = reader.size("state attributes", limit=64)
    if count:
        reader.begin_bracket()
        for _ in range(count):
            reader.read_object()
            reader.i32()
        reader.end_bracket()


def _read_stateattribute_fields(reader: _Reader) -> None:
    reader.read_obj_ref()
    reader.read_obj_ref()


def _read_material_color(reader: _Reader) -> None:
    # User serializer bool (checker is always true) then front/back values.
    if not reader.bool():
        return
    reader.bool()  # front and back coupled
    for _ in range(4):
        reader.f32()
    for _ in range(4):
        reader.f32()


def _read_material_shininess(reader: _Reader) -> None:
    if not reader.bool():
        return
    reader.bool()
    reader.f32()
    reader.f32()


def _read_material_fields(reader: _Reader) -> None:
    reader.i32()  # color mode
    for _ in range(4):
        _read_material_color(reader)
    _read_material_shininess(reader)


def _read_glenum_user(reader: _Reader) -> None:
    """User serializer whose payload is one GLenum (wrap / filter)."""
    if reader.bool():
        reader.u32()


def _read_optional_glenum_user(reader: _Reader) -> None:
    if reader.bool():
        reader.u32()


def _read_texture_fields(reader: _Reader) -> None:
    for _ in range(5):  # wrap s/t/r, min, mag
        _read_glenum_user(reader)
    reader.f32()  # max anisotropy
    reader.bool()
    reader.bool()
    reader.bool()
    reader.bool()
    for _ in range(4):
        reader.f64()  # border color
    reader.i32()  # border width
    reader.i32()  # internal format mode
    _read_optional_glenum_user(reader)  # internal format
    _read_optional_glenum_user(reader)  # source format
    _read_optional_glenum_user(reader)  # source type
    reader.bool()  # shadow comparison
    reader.i32()
    reader.i32()
    reader.f32()  # shadow ambient
    if 95 <= reader.version < 154:
        # ImageAttachment checker always returns false.
        reader.bool()
    if reader.version >= 98:
        if reader.bool():
            reader.string()  # swizzle, usually "RGBA"
    if reader.version >= 155:
        reader.f32()
        reader.f32()
        reader.f32()


def _read_texture2d_fields(reader: _Reader) -> None:
    image = reader.read_image_ref()
    if isinstance(image, tuple) and len(image) == 3 and image[2]:
        reader.built = image  # a shared Texture2D reference then restores the image too
    reader.i32()
    reader.i32()


def _read_cullface_fields(reader: _Reader) -> None:
    reader.i32()


def _read_blendfunc_fields(reader: _Reader) -> None:
    reader.u32()
    reader.u32()
    reader.u32()
    reader.u32()


def _read_shademodel_fields(reader: _Reader) -> None:
    reader.i32()


def _read_texenv_fields(reader: _Reader) -> None:
    reader.i32()
    for _ in range(4):
        reader.f32()


def _read_udc_fields(reader: _Reader) -> None:
    if reader.bool():
        reader.begin_bracket()
        reader.read_object()
        reader.end_bracket()
    if reader.bool():
        count = reader.size("udc descriptions", limit=10_000)
        reader.begin_bracket()
        for _ in range(count):
            reader.string()
        reader.end_bracket()
    if reader.bool():
        count = reader.size("user objects", limit=10_000)
        reader.begin_bracket()
        for _ in range(count):
            reader.read_object()
        reader.end_bracket()


def _read_switch_fields(reader: _Reader) -> None:
    if not reader.bool():
        return
    count = reader.size("switch values", limit=100_000)
    reader.begin_bracket()
    for _ in range(count):
        reader.bool()
    reader.end_bracket()


def _read_array_common(reader: _Reader) -> None:
    reader.i32()  # binding
    reader.bool()  # normalize
    reader.bool()  # preserve data type


def _read_bufferdata_fields(reader: _Reader) -> None:
    reader.read_obj_ref()


def _read_bufferobject_fields(reader: _Reader) -> None:
    reader.u32()
    reader.u32()
    reader.bool()


def _make_vector_body(components: int, dtype: str):
    def reader(self: _Reader) -> None:
        count = self.size("vector elements")
        self.built = self.read_numeric(count, components, dtype)

    return reader


def _make_index_body(dtype: str):
    def reader(self: _Reader) -> None:
        count = self.size("draw elements")
        indices = self.read_indices(count, dtype)
        self.built = ("elements", self._prim_mode, indices)

    return reader


def _read_primitiveset_fields(reader: _Reader) -> None:
    reader.i32()  # num instances
    reader._prim_mode = reader.i32()


def _read_drawarrays_fields(reader: _Reader) -> None:
    first = reader.i32()
    count = reader.i32()
    reader.built = ("arrays", reader._prim_mode, first, count)


def _read_drawarraylengths_fields(reader: _Reader) -> None:
    first = reader.i32()
    count = reader.size("draw array lengths")
    lengths = [reader.i32() for _ in range(count)]
    reader.built = ("lengths", reader._prim_mode, first, lengths)


def _associate(*readers_and_ranges):
    return readers_and_ranges


_INF = 10**9

def _array_class(body):
    return (
        (_read_object_fields, 0, _INF),
        (_read_bufferdata_fields, 147, _INF),
        (_read_array_common, 0, _INF),
        (body, 0, _INF),
    )


def _primitive_class(body):
    return (
        (_read_object_fields, 0, _INF),
        (_read_bufferdata_fields, 147, _INF),
        (_read_primitiveset_fields, 0, _INF),
        (body, 0, _INF),
    )


_CLASS_SPEC: dict[str, tuple] = {
    "osg::Object": ((_read_object_fields, 0, _INF),),
    "osg::Node": (
        (_read_object_fields, 0, _INF),
        (_read_node_fields, 0, _INF),
    ),
    "osg::Group": (
        (_read_object_fields, 0, _INF),
        (_read_node_fields, 0, _INF),
        (_read_group_children, 0, _INF),
    ),
    "osg::Geode": (
        (_read_object_fields, 0, _INF),
        (_read_node_fields, 0, _INF),
        (_read_geode_fields, 0, _INF),
    ),
    "osg::Geometry": (
        (_read_object_fields, 0, _INF),
        (_read_node_fields, 154, _INF),
        (_read_drawable_fields, 0, _INF),
        (_read_geometry_fields, 0, _INF),
    ),
    "osg::Drawable": (
        (_read_object_fields, 0, _INF),
        (_read_node_fields, 154, _INF),
        (_read_drawable_fields, 0, _INF),
    ),
    "osg::LOD": (
        (_read_object_fields, 0, _INF),
        (_read_node_fields, 0, _INF),
        (_read_group_children, 0, _INF),
        (_read_lod_fields, 0, _INF),
    ),
    "osg::PagedLOD": (
        (_read_object_fields, 0, _INF),
        (_read_node_fields, 0, _INF),
        (_read_lod_fields, 0, _INF),
        (_read_pagedlod_fields, 0, _INF),
    ),
    "osg::MatrixTransform": (
        (_read_object_fields, 0, _INF),
        (_read_node_fields, 0, _INF),
        (_read_group_children, 0, _INF),
        (_read_transform_fields, 0, _INF),
        (_read_matrix_fields, 0, _INF),
    ),
    "osg::Switch": (
        (_read_object_fields, 0, _INF),
        (_read_node_fields, 0, _INF),
        (_read_group_children, 0, _INF),
        (_read_switch_fields, 0, _INF),
    ),
    "osg::StateSet": (
        (_read_object_fields, 0, _INF),
        (_read_stateset_fields, 0, _INF),
    ),
    "osg::StateAttribute": (
        (_read_object_fields, 0, _INF),
        (_read_stateattribute_fields, 0, _INF),
    ),
    "osg::Material": (
        (_read_object_fields, 0, _INF),
        (_read_stateattribute_fields, 0, _INF),
        (_read_material_fields, 0, _INF),
    ),
    "osg::Texture2D": (
        (_read_object_fields, 0, _INF),
        (_read_stateattribute_fields, 0, _INF),
        (_read_texture_fields, 0, _INF),
        (_read_texture2d_fields, 0, _INF),
    ),
    "osg::CullFace": (
        (_read_object_fields, 0, _INF),
        (_read_stateattribute_fields, 0, _INF),
        (_read_cullface_fields, 0, _INF),
    ),
    "osg::BlendFunc": (
        (_read_object_fields, 0, _INF),
        (_read_stateattribute_fields, 0, _INF),
        (_read_blendfunc_fields, 0, _INF),
    ),
    "osg::ShadeModel": (
        (_read_object_fields, 0, _INF),
        (_read_stateattribute_fields, 0, _INF),
        (_read_shademodel_fields, 0, _INF),
    ),
    "osg::TexEnv": (
        (_read_object_fields, 0, _INF),
        (_read_stateattribute_fields, 0, _INF),
        (_read_texenv_fields, 0, _INF),
    ),
    "osg::DefaultUserDataContainer": (
        (_read_object_fields, 0, _INF),
        (_read_udc_fields, 0, _INF),
    ),
    "osg::UserDataContainer": ((_read_object_fields, 0, _INF),),
    "osg::BufferObject": (
        (_read_object_fields, 0, _INF),
        (_read_bufferobject_fields, 0, _INF),
    ),
    "osg::VertexBufferObject": (
        (_read_object_fields, 0, _INF),
        (_read_bufferobject_fields, 0, _INF),
    ),
    "osg::ElementBufferObject": (
        (_read_object_fields, 0, _INF),
        (_read_bufferobject_fields, 0, _INF),
    ),
    "osg::DrawArrays": _primitive_class(_read_drawarrays_fields),
    "osg::DrawArrayLengths": _primitive_class(_read_drawarraylengths_fields),
    "osg::DrawElementsUByte": _primitive_class(_make_index_body("u1")),
    "osg::DrawElementsUShort": _primitive_class(_make_index_body("<u2")),
    "osg::DrawElementsUInt": _primitive_class(_make_index_body("<u4")),
    "osg::FloatArray": _array_class(_make_vector_body(1, "<f4")),
    "osg::DoubleArray": _array_class(_make_vector_body(1, "<f8")),
    "osg::Vec2Array": _array_class(_make_vector_body(2, "<f4")),
    "osg::Vec3Array": _array_class(_make_vector_body(3, "<f4")),
    "osg::Vec4Array": _array_class(_make_vector_body(4, "<f4")),
    "osg::Vec2dArray": _array_class(_make_vector_body(2, "<f8")),
    "osg::Vec3dArray": _array_class(_make_vector_body(3, "<f8")),
    "osg::Vec4dArray": _array_class(_make_vector_body(4, "<f8")),
    "osg::Vec4ubArray": _array_class(_make_vector_body(4, "u1")),
    "osg::UByteArray": _array_class(_make_vector_body(1, "u1")),
    "osg::UIntArray": _array_class(_make_vector_body(1, "<u4")),
    "osg::IntArray": _array_class(_make_vector_body(1, "<i4")),
    "osg::UShortArray": _array_class(_make_vector_body(1, "<u2")),
}
