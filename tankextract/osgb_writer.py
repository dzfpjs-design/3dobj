"""Write a ContextCapture-style OSGB (SO version 91, no binary brackets).

The byte layout matches osgDB's binary serializers for the classes this
package reads: Group is not used; a leaf is a Geode, a coarse tile is a
PagedLOD pointing at the next file. Textures are a tiny embedded JPEG so the
image-skipping path is exercised. Coordinates are local metres, Z up.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

MAGIC_LOW = 0x6C910EA1
MAGIC_HIGH = 0x1AFB4545
VERSION = 91

_ID_VEC2F = 15
_ID_VEC3F = 16
_ID_DRAWELEMENTS_UINT = 54
_GL_TRIANGLES = 4
_GL_REPEAT = 0x2901
_GL_LINEAR = 0x2601
_GL_LINEAR_MIPMAP_LINEAR = 0x2703

# 1x1 baseline JPEG. osgconv and the built-in reader both accept it as an
# embedded osg::Image file; pixels are never used (white-model output).
_MIN_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300"
    "080606070605080707070909080a0c140d0c0b0b0c1912130f"
    "141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c3031"
    "3434341f27393d38323c2e333432ffc0000b08000100010101"
    "1100ffc4001f00000105010101010101000000000000000001"
    "02030405060708090a0bffc400b51000020103030204030505"
    "04040000017d01020300041105122131410613516107227114"
    "328191a1082342b1c11552d1f02433627282090a161718191a"
    "25262728292a3435363738393a434445464748494a53545556"
    "5758595a636465666768696a737475767778797a8384858687"
    "88898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5"
    "b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2"
    "e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda000c030100"
    "02110311003f00f7fa28a2803f7fffd9"
)


class _Buf:
    def __init__(self) -> None:
        self.buf = bytearray()
        self._next_obj = 1
        self._next_array = 1

    def obj_id(self) -> int:
        value = self._next_obj
        self._next_obj += 1
        return value

    def array_id(self) -> int:
        value = self._next_array
        self._next_array += 1
        return value

    def pack(self, fmt: str, *values: float) -> None:
        self.buf += struct.pack("<" + fmt, *values)

    def u32(self, value: int) -> None:
        self.pack("I", int(value))

    def i32(self, value: int) -> None:
        self.pack("i", int(value))

    def f32(self, value: float) -> None:
        self.pack("f", float(value))

    def f64(self, value: float) -> None:
        self.pack("d", float(value))

    def bool(self, value: bool) -> None:
        self.buf.append(1 if value else 0)

    def string(self, text: str) -> None:
        raw = text.encode("utf-8")
        self.i32(len(raw))
        self.buf += raw

    def raw(self, data: bytes) -> None:
        self.buf += data


def _normals(vertices: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    normals = np.zeros_like(vertices, dtype=np.float64)
    if len(triangles) == 0:
        normals[:, 2] = 1.0
        return normals
    faces = vertices[triangles]
    face_n = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    for column in range(3):
        np.add.at(normals, triangles[:, column], face_n)
    length = np.linalg.norm(normals, axis=1)
    ok = length > 1e-12
    normals[ok] /= length[ok, None]
    normals[~ok] = (0.0, 0.0, 1.0)
    return normals


def _header(out: _Buf) -> None:
    out.u32(MAGIC_LOW)
    out.u32(MAGIC_HIGH)
    out.u32(1)  # scene
    out.u32(VERSION)
    out.u32(0)  # no schema, no binary brackets
    out.string("0")


def _object_fields(out: _Buf, name: str = "") -> None:
    out.string(name)
    out.i32(2)  # UNSPECIFIED
    out.bool(False)  # no user-data container (version >= 77)


def _node_fields(out: _Buf, stateset: bool) -> None:
    out.bool(False)  # initial bound
    out.bool(False)
    out.bool(False)
    out.bool(False)
    out.bool(False)  # callbacks
    out.bool(True)  # culling active
    out.u32(0xFFFFFFFF)
    out.bool(stateset)


def _begin_object(out: _Buf, class_name: str) -> None:
    out.string(class_name)
    out.u32(out.obj_id())


def _write_image(out: _Buf) -> None:
    # ClassName is written only when SO version > 94. ContextCapture files are 91.
    out.u32(out.obj_id())
    out.string("tex.jpg")
    out.i32(0)  # write hint
    out.i32(1)  # inline file
    out.u32(len(_MIN_JPEG))
    out.raw(_MIN_JPEG)
    _object_fields(out, "tex.jpg")


def _write_texture2d(out: _Buf) -> None:
    _begin_object(out, "osg::Texture2D")
    _object_fields(out, "")
    out.bool(False)  # state attribute callbacks
    out.bool(False)
    for enum in (_GL_REPEAT, _GL_REPEAT, _GL_REPEAT, _GL_LINEAR_MIPMAP_LINEAR, _GL_LINEAR):
        out.bool(True)
        out.u32(enum)
    out.f32(1.0)  # max anisotropy
    out.bool(True)
    out.bool(False)
    out.bool(False)
    out.bool(True)
    for _ in range(4):
        out.f64(0.0)
    out.i32(0)  # border width
    out.i32(0)  # internal format mode = USE_IMAGE_DATA_FORMAT
    out.bool(False)  # internal format
    out.bool(False)  # source format
    out.bool(False)  # source type
    out.bool(False)  # shadow comparison
    out.i32(0x0203)  # LEQUAL placeholder, value is ignored
    out.i32(0x1909)
    out.f32(0.0)
    # version 91: no image attachment, no swizzle
    out.bool(True)
    _write_image(out)
    out.i32(1)
    out.i32(1)


def _write_stateset(out: _Buf) -> None:
    _begin_object(out, "osg::StateSet")
    _object_fields(out, "")
    out.bool(False)  # modes
    out.bool(False)  # attributes
    out.bool(False)  # texture modes
    out.bool(True)  # texture attributes
    out.u32(1)  # one unit
    out.u32(1)  # one attribute in that unit
    _write_texture2d(out)
    out.i32(1)  # ON
    out.bool(False)  # no uniforms
    out.i32(0)  # rendering hint
    out.i32(0)  # bin mode
    out.i32(0)
    out.string("")
    out.bool(True)
    out.bool(False)
    out.bool(False)


def _write_inline_vec(out: _Buf, array: np.ndarray, type_id: int) -> None:
    """Geometry array struct + the inline Vec* array it points at."""
    out.bool(True)  # has array
    out.u32(out.array_id())
    out.i32(type_id)
    count = int(array.shape[0])
    out.i32(count)
    payload = np.ascontiguousarray(array, dtype="<f4")
    out.raw(payload.tobytes())
    out.bool(False)  # no per-attribute indices
    out.i32(4)  # BIND_PER_VERTEX
    out.i32(0)  # normalize


def _write_geometry(out: _Buf, vertices: np.ndarray, triangles: np.ndarray) -> None:
    vertices = np.asarray(vertices, dtype=np.float64)
    triangles = np.asarray(triangles, dtype=np.int64)
    normals = _normals(vertices, triangles)
    uvs = np.zeros((len(vertices), 2), dtype=np.float64)
    if len(vertices):
        span = np.ptp(vertices[:, :2], axis=0)
        span[span < 1e-6] = 1.0
        uvs[:, 0] = (vertices[:, 0] - vertices[:, 0].min()) / span[0]
        uvs[:, 1] = (vertices[:, 1] - vertices[:, 1].min()) / span[1]

    _begin_object(out, "osg::Geometry")
    _object_fields(out, "")
    # Drawable at version 91: stateset, no initial bound, no callbacks, three bools.
    out.bool(True)
    _write_stateset(out)
    out.bool(False)  # initial bound
    out.bool(False)  # compute bbox callback
    out.bool(False)  # shape
    out.bool(True)
    out.bool(True)
    out.bool(False)
    out.bool(False)
    out.bool(False)
    out.bool(False)
    out.bool(False)
    # PrimitiveSetList (inline, version < 112). No numInstances at version 91.
    out.u32(1)
    out.i32(_ID_DRAWELEMENTS_UINT)
    out.i32(_GL_TRIANGLES)
    indices = np.ascontiguousarray(triangles.reshape(-1), dtype="<u4")
    out.i32(int(indices.size))
    out.raw(indices.tobytes())
    # Vertex / normal / color / secondary / fog.
    out.bool(True)
    _write_inline_vec(out, vertices, _ID_VEC3F)
    out.bool(True)
    _write_inline_vec(out, normals, _ID_VEC3F)
    out.bool(False)
    out.bool(False)
    out.bool(False)
    # One texcoord array.
    out.bool(True)
    out.u32(1)
    _write_inline_vec(out, uvs, _ID_VEC2F)
    out.bool(False)  # vertex attribs
    out.bool(False)  # fast path hint


def _write_geode_body(out: _Buf, vertices: np.ndarray, triangles: np.ndarray, name: str) -> None:
    _begin_object(out, "osg::Geode")
    _object_fields(out, name)
    _node_fields(out, stateset=False)
    out.bool(True)  # has drawables
    out.u32(1)
    _write_geometry(out, vertices, triangles)


def write_geode_osgb(path: str | Path, vertices: np.ndarray, triangles: np.ndarray, name: str = "") -> None:
    out = _Buf()
    _header(out)
    _write_geode_body(out, vertices, triangles, name)
    Path(path).write_bytes(out.buf)


def write_pagedlod_osgb(
    path: str | Path,
    vertices: np.ndarray,
    triangles: np.ndarray,
    child_filename: str,
    name: str = "",
) -> None:
    """Coarse tile: inline geode plus the filename of the finer file."""
    out = _Buf()
    _header(out)
    _begin_object(out, "osg::PagedLOD")
    _object_fields(out, name)
    _node_fields(out, stateset=False)
    # LOD
    out.i32(0)  # center mode: bounding sphere
    out.bool(False)  # no user center
    out.i32(1)  # pixel size on screen
    out.bool(True)
    out.u32(2)
    out.f32(0.0)
    out.f32(500.0)
    out.f32(500.0)
    out.f32(1.0e30)
    # PagedLOD
    out.bool(True)  # database-path checker
    out.bool(False)  # empty database path
    out.u32(0)  # children that cannot expire
    out.bool(False)
    out.bool(True)  # filenames
    out.u32(2)
    out.string("")
    out.string(child_filename)
    out.u32(2)  # priorities
    out.f32(0.0)
    out.f32(1.0)
    out.f32(0.0)
    out.f32(1.0)
    out.bool(True)  # one inline child
    out.u32(1)
    _write_geode_body(out, vertices, triangles, name + "_coarse")
    Path(path).write_bytes(out.buf)
