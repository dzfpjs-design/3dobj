"""Build a small ContextCapture-style block with tanks of known size.

One tank sits on the boundary of Tile_+000_+000 and Tile_+001_+000 so the
detector has to merge geometry across tiles. The root OSGB of each tile is a
coarser, deliberately wrong mesh; only the L19 child has the true geometry.
"""

from __future__ import annotations

import math
import zipfile
from pathlib import Path

import numpy as np

from tankextract.osgb_writer import write_geode_osgb, write_pagedlod_osgb

ORIGIN = (629024.0, 3586203.0, 0.0)
SRS = "EPSG:4548"
TILE_BOUNDARY = 40.0

# Local metres. Absolute X/Y = ORIGIN + local.
EXPECTED = [
    {
        "key": "flat",
        "x": 12.0,
        "y": 14.0,
        "r": 4.0,
        "z_base": 8.0,
        "wall_h": 9.0,
        "total_h": 9.0,
        "rise": 0.0,
        "roof": "flat",
        "stair": True,
        "girders": 1,
        "base_ring": True,
        "rim": True,
    },
    {
        "key": "cone",
        "x": 60.0,
        "y": 22.0,
        "r": 6.5,
        "z_base": 8.0,
        "wall_h": 11.0,
        "total_h": 13.4,
        "rise": 2.4,
        "roof": "cone",
        "stair": False,
        "girders": 0,
        "base_ring": False,
        "rim": False,
    },
    {
        "key": "dome",
        "x": 40.0,
        "y": 28.0,
        "r": 5.5,
        "z_base": 7.5,
        "wall_h": 8.0,
        "total_h": 10.2,
        "rise": 2.2,
        "roof": "dome",
        "stair": False,
        "girders": 0,
        "base_ring": False,
        "rim": False,
    },
]


def generate_scene(root: Path) -> Path:
    """Write ``root/E_OSGB`` and ``root/zips/E_OSGB.zip``. Return the block folder."""
    root = Path(root)
    block = root / "E_OSGB"
    fine = _concat(
        [
            _terrain(),
            _box(),
            _tank_flat(),
            _tank_cone(),
            _tank_dome(),
        ]
    )
    coarse = _concat([_terrain(step=6.0), _coarse_tanks()])
    _write_tile(block, "+000", "+000", fine, coarse, x_min=None, x_max=TILE_BOUNDARY)
    _write_tile(block, "+001", "+000", fine, coarse, x_min=TILE_BOUNDARY, x_max=None)
    _write_metadata(block)
    _zip_block(block, root / "zips" / "E_OSGB.zip")
    return block


def _write_metadata(block: Path) -> None:
    block.mkdir(parents=True, exist_ok=True)
    text = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ModelMetadata version="1">\n'
        "    <SRS>EPSG:4548</SRS>\n"
        "    <SRSOrigin>629024.000000,3586203.000000,0.000000</SRSOrigin>\n"
        "    <Texture>\n"
        "        <ColorSource>Visible</ColorSource>\n"
        "    </Texture>\n"
        "</ModelMetadata>\n"
    )
    (block / "metadata.xml").write_text(text, encoding="utf-8")


def _write_tile(block, ix, iy, fine, coarse, x_min, x_max) -> None:
    folder = block / "Data" / f"Tile_{ix}_{iy}"
    folder.mkdir(parents=True, exist_ok=True)
    fine_name = f"Tile_{ix}_{iy}_L19_0.osgb"
    root_name = f"Tile_{ix}_{iy}.osgb"
    fv, ft = _clip(fine[0], fine[1], x_min, x_max)
    cv, ct = _clip(coarse[0], coarse[1], x_min, x_max)
    write_geode_osgb(folder / fine_name, fv, ft, name=fine_name)
    write_pagedlod_osgb(folder / root_name, cv, ct, fine_name, name=root_name)


def _zip_block(block: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file in block.rglob("*"):
            if file.is_file():
                archive.write(file, file.relative_to(block).as_posix())


def _clip(vertices, triangles, x_min, x_max, overlap: float = 1.25):
    if len(triangles) == 0:
        return vertices[:0], triangles[:0]
    centroid = vertices[triangles].mean(axis=1)
    mask = np.ones(len(triangles), dtype=bool)
    if x_min is not None:
        mask &= centroid[:, 0] >= x_min - overlap
    if x_max is not None:
        mask &= centroid[:, 0] < x_max + overlap
    tri = triangles[mask]
    if len(tri) == 0:
        return vertices[:0].reshape(0, 3), tri.reshape(0, 3)
    used = np.unique(tri.reshape(-1))
    remap = np.full(len(vertices), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return vertices[used], remap[tri]


def _concat(parts):
    verts = []
    tris = []
    offset = 0
    for vertices, triangles in parts:
        if len(vertices) == 0:
            continue
        verts.append(vertices)
        tris.append(triangles + offset)
        offset += len(vertices)
    if not verts:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    return np.vstack(verts), np.vstack(tris)


def _jitter(vertices: np.ndarray, scale: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.array(vertices, dtype=np.float64, copy=True)
    out += rng.normal(0.0, scale, size=out.shape)
    return out


def _cylinder(cx, cy, z0, z1, radius, n=80, nh=6) -> tuple[np.ndarray, np.ndarray]:
    verts = []
    for i in range(nh + 1):
        z = z0 + (z1 - z0) * i / nh
        for k in range(n):
            a = 2 * math.pi * k / n
            verts.append((cx + radius * math.cos(a), cy + radius * math.sin(a), z))
    verts = np.asarray(verts, dtype=np.float64)
    tris = []
    for i in range(nh):
        for k in range(n):
            a = i * n + k
            b = i * n + (k + 1) % n
            c = (i + 1) * n + k
            d = (i + 1) * n + (k + 1) % n
            tris.append((a, c, b))
            tris.append((b, c, d))
    return verts, np.asarray(tris, dtype=np.int64)


def _disk(cx, cy, z, radius, n=80) -> tuple[np.ndarray, np.ndarray]:
    verts = [(cx, cy, z)]
    for k in range(n):
        a = 2 * math.pi * k / n
        verts.append((cx + radius * math.cos(a), cy + radius * math.sin(a), z))
    tris = [(0, k, k + 1) for k in range(1, n)]
    tris.append((0, n, 1))
    return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64)


def _annulus(cx, cy, z, r0, r1, n=80) -> tuple[np.ndarray, np.ndarray]:
    verts = []
    for radius in (r0, r1):
        for k in range(n):
            a = 2 * math.pi * k / n
            verts.append((cx + radius * math.cos(a), cy + radius * math.sin(a), z))
    tris = []
    for k in range(n):
        a = k
        b = (k + 1) % n
        c = n + k
        d = n + (k + 1) % n
        tris.append((a, c, b))
        tris.append((b, c, d))
    return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64)


def _band(cx, cy, z0, height, r_in, r_out, n=80) -> tuple[np.ndarray, np.ndarray]:
    """Vertical annular band (wind girder / base ring)."""
    outer, outer_tri = _cylinder(cx, cy, z0, z0 + height, r_out, n=n, nh=1)
    inner, inner_tri = _cylinder(cx, cy, z0, z0 + height, r_in, n=n, nh=1)
    top, top_tri = _annulus(cx, cy, z0 + height, r_in, r_out, n=n)
    bot, bot_tri = _annulus(cx, cy, z0, r_in, r_out, n=n)
    return _concat([(outer, outer_tri), (inner, inner_tri), (top, top_tri), (bot, bot_tri)])


def _cone(cx, cy, z_rim, radius, rise, n=80) -> tuple[np.ndarray, np.ndarray]:
    verts = [(cx, cy, z_rim + rise)]
    for k in range(n):
        a = 2 * math.pi * k / n
        verts.append((cx + radius * math.cos(a), cy + radius * math.sin(a), z_rim))
    tris = [(0, k, k + 1) for k in range(1, n)]
    tris.append((0, n, 1))
    return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64)


def _dome(cx, cy, z_rim, radius, rise, n=80, n_lat=10) -> tuple[np.ndarray, np.ndarray]:
    # Spherical cap (拱顶): the usual fixed dome roof.
    rs = (radius * radius + rise * rise) / (2.0 * rise)
    rings = []
    for i in range(n_lat + 1):
        rho = radius * (1.0 - i / n_lat)
        z = z_rim + math.sqrt(max(rs * rs - rho * rho, 0.0)) - (rs - rise)
        if i == n_lat:
            rings.append([(cx, cy, z)])
        else:
            rings.append(
                [
                    (cx + rho * math.cos(2 * math.pi * k / n), cy + rho * math.sin(2 * math.pi * k / n), z)
                    for k in range(n)
                ]
            )
    verts = [p for ring in rings for p in ring]
    tris = []
    for i in range(n_lat - 1):
        base = i * n
        nxt = (i + 1) * n
        for k in range(n):
            a = base + k
            b = base + (k + 1) % n
            c = nxt + k
            d = nxt + (k + 1) % n
            tris.append((a, c, b))
            tris.append((b, c, d))
    apex = len(verts) - 1
    last = (n_lat - 1) * n
    for k in range(n):
        tris.append((last + k, apex, last + (k + 1) % n))
    return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64)


def _stair(cx, cy, z0, height, r_in, r_out, turns=1.0, steps=48) -> tuple[np.ndarray, np.ndarray]:
    verts = []
    tris = []

    def add_quad(p0, p1, p2, p3):
        base = len(verts)
        verts.extend((p0, p1, p2, p3))
        tris.append((base, base + 1, base + 2))
        tris.append((base, base + 2, base + 3))

    for s in range(steps):
        a0 = 2 * math.pi * turns * s / steps
        a1 = 2 * math.pi * turns * (s + 1) / steps
        z_lo = z0 + height * s / steps
        z_hi = z0 + height * (s + 1) / steps
        def polar(radius, ang, z):
            return (cx + radius * math.cos(ang), cy + radius * math.sin(ang), z)
        # Tread at the top of the step.
        add_quad(polar(r_in, a0, z_hi), polar(r_out, a0, z_hi), polar(r_out, a1, z_hi), polar(r_in, a1, z_hi))
        # Riser at the start angle.
        add_quad(polar(r_in, a0, z_lo), polar(r_out, a0, z_lo), polar(r_out, a0, z_hi), polar(r_in, a0, z_hi))
    return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64)


def _box(cx=26.0, cy=8.0, z0=7.5, sx=6.0, sy=4.0, sz=5.0) -> tuple[np.ndarray, np.ndarray]:
    x0, x1 = cx - sx / 2, cx + sx / 2
    y0, y1 = cy - sy / 2, cy + sy / 2
    z1 = z0 + sz
    verts = np.array(
        [
            (x0, y0, z0),
            (x1, y0, z0),
            (x1, y1, z0),
            (x0, y1, z0),
            (x0, y0, z1),
            (x1, y0, z1),
            (x1, y1, z1),
            (x0, y1, z1),
        ],
        dtype=np.float64,
    )
    faces = [
        (0, 1, 2), (0, 2, 3),
        (4, 6, 5), (4, 7, 6),
        (0, 4, 5), (0, 5, 1),
        (1, 5, 6), (1, 6, 2),
        (2, 6, 7), (2, 7, 3),
        (3, 7, 4), (3, 4, 0),
    ]
    return verts, np.asarray(faces, dtype=np.int64)


def _terrain(step: float = 2.0) -> tuple[np.ndarray, np.ndarray]:
    xs = np.arange(-2.0, 82.0 + step, step)
    ys = np.arange(-2.0, 42.0 + step, step)
    rng = np.random.default_rng(1)
    verts = []
    for y in ys:
        for x in xs:
            verts.append((x, y, 7.5 + float(rng.normal(0, 0.015))))
    verts = np.asarray(verts, dtype=np.float64)
    nx, ny = len(xs), len(ys)
    tris = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i
            b = a + 1
            c = a + nx
            d = c + 1
            tris.append((a, c, b))
            tris.append((b, c, d))
    return verts, np.asarray(tris, dtype=np.int64)


def _tank_flat() -> tuple[np.ndarray, np.ndarray]:
    spec = EXPECTED[0]
    z0, z1 = spec["z_base"], spec["z_base"] + spec["wall_h"]
    r = spec["r"]
    parts = [
        _cylinder(spec["x"], spec["y"], z0, z1, r),
        _disk(spec["x"], spec["y"], z1, r),
        _annulus(spec["x"], spec["y"], z1, r - 0.02, r + 0.24),
        _band(spec["x"], spec["y"], z0 + 0.55 * spec["wall_h"], 0.16, r, r + 0.30),
        _band(spec["x"], spec["y"], z0, 0.22, r, r + 0.32),
        _stair(spec["x"], spec["y"], z0, spec["wall_h"], r + 0.45, r + 1.15),
    ]
    v, t = _concat(parts)
    return _jitter(v, 0.006, seed=11), t


def _tank_cone() -> tuple[np.ndarray, np.ndarray]:
    spec = EXPECTED[1]
    z0, z1 = spec["z_base"], spec["z_base"] + spec["wall_h"]
    parts = [
        _cylinder(spec["x"], spec["y"], z0, z1, spec["r"]),
        _cone(spec["x"], spec["y"], z1, spec["r"], spec["rise"]),
    ]
    v, t = _concat(parts)
    return _jitter(v, 0.006, seed=12), t


def _tank_dome() -> tuple[np.ndarray, np.ndarray]:
    spec = EXPECTED[2]
    z0, z1 = spec["z_base"], spec["z_base"] + spec["wall_h"]
    parts = [
        _cylinder(spec["x"], spec["y"], z0, z1, spec["r"]),
        _dome(spec["x"], spec["y"], z1, spec["r"], spec["rise"]),
    ]
    v, t = _concat(parts)
    return _jitter(v, 0.006, seed=13), t


def _coarse_tanks() -> tuple[np.ndarray, np.ndarray]:
    parts = []
    for spec in EXPECTED:
        z0 = spec["z_base"]
        z1 = z0 + spec["wall_h"] * 0.5
        parts.append(_cylinder(spec["x"], spec["y"], z0, z1, spec["r"] * 0.55, n=16, nh=1))
    return _concat(parts)
