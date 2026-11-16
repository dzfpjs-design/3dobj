"""Ancillary steel structures measured from the mesh: stair towers, roof platforms,
sphere accessories (pedestals, bracing, meridian stairs), pipe racks and pipes.

Every detector works on mesh faces ``F`` = (N, 7): centroid x, y, z, normal
nx, ny, nz, area (absolute coordinates).  Results are ``Structure`` records
with their measured dimensions and a list of primitive *parts* (rods, beams,
boxes, prisms) in absolute coordinates; the Blender exporter turns every part
into one named object under the structure's (or tank's) parent Empty.

Primitive parts (all coordinates absolute, metres):
  {"name", "type": "rod",   "p0", "p1", "radius"}             round bar / pipe
  {"name", "type": "beam",  "p0", "p1", "width", "depth"}     rectangular bar, top face at p0-p1
  {"name", "type": "box",   "center", "size": [l, w, h], "yaw_deg"}
  {"name", "type": "prism", "polygon": [[x, y], ...], "z0", "z1"}
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from tankextract import grounding as G


@dataclass
class Structure:
    name: str
    kind: str  # stair_tower, pipe_rack, pipe, platform, ...
    parent: str = ""  # tank id when the structure belongs to a tank
    dims: dict = field(default_factory=dict)
    parts: list = field(default_factory=list)
    anchor: tuple = (0.0, 0.0, 0.0)
    geometry: dict = field(default_factory=dict)  # GeoJSON geometry (absolute)
    block: str = ""
    srs: str = ""
    origin: tuple = (0.0, 0.0, 0.0)
    tank_ref: object = field(default=None, repr=False, compare=False)


# ----------------------------------------------------------------- helpers ---

def collect_faces(tiles, boxes) -> np.ndarray:
    """(N, 7) faces of absolute-coordinate tiles whose centroid lies in any box (x0, y0, x1, y1)."""
    if not boxes:
        return np.zeros((0, 7))
    bx = np.asarray(boxes, dtype=float)
    lo, hi = bx[:, :2].min(axis=0), bx[:, 2:].max(axis=0)
    chunks = []
    for vertices, triangles in tiles:
        if len(vertices) == 0 or len(triangles) == 0:
            continue
        a, b, c = vertices[triangles[:, 0]], vertices[triangles[:, 1]], vertices[triangles[:, 2]]
        cen = (a + b + c) / 3.0
        sel = (cen[:, 0] > lo[0]) & (cen[:, 0] < hi[0]) & (cen[:, 1] > lo[1]) & (cen[:, 1] < hi[1])
        if not sel.any():
            continue
        inside = np.zeros(len(cen), bool)
        for x0, y0, x1, y1 in bx:
            inside |= sel & (cen[:, 0] > x0) & (cen[:, 0] < x1) & (cen[:, 1] > y0) & (cen[:, 1] < y1)
        if not inside.any():
            continue
        n = np.cross(b[inside] - a[inside], c[inside] - a[inside])
        length = np.linalg.norm(n, axis=1)
        nn = n / np.maximum(length, 1e-12)[:, None]
        chunks.append(np.column_stack([cen[inside], nn, length / 2.0]))
    return np.concatenate(chunks) if chunks else np.zeros((0, 7))


def _rot(deg: float):
    a = math.radians(deg)
    return np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])


def _peaks(hist: np.ndarray, threshold: float) -> list[int]:
    out = []
    for i in range(len(hist)):
        lo = hist[i - 1] if i > 0 else -1
        hi = hist[i + 1] if i + 1 < len(hist) else -1
        if hist[i] >= threshold and hist[i] >= lo and hist[i] > hi:
            out.append(i)
    return out


def _smooth(h: np.ndarray, k: int = 1) -> np.ndarray:
    if k <= 0:
        return h
    w = np.ones(2 * k + 1) / (2 * k + 1)
    return np.convolve(h, w, mode="same")


def min_area_rect(xy: np.ndarray, lo_pct: float = 4, hi_pct: float = 96):
    """Robust oriented rectangle: (cx, cy, length, width, axis_deg) with length >= width."""
    best = None
    for deg in np.arange(0.0, 90.0, 1.0):
        u, v = _rot(deg)
        a, b = xy @ u, xy @ v
        a0, a1 = np.percentile(a, [lo_pct, hi_pct])
        b0, b1 = np.percentile(b, [lo_pct, hi_pct])
        area = (a1 - a0) * (b1 - b0)
        if best is None or area < best[0]:
            best = (area, deg, a0, a1, b0, b1)
    _area, deg, a0, a1, b0, b1 = best
    u, v = _rot(deg)
    c = u * (a0 + a1) / 2 + v * (b0 + b1) / 2
    length, width = a1 - a0, b1 - b0
    if width > length:
        length, width, deg = width, length, deg + 90.0
    return float(c[0]), float(c[1]), float(length), float(width), float(deg % 180.0)


def _p(x, y, z):
    return [float(x), float(y), float(z)]


def rod(name, p0, p1, radius):
    return {"name": name, "type": "rod", "p0": list(map(float, p0)), "p1": list(map(float, p1)), "radius": float(radius)}


def beam(name, p0, p1, width, depth):
    return {"name": name, "type": "beam", "p0": list(map(float, p0)), "p1": list(map(float, p1)),
            "width": float(width), "depth": float(depth)}


def box(name, center, size, yaw_deg):
    return {"name": name, "type": "box", "center": list(map(float, center)), "size": list(map(float, size)),
            "yaw_deg": float(yaw_deg)}


def cylinder(name, center, z0, z1, radius, segments=96):
    """Closed vertical cylinder (foundations)."""
    return {"name": name, "type": "cylinder", "center": [float(center[0]), float(center[1])], "z0": float(z0),
            "z1": float(z1), "radius": float(radius), "segments": int(segments)}


def prism(name, polygon, z0, z1):
    return {"name": name, "type": "prism", "polygon": [[float(x), float(y)] for x, y in polygon],
            "z0": float(z0), "z1": float(z1)}


def polyline_rods(name, points, radius):
    return [rod(name, points[i], points[i + 1], radius) for i in range(len(points) - 1)]


def railing(prefix, a, b, z_base, height, post_step=1.5, z_base_b=None, top_r=0.025, post_r=0.025):
    """Top + mid rail and posts along a-b (xy), base height z_base (z_base_b at b for sloped runs)."""
    zb = z_base if z_base_b is None else z_base_b
    a3 = np.array([a[0], a[1], z_base]); b3 = np.array([b[0], b[1], zb])
    up = np.array([0, 0, height])
    parts = [rod(f"{prefix}_top", a3 + up, b3 + up, top_r), rod(f"{prefix}_mid", a3 + up / 2, b3 + up / 2, top_r * 0.8)]
    L = float(np.hypot(*(b3[:2] - a3[:2])))
    n = max(1, int(math.ceil(L / post_step)))
    for k in range(n + 1):
        p = a3 + (b3 - a3) * (k / n)
        parts.append(rod(f"{prefix}_posts", p, p + up, post_r))
    return parts


def rect_corners(cx, cy, length, width, deg):
    u, v = _rot(deg)
    c = np.array([cx, cy])
    return [c + u * su * length / 2 + v * sv * width / 2 for su, sv in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


# ------------------------------------------------------------ stair towers ---

def detect_stair_tower(F: np.ndarray, x: float, y: float, z_ground: float, z_top: float,
                       length: float = 3.0, width: float = 3.0, axis_deg: float = 0.0,
                       rail_height: float = 1.05) -> dict | None:
    """Measure a free-standing stair tower around (x, y) from its frame faces.

    Footprint: robust oriented rectangle of the vertical faces.  Landings: peaks
    of the upward-face area histogram inside the footprint, regularised to the
    flight rise.  Returns the dimensions, or None if there is no tower.
    """
    reach = max(length, width) / 2 + 0.8
    sel = (np.abs(F[:, 0] - x) < reach) & (np.abs(F[:, 1] - y) < reach) & (F[:, 2] > z_ground + 0.4) & (F[:, 2] < z_top + 1.5)
    G = F[sel]
    vert = G[np.abs(G[:, 5]) < 0.3]
    if len(vert) < 200:
        return None
    # Footprint from vertical faces near the expected box.
    u0, v0 = _rot(axis_deg)
    rel = vert[:, :2] - [x, y]
    keep = (np.abs(rel @ u0) < length / 2 + 0.4) & (np.abs(rel @ v0) < width / 2 + 0.4)
    if keep.sum() < 100:
        return None
    cx, cy, L, W, deg = min_area_rect(vert[keep, :2], 3, 97)
    u, v = _rot(deg)
    rel = G[:, :2] - [cx, cy]
    a, b = rel @ u, rel @ v
    inside = (np.abs(a) < L / 2 - 0.15) & (np.abs(b) < W / 2 - 0.15)
    up = G[inside & (G[:, 5] > 0.85)]
    bins = np.arange(z_ground + 0.4, z_top + 0.6, 0.1)
    h, e = np.histogram(up[:, 2], bins=bins, weights=up[:, 6])
    h = _smooth(h, 1)
    if h.max() <= 0:
        return None
    thr = 0.25 * np.median(np.sort(h)[-8:])
    pk = [float(e[i] + 0.05) for i in _peaks(h, thr)]
    # Merge peaks closer than 0.5 m (keep the stronger one).
    merged = []
    for z in pk:
        if merged and z - merged[-1] < 0.5:
            if h[int((z - e[0]) / 0.1)] > h[int((merged[-1] - e[0]) / 0.1)]:
                merged[-1] = z
        else:
            merged.append(z)
    top = max(merged) if merged else z_top
    diffs = np.diff(merged)
    diffs = diffs[(diffs > 0.9) & (diffs < 3.2)]
    rise = float(np.median(diffs)) if len(diffs) else 1.5
    levels = []
    zc = top
    while zc > z_ground + 0.6 * rise:
        near = [m for m in merged if abs(m - zc) < 0.35 * rise]
        zc = min(near, key=lambda m: abs(m - zc)) if near else zc
        levels.append(round(zc, 3))
        zc -= rise
    levels = sorted(levels)
    measured = sum(1 for lv in levels if any(abs(lv - m) < 0.05 for m in merged))
    return {
        "x": cx, "y": cy, "length": L, "width": W, "axis_deg": deg,
        "z_ground": float(z_ground), "z_top": float(top), "levels": levels, "rise": rise,
        "levels_measured": measured, "rail_height": rail_height,
    }


def stair_tower_parts(t: dict, prefix: str) -> list:
    """Open steel frame: 4 corner columns, ring beams + landing plates at every level,
    switch-back flights between levels, rails on flights, landings and the top."""
    parts = []
    cx, cy, L, W, deg = t["x"], t["y"], t["length"], t["width"], t["axis_deg"]
    u, v = _rot(deg)
    c = np.array([cx, cy])
    zg, top, levels = t["z_ground"], t["z_top"], t["levels"]
    rh = t.get("rail_height", 1.05)
    corners = rect_corners(cx, cy, L, W, deg)
    col = 0.2
    for k, p in enumerate(corners, start=1):
        parts.append(box(f"{prefix}_column_{k:02d}", (p[0], p[1], (zg + top + rh) / 2), (col, col, top + rh - zg), deg))
    ring = corners + [corners[0]]
    for i, z in enumerate(levels, start=1):
        for k in range(4):
            parts.append(beam(f"{prefix}_beam_{i:02d}", (*ring[k], z), (*ring[k + 1], z), 0.12, 0.18))
    # Diagonal bracing on the four faces, one diagonal per storey, alternating.
    zz = [zg] + levels
    for i in range(len(zz) - 1):
        for k in range(4):
            p0, p1 = ring[k], ring[k + 1]
            if i % 2:
                p0, p1 = p1, p0
            parts.append(rod(f"{prefix}_brace", (*p0, zz[i]), (*p1, zz[i + 1]), 0.04))
    # Flights: run along the long axis, each in one half of the width, alternating.
    land = min(1.0, 0.28 * L)
    half_w = W / 2 - 0.05
    fw = max(0.6, half_w - 0.05)
    for i in range(len(zz) - 1):
        z0, z1 = zz[i], zz[i + 1]
        side = -1 if i % 2 == 0 else 1
        dirn = 1 if i % 2 == 0 else -1
        off = v * side * (W / 4)
        a = c + off - u * dirn * (L / 2 - land)
        b = c + off + u * dirn * (L / 2 - land)
        n_steps = max(3, int(round((z1 - z0) / 0.18)))
        for s_i in range(n_steps):
            f0 = s_i / n_steps
            p = a + (b - a) * (f0 + 0.5 / n_steps)
            zs = z0 + (z1 - z0) * (s_i + 1) / n_steps
            parts.append(box(f"{prefix}_flight_{i + 1:02d}", (p[0], p[1], zs - 0.025),
                             (float(np.linalg.norm(b - a)) / n_steps + 0.03, fw, 0.05), deg))
        for sgn in (-1, 1):
            e = v * sgn * fw / 2
            parts.append(beam(f"{prefix}_flight_{i + 1:02d}", (*(a + e), z0), (*(b + e), z1), 0.06, 0.2))
        e_in = -v * side * fw / 2
        parts += railing(f"{prefix}_rail_flight_{i + 1:02d}", a + e_in, b + e_in, z0, rh * 0.9, post_step=1.2, z_base_b=z1)
    # Landings at each level: platforms at both ends (half-landings), full width.
    for i, z in enumerate(levels, start=1):
        for sgn in (-1, 1):
            end = c + u * sgn * (L / 2 - land / 2)
            poly = rect_corners(end[0], end[1], land, W - 0.1, deg)
            parts.append(prism(f"{prefix}_landing_{i:02d}", poly, z - 0.05, z))
    # Top: full deck plate + perimeter rail.
    parts.append(prism(f"{prefix}_top_deck", rect_corners(cx, cy, L - 0.05, W - 0.05, deg), top - 0.08, top))
    open_sides = set(t.get("open_sides", []))
    for k in range(4):
        if k in open_sides:
            continue
        parts += railing(f"{prefix}_rail_top", ring[k], ring[k + 1], top, rh)
    for j, br in enumerate(t.get("bridges", []), start=1):
        parts.append(prism(f"{prefix}_bridge_{j:02d}", br["polygon"], br["z"] - 0.08, br["z"]))
    return parts


# ------------------------------------------------------------- pipe racks ---

def site_axis(F: np.ndarray) -> float:
    """Dominant plan direction (deg, 0-90) of vertical faces: plants are laid out on a grid."""
    vert = F[np.abs(F[:, 5]) < 0.2]
    if len(vert) < 50:
        return 0.0
    ang = np.degrees(np.arctan2(vert[:, 4], vert[:, 3])) % 90.0
    h, e = np.histogram(ang, bins=90, range=(0, 90), weights=vert[:, 6])
    h = h + np.roll(h, 1) + np.roll(h, -1)
    return float(e[int(np.argmax(h))] + 0.5)


def _label(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """4-connected components of a boolean grid (numpy min-propagation + pointer jumping)."""
    ids = np.arange(1, mask.size + 1).reshape(mask.shape)
    lab = np.where(mask, ids, 0)
    big = mask.size + 10
    while True:
        cur = np.where(mask, lab, big)
        nb = cur.copy()
        nb[1:, :] = np.minimum(nb[1:, :], cur[:-1, :])
        nb[:-1, :] = np.minimum(nb[:-1, :], cur[1:, :])
        nb[:, 1:] = np.minimum(nb[:, 1:], cur[:, :-1])
        nb[:, :-1] = np.minimum(nb[:, :-1], cur[:, 1:])
        nb = np.where(mask, nb, 0)
        flat = nb.ravel()
        for _ in range(4):  # jump: label := label of the cell it points at
            pos = flat > 0
            flat[pos] = np.minimum(flat[pos], flat[flat[pos] - 1])
        nb = flat.reshape(mask.shape)
        if np.array_equal(nb, lab):
            break
        lab = nb
    uniq, inv = np.unique(lab, return_inverse=True)
    inv = inv.reshape(lab.shape)
    if uniq[0] != 0:
        inv = inv + 1
    return inv, int((uniq > 0).sum())


def _runs_mask(occ: np.ndarray, min_len: int, gap: int) -> np.ndarray:
    """Cells that belong to a run (along axis 1) of occupied cells >= min_len, closing gaps <= gap."""
    out = np.zeros_like(occ)
    for r in range(occ.shape[0]):
        row = occ[r]
        idx = np.flatnonzero(row)
        if len(idx) == 0:
            continue
        start = prev = idx[0]
        for k in list(idx[1:]) + [None]:
            if k is not None and k - prev <= gap + 1:
                prev = k
                continue
            if prev - start + 1 >= min_len:
                out[r, start:prev + 1] = True
            if k is not None:
                start = prev = k
    return out


def elevated_mask(F: np.ndarray, tanks, ground: np.ndarray, lo: float = 1.6, hi: float = 14.0,
                  exclude_boxes=()) -> np.ndarray:
    """Faces that are lifted off the ground and are not part of a tank (or an excluded box)."""
    h = F[:, 2] - ground
    keep = (h > lo) & (h < hi)
    for t in tanks:
        r = t.radius + 0.7
        sph = getattr(t, "sphere", None)
        if sph:
            r = max(t.radius, float(sph.get("leg_rho", 0.0)) + 0.6) + 0.6
        d2 = (F[:, 0] - t.center_x) ** 2 + (F[:, 1] - t.center_y) ** 2
        keep &= d2 > r * r
    for item in exclude_boxes:
        cx, cy, L, W, deg = item[:5]
        zmin, zmax = (item[5], item[6]) if len(item) > 6 else (-1e9, 1e9)
        u, v = _rot(deg)
        rel = F[:, :2] - [cx, cy]
        keep &= ~((np.abs(rel @ u) < L / 2) & (np.abs(rel @ v) < W / 2) & (F[:, 2] > zmin) & (F[:, 2] < zmax))
    return keep


def ground_grid(F: np.ndarray, cell: float = 2.0, window: int = 3):
    """Ground height function from the faces: 5th-percentile upward face per cell, then the
    20th percentile over a (2*window+1)^2 neighbourhood (removes roofs and racks)."""
    up = F[F[:, 5] > 0.8]
    x0, y0 = F[:, 0].min(), F[:, 1].min()
    nx = int((F[:, 0].max() - x0) / cell) + 1
    ny = int((F[:, 1].max() - y0) / cell) + 1
    ix = np.clip(((up[:, 0] - x0) / cell).astype(int), 0, nx - 1)
    iy = np.clip(((up[:, 1] - y0) / cell).astype(int), 0, ny - 1)
    key = ix * ny + iy
    order = np.lexsort((up[:, 2], key))
    key, zz = key[order], up[order, 2]
    starts = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    ends = np.r_[starts[1:], len(key)]
    g = np.full(nx * ny, np.nan)
    g[key[starts]] = zz[starts + (0.05 * (ends - starts)).astype(int)]
    g = g.reshape(nx, ny)
    pad = np.pad(g, window, constant_values=np.nan)
    win = np.lib.stride_tricks.sliding_window_view(pad, (2 * window + 1, 2 * window + 1))
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = np.nanpercentile(win.reshape(nx, ny, -1), 20, axis=2)
    fill = float(np.nanmedian(out)) if np.isfinite(out).any() else 0.0
    out = np.where(np.isnan(out), fill, out)

    def at(x, y):
        i = np.clip(((np.asarray(x) - x0) / cell).astype(int), 0, nx - 1)
        j = np.clip(((np.asarray(y) - y0) / cell).astype(int), 0, ny - 1)
        return out[i, j]
    return at


def rack_segments(F: np.ndarray, elev: np.ndarray, axis_deg: float, cell: float = 0.25,
                  min_length: float = 8.0, min_width: float = 0.3, max_width: float = 9.0) -> list[dict]:
    """Long straight strips of elevated faces along the two site directions."""
    out = []
    E = F[elev]
    if len(E) < 100:
        return out
    for deg in (axis_deg, axis_deg + 90.0):
        u, v = _rot(deg)
        U = E[:, :2] @ u
        V = E[:, :2] @ v
        u0, v0 = U.min(), V.min()
        iu = ((U - u0) / cell).astype(int)
        iv = ((V - v0) / cell).astype(int)
        occ = np.zeros((iv.max() + 1, iu.max() + 1), np.int32)
        np.add.at(occ, (iv, iu), 1)
        runs = _runs_mask(occ >= 2, int(min_length / cell), int(1.5 / cell))
        # bridge gaps of up to ~1 m across the strip (between pipes of one rack)
        grown = runs.copy()
        for sh in range(1, int(0.5 / cell) + 1):
            grown[sh:] |= runs[:-sh]
            grown[:-sh] |= runs[sh:]
        lab, n = _label(grown)
        lab = np.where(runs, lab, 0)
        for k in range(1, n + 1):
            rr, cc = np.nonzero(lab == k)
            if len(rr) < (min_length / cell) * 2:
                continue
            # row profile: rows with long runs
            rows, counts = np.unique(rr, return_counts=True)
            good = rows[counts >= 0.5 * counts.max()]
            va, vb = v0 + good.min() * cell, v0 + (good.max() + 1) * cell
            ua, ub = u0 + np.percentile(cc, 1) * cell, u0 + (np.percentile(cc, 99) + 1) * cell
            width, length = vb - va, ub - ua
            if width < min_width or length < min_length or width > max_width:
                continue
            out.append({"axis_deg": float(deg % 360.0), "u0": float(ua), "u1": float(ub), "v0": float(va), "v1": float(vb),
                        "length": float(length), "width": float(width)})
    return out


def _segment_faces(F, seg, pad_v=0.4):
    u, v = _rot(seg["axis_deg"])
    U, V = F[:, :2] @ u, F[:, :2] @ v
    sel = (U > seg["u0"] - 0.3) & (U < seg["u1"] + 0.3) & (V > seg["v0"] - pad_v) & (V < seg["v1"] + pad_v)
    return sel, U, V, u, v


def find_pipes_in_section(Vs, Zs, NV, NZ, Us, length, r_min=0.04, r_max=0.45, cell=0.03,
                          min_cover=0.3, min_votes=40):
    """Circle Hough in the (v, z) cross-section plane using face normals: a face on a pipe of
    radius r votes for the centre at p - r * n.  Returns [{v, z, r, u0, u1, cover, votes}]."""
    n2 = np.hypot(NV, NZ)
    ok = n2 > 0.75  # normal lies in the cross-section plane (pipe surface along u)
    Vs, Zs, NV, NZ, Us = Vs[ok], Zs[ok], NV[ok] / n2[ok], NZ[ok] / n2[ok], Us[ok]
    if len(Vs) < min_votes:
        return []
    v0, z0 = Vs.min() - r_max, Zs.min() - r_max
    nv = int((Vs.max() + r_max - v0) / cell) + 2
    nz = int((Zs.max() + r_max - z0) / cell) + 2
    radii = np.arange(r_min, r_max + 1e-9, 0.02)
    best = []
    acc = np.zeros((len(radii), nv, nz), np.float32)
    for k, r in enumerate(radii):
        cv = ((Vs - r * NV - v0) / cell).astype(int)
        cz = ((Zs - r * NZ - z0) / cell).astype(int)
        np.add.at(acc[k], (cv, cz), 1.0)
    # 3x3 spatial smoothing
    sm = acc.copy()
    for dv in (-1, 0, 1):
        for dz in (-1, 0, 1):
            if dv or dz:
                sm += np.roll(np.roll(acc, dv, axis=1), dz, axis=2)
    used = np.zeros(len(Vs), bool)
    order = np.argsort(sm.ravel())[::-1]
    out = []
    for flat in order[:6000]:
        k, i, j = np.unravel_index(flat, sm.shape)
        votes = sm[k, i, j]
        if votes < min_votes:
            break
        r = radii[k]
        cvv, czz = v0 + (i + 0.5) * cell, z0 + (j + 0.5) * cell
        # Pipes do not intersect: skip circles overlapping an accepted one.
        if any(math.hypot(cvv - q["v"], czz - q["z"]) < (r + q["r"]) * 0.9 for q in out):
            continue
        d = np.hypot(Vs - cvv, Zs - czz)
        on = np.abs(d - r) < max(0.03, 0.15 * r)
        # normals must point away from the centre
        dirn = ((Vs - cvv) * NV + (Zs - czz) * NZ) / np.maximum(d, 1e-6)
        on &= (dirn > 0.7) & ~used
        if on.sum() < min_votes * 0.6:
            continue
        ang = np.degrees(np.arctan2(Zs[on] - czz, Vs[on] - cvv))
        arc = len(np.unique((ang // 30).astype(int))) * 30
        if arc < 120:
            continue
        ub = np.unique(np.floor(Us[on] / 0.5))
        cover = len(ub) * 0.5 / max(length, 0.5)
        if cover < min_cover:
            continue
        used |= on
        ua, ub_ = np.percentile(Us[on], [1, 99])
        out.append({"v": float(cvv), "z": float(czz), "r": float(r), "u0": float(ua), "u1": float(ub_),
                    "cover": float(min(cover, 1.0)), "votes": int(on.sum()), "arc": int(arc)})
    return out


def measure_rack(F: np.ndarray, ground: np.ndarray, elev: np.ndarray, seg: dict) -> dict | None:
    """Pipes (circle Hough on the cross-section), tier levels and bents of one strip."""
    sel, U, V, u, v = _segment_faces(F, seg)
    sel &= elev | (F[:, 2] - ground > 0.3)
    G = F[sel]
    if len(G) < 200:
        return None
    Us, Vs, Zs = U[sel], V[sel], G[:, 2]
    NV = G[:, 3:5] @ v
    NZ = G[:, 5]
    gz = float(np.median(ground[sel]))
    h = Zs - gz
    length = seg["u1"] - seg["u0"]
    lifted = h > 1.2
    pipes = find_pipes_in_section(Vs[lifted], Zs[lifted], NV[lifted], NZ[lifted], Us[lifted], length)
    # Building check: vertical faces along both long sides covering most of the height.
    vert = np.abs(NZ) < 0.25
    top = float(np.percentile(Zs[lifted], 98)) if lifted.any() else gz + 2
    side = vert & (h > 0.3) & ((np.abs(Vs - seg["v0"]) < 0.5) | (np.abs(Vs - seg["v1"]) < 0.5))
    ub = np.floor((Us[side] - seg["u0"]) / 0.5).astype(int)
    wall_bins = 0
    nb = max(1, int(length / 0.5))
    for k in np.unique(ub):
        zz = h[side][ub == k]
        if len(zz) > 8 and (np.histogram(zz, bins=8, range=(0.3, top - gz))[0] > 0).mean() > 0.75:
            wall_bins += 1
    wall_frac = wall_bins / nb
    up = (NZ > 0.85) & lifted
    roof_cells = len(np.unique(np.floor(Us[up & (Zs > top - 0.6)] / 0.5) * 1000 + np.floor(Vs[up & (Zs > top - 0.6)] / 0.5)))
    roof_frac = roof_cells / max(1.0, (length / 0.5) * (seg["width"] / 0.5))
    # Tier levels: heights where upward faces (beam tops, pipe bottoms on beams) cover a
    # large share of the strip's plan area.
    tier_z = []
    if up.any():
        zb = np.arange(gz + 1.2, top + 0.3, 0.1)
        cells_total = max(1.0, (length / 0.5) * max(seg["width"], 0.5) / 0.5)
        cov = []
        for z_ in zb:
            m = up & (np.abs(Zs - z_) < 0.08)
            cov.append(len(np.unique(np.floor(Us[m] / 0.5) * 1000 + np.floor(Vs[m] / 0.5))) / cells_total)
        cov = np.array(cov)
        thr = max(0.12, 0.35 * cov.max())
        for i in _peaks(cov, thr):
            if tier_z and zb[i] - tier_z[-1] < 0.45:
                if cov[i] > cov[int(round((tier_z[-1] - zb[0]) / 0.1))]:
                    tier_z[-1] = float(zb[i])
                continue
            tier_z.append(float(zb[i]))
    pipes = [p for p in pipes if p["r"] >= 0.05 and p["arc"] >= 150 and p["cover"] >= 0.4]
    # Bents: vertical faces at the strip edges reaching from near the ground to the lowest tier.
    bents = []
    if tier_z:
        lowest = min(tier_z)
        colf = vert & (h > 0.3) & (Zs < lowest) & ((np.abs(Vs - seg["v0"]) < 0.6) | (np.abs(Vs - seg["v1"]) < 0.6) | (np.abs(Vs - 0.5 * (seg["v0"] + seg["v1"])) < 0.4))
        bins = np.arange(seg["u0"], seg["u1"] + 0.4, 0.4)
        hits = []
        for k in range(len(bins) - 1):
            m = colf & (Us >= bins[k]) & (Us < bins[k + 1])
            if m.sum() < 6:
                continue
            if (np.histogram(Zs[m], bins=6, range=(gz + 0.3, lowest))[0] > 0).mean() >= 0.67:
                hits.append(k)
        groups = []
        for k in hits:
            if groups and k - groups[-1][-1] <= 2:
                groups[-1].append(k)
            else:
                groups.append([k])
        bents = [float(0.5 * (bins[g[0]] + bins[g[-1] + 1])) for g in groups]
        merged_b = []
        for b_ in bents:
            if merged_b and b_ - merged_b[-1] < 1.5:
                merged_b[-1] = 0.5 * (merged_b[-1] + b_)
            else:
                merged_b.append(b_)
        bents = merged_b
    return {"pipes": pipes, "tiers": tier_z, "bents": bents, "ground": gz, "top": top,
            "wall_frac": float(wall_frac), "roof_frac": float(roof_frac)}


def rack_parts(prefix: str, seg: dict, m: dict, col: float = 0.25) -> list:
    """Steel frame (columns at every bent, cross beams + longitudinal beams per tier) and pipes."""
    parts = []
    u, v = _rot(seg["axis_deg"])
    gz, tiers = m["ground"], sorted(m["tiers"])
    if not tiers:
        return parts
    top = tiers[-1]
    bents = list(m["bents"])
    if len(bents) < 2:
        step = 6.0
        n = max(1, int(round((seg["u1"] - seg["u0"]) / step)))
        bents = [seg["u0"] + 0.3 + k * (seg["u1"] - seg["u0"] - 0.6) / n for k in range(n + 1)]
        m["bents_source"] = "assumed (6 m spacing)"
    va, vb = seg["v0"] + col / 2, seg["v1"] - col / 2

    def P(uu, vv, zz):
        xy = u * uu + v * vv
        return (float(xy[0]), float(xy[1]), float(zz))

    for k, b in enumerate(bents, start=1):
        for side, vv in (("A", va), ("B", vb)):
            c = P(b, vv, 0.0)
            parts.append(box(f"{prefix}_column_{k:02d}{side}", (c[0], c[1], (gz + top) / 2), (col, col, top - gz), seg["axis_deg"]))
        for i, z in enumerate(tiers, start=1):
            parts.append(beam(f"{prefix}_crossbeam_t{i}", P(b, va - col / 2, z), P(b, vb + col / 2, z), 0.2, 0.25))
    for i, z in enumerate(tiers, start=1):
        for side, vv in (("A", va), ("B", vb)):
            parts.append(beam(f"{prefix}_longbeam_t{i}{side}", P(bents[0], vv, z), P(bents[-1], vv, z), 0.15, 0.2))
    for j, p in enumerate(sorted(m["pipes"], key=lambda q: (q["z"], q["v"])), start=1):
        parts.append(rod(f"{prefix}_pipe_{j:02d}", P(p["u0"], p["v"], p["z"]), P(p["u1"], p["v"], p["z"]), p["r"]))
    return parts


# ------------------------------------------------------- tank accessories ---

_rng_seed = 12345


def ransac_circle(xy: np.ndarray, tol: float = 0.04, rmin: float = 0.04, rmax: float = 0.7,
                  iters: int = 250, min_arc: int = 210):
    """Robust circle (centre, radius, inliers) needing >= min_arc degrees of visible outline."""
    n = len(xy)
    if n < 12:
        return None
    rng = np.random.default_rng(_rng_seed + n)
    best = None
    for _ in range(iters):
        p1, p2, p3 = xy[rng.choice(n, 3, replace=False)]
        A = np.array([[p2[0] - p1[0], p2[1] - p1[1]], [p3[0] - p1[0], p3[1] - p1[1]]])
        if abs(np.linalg.det(A)) < 1e-9:
            continue
        b = 0.5 * np.array([p2 @ p2 - p1 @ p1, p3 @ p3 - p1 @ p1])
        c = np.linalg.solve(A, b)
        r = float(np.linalg.norm(p1 - c))
        if r < rmin or r > rmax:
            continue
        inl = np.abs(np.linalg.norm(xy - c, axis=1) - r) < tol
        ang = np.degrees(np.arctan2(xy[inl, 1] - c[1], xy[inl, 0] - c[0]))
        arc = len(np.unique((ang // 30).astype(int))) * 30
        if arc < min_arc:
            continue
        if best is None or inl.sum() > best[2]:
            best = (c, r, int(inl.sum()))
    return best


def measure_round_member(F: np.ndarray, x: float, y: float, z0: float, z1: float, reach: float = 0.9,
                         rmax: float = 0.7, band: float = 0.5) -> dict | None:
    """Diameter of a vertical round member (leg, pipe) from RANSAC circles on horizontal slices."""
    near = (np.abs(F[:, 0] - x) < reach) & (np.abs(F[:, 1] - y) < reach) & (F[:, 2] > z0) & (F[:, 2] < z1)
    G = F[near]
    d = np.hypot(G[:, 0] - x, G[:, 1] - y)
    G = G[d < reach]
    ds, cs = [], []
    for zb in np.arange(z0, z1 - band + 1e-6, band):
        m = (G[:, 2] > zb) & (G[:, 2] < zb + band)
        if m.sum() < 15:
            continue
        res = ransac_circle(G[m, :2] - [x, y], rmax=rmax)
        if res:
            ds.append(2 * res[1])
            cs.append(res[0])
    if len(ds) < 3:
        return None
    return {"diameter": float(np.median(ds)), "samples": len(ds), "iqr": float(np.subtract(*np.percentile(ds, [75, 25]))),
            "dx": float(np.median([c[0] for c in cs])), "dy": float(np.median([c[1] for c in cs]))}


def sphere_supports(F: np.ndarray, tank) -> dict:
    """Measured leg diameter, concrete pedestals and X-bracing heights of a spherical tank."""
    s = tank.sphere
    out = {}
    c = np.array([tank.center_x, tank.center_y])
    rho = float(s.get("leg_rho") or tank.radius * 0.97)
    zg = float(tank.z_base)
    zc = float(s["center_z"])
    legs = [math.radians(a) for a in s.get("legs") or []]
    if not legs:
        return out
    diam, peds = [], []
    for a in legs:
        p = c + rho * np.array([math.cos(a), math.sin(a)])
        r_ = measure_round_member(F, p[0], p[1], zg + 1.6, min(zg + 7.0, zc - 2.0))
        if r_:
            diam.append(r_["diameter"])
        # Pedestal: upward faces around the leg foot, and the plinth's plan size below them.
        d = np.hypot(F[:, 0] - p[0], F[:, 1] - p[1])
        m = (d < 1.0) & (F[:, 2] > zg + 0.2) & (F[:, 2] < zg + 2.0) & (F[:, 5] > 0.8)
        if m.sum() < 5:
            continue
        top = float(np.median(F[m, 2]))
        m2 = (d < 1.2) & (F[:, 2] > zg + 0.1) & (F[:, 2] < top - 0.1) & (np.abs(F[:, 5]) < 0.3)
        if m2.sum() < 10:
            continue
        rel = F[m2, :2] - p
        rad = np.array([math.cos(a), math.sin(a)])
        tan = np.array([-rad[1], rad[0]])
        pr, pt = rel @ rad, rel @ tan
        peds.append((top, np.percentile(pr, 95) - np.percentile(pr, 5), np.percentile(pt, 95) - np.percentile(pt, 5)))
    if diam:
        out["leg_diameter"] = float(np.median(diam))
        out["leg_diameter_samples"] = len(diam)
    if len(peds) >= max(2, len(legs) // 2):
        P = np.array(peds)
        top = float(np.median(P[:, 0]))
        if 0.2 < top - zg < 2.0:
            out["pedestal"] = {"height": top - zg, "size_radial": float(np.median(P[:, 1])),
                               "size_tangential": float(np.median(P[:, 2])), "top": top, "count": len(peds)}
    # X bracing: best pair of heights (z_lo at one leg, z_hi at the next and vice versa).
    lo_lim = (out.get("pedestal", {}).get("top", zg)) + 0.2
    hi_lim = float(s.get("leg_contact_z", zc - 0.25 * tank.radius)) - 0.3
    zs = np.arange(lo_lim, hi_lim, 0.25)
    if len(zs) >= 4:
        score = np.zeros((len(zs), len(zs)))
        for i in range(len(legs)):
            a0, a1 = legs[i], legs[(i + 1) % len(legs)]
            p0 = c + rho * np.array([math.cos(a0), math.sin(a0)])
            p1 = c + rho * np.array([math.cos(a1), math.sin(a1)])
            L = float(np.linalg.norm(p1 - p0))
            uu = (p1 - p0) / L
            nn = np.array([-uu[1], uu[0]])
            rel = F[:, :2] - p0
            sx, off = rel @ uu, rel @ nn
            m = (sx > 0.6) & (sx < L - 0.6) & (np.abs(off) < 0.35) & (F[:, 2] > lo_lim - 0.3) & (F[:, 2] < hi_lim + 0.3)
            sx_, z_ = sx[m], F[m, 2]
            for ia, za in enumerate(zs):
                zl = za + (zs[None, :] - za) * (sx_[:, None] / L)
                score[ia] += np.sum(np.abs(z_[:, None] - zl) < 0.12, axis=0) / L
        S2 = score + score.T
        for ia in range(len(zs)):
            for ib in range(len(zs)):
                if abs(zs[ia] - zs[ib]) < 2.0:
                    S2[ia, ib] = 0
        ia, ib = np.unravel_index(int(np.argmax(np.triu(S2))), S2.shape)
        base = float(np.median(S2[S2 > 0])) if (S2 > 0).any() else 0.0
        if S2[ia, ib] > 1.5 * base:
            out["brace_z0"] = float(min(zs[ia], zs[ib]))
            out["brace_z1"] = float(max(zs[ia], zs[ib]))
            out["brace_score_ratio"] = float(S2[ia, ib] / max(base, 1e-6))
    return out


def roof_surface(F: np.ndarray, tank, bin_size: float = 0.5):
    """Measured roof profile z(rho): low percentile of faces per radial bin (ignores fittings)."""
    eave = tank.z_base + tank.wall_height
    d = np.hypot(F[:, 0] - tank.center_x, F[:, 1] - tank.center_y)
    m = (d < tank.radius) & (F[:, 2] > eave - 0.5)
    rho, z = d[m], F[m, 2]
    edges = np.arange(0, tank.radius + bin_size, bin_size)
    mids, vals = [], []
    for k in range(len(edges) - 1):
        sel = (rho >= edges[k]) & (rho < edges[k + 1])
        if sel.sum() > 20:
            mids.append(0.5 * (edges[k] + edges[k + 1]))
            vals.append(float(np.percentile(z[sel], 15)))
    if len(mids) < 3:
        return None
    mids, vals = np.array(mids), np.array(vals)
    return lambda r: np.interp(r, mids, vals)


def roof_platform(F: np.ndarray, tank, connectors_az=()) -> dict | None:
    """Central roof platform (flat deck + rail) and radial roof walkways with rails.

    Spheres: flat deck above the crown.  Cylinders: elevated faces near the centre form the
    platform; an azimuth band of elevated faces from the rim to the centre is a radial walkway.
    """
    cxy = np.array([tank.center_x, tank.center_y])
    d = np.hypot(F[:, 0] - cxy[0], F[:, 1] - cxy[1])
    az = np.degrees(np.arctan2(F[:, 1] - cxy[1], F[:, 0] - cxy[0])) % 360
    out = {"walkways": []}
    sph = getattr(tank, "sphere", None)
    R = tank.radius
    if sph:
        crown = float(sph["center_z"] + sph["radius"])
        m = (d < 0.45 * R) & (F[:, 2] > crown - 0.4) & (F[:, 2] < crown + 1.0) & (F[:, 5] > 0.85)
        if m.sum() < 30:
            return None
        hh, e = np.histogram(F[m, 2], bins=np.arange(crown - 0.4, crown + 1.0, 0.05), weights=F[m, 6])
        zp = float(e[int(np.argmax(hh))] + 0.025)
        on = m & (np.abs(F[:, 2] - zp) < 0.08)
        # radius: largest rho where the deck still covers most azimuths (ignores walkway arms)
        rp = 0.6
        for r_ in np.arange(0.6, 0.45 * R, 0.1):
            ring = on & (np.abs(d - r_) < 0.1)
            cov = len(np.unique((az[ring] // 15).astype(int))) / 24.0
            if cov >= 0.6:
                rp = float(r_)
        surf = None
    else:
        surf = roof_surface(F, tank)
        if surf is None:
            return None
        el = F[:, 2] - surf(d)
        lifted = (d < R - 0.4) & (el > 0.25) & (el < 1.8)
        core = lifted & (d < 2.5)
        if core.sum() < 30:
            rp = None
        else:
            rp = float(np.percentile(d[core], 90))
            rp = min(max(rp, 0.6), 2.5)
        zp = float(surf(0.0)) + 0.1
        # Radial walkways: azimuth bands of elevated faces covering most of the radius.
        band = lifted & (d > (rp or 1.0) + 0.3) & (d < R - 0.6)
        if band.sum() > 50:
            h, e = np.histogram(az[band], bins=72, range=(0, 360))
            h = h + np.roll(h, 1) + np.roll(h, -1)
            for i in _peaks(h, max(40, 0.3 * h.max())):
                a0 = (e[i] + 2.5)
                sel = band & (np.abs(((az - a0 + 180) % 360) - 180) < 12)
                uvec = np.array([math.cos(math.radians(a0)), math.sin(math.radians(a0))])
                nvec = np.array([-uvec[1], uvec[0]])
                rel = F[sel, :2] - cxy
                along, lat = rel @ uvec, rel @ nvec
                keep = (along > 0) & (np.abs(lat) < 1.2)
                if keep.sum() < 40:
                    continue
                st = np.unique(np.floor(along[keep] / 0.5))
                span = (R - 0.6) - ((rp or 1.0) + 0.3)
                if len(st) * 0.5 < 0.6 * span:
                    continue
                lat_k = lat[keep]
                c_lat = float(np.median(lat_k))
                l0, l1 = np.percentile(lat_k, [5, 95])
                width = float(np.clip(l1 - l0, 0.6, 1.6))
                rail = float(np.percentile(el[sel][keep], 95))
                # refine direction: fit lat = a + b*along
                b_, a_ = np.polyfit(along[keep], lat_k, 1)
                ang = math.degrees(math.atan2(uvec[1], uvec[0])) + math.degrees(math.atan(b_))
                off = float(a_)
                out["walkways"].append({"azimuth_deg": float(ang % 360), "offset": off, "width": width,
                                        "rail_height": float(np.clip(rail, 0.8, 1.3)),
                                        "r0": float((rp or 0.0)), "r1": float(R + 0.05)})
        # one walkway per direction
        ww = []
        for w in sorted(out["walkways"], key=lambda q: -q["width"]):
            if all(abs(((w["azimuth_deg"] - q["azimuth_deg"] + 180) % 360) - 180) > 20 for q in ww):
                ww.append(w)
        # radial walkways only make sense on large roofs (span from platform to rim >= 3 m)
        out["walkways"] = ww if (R - 0.6) - ((rp or 1.0) + 0.3) >= 3.0 else []
        if rp is not None and rp > 0.35 * R:
            rp = None  # small tank: the "platform" is just its roof rail / fittings
        if rp is None and not out["walkways"]:
            return None
    if rp is not None:
        if sph:
            rr = on & (np.abs(d - rp) < 0.5)
            above = (np.abs(d - rp) < 0.4) & (F[:, 2] > zp + 0.3) & (F[:, 2] < zp + 1.5)
        else:
            above = (np.abs(d - rp) < 0.4) & (F[:, 2] > zp + 0.3) & (F[:, 2] < zp + 1.5)
        rail = None
        if above.sum() > 10:
            cov = len(np.unique((az[above] // 15).astype(int))) / 24.0
            if cov >= 0.4:
                rail = float(np.percentile(F[above, 2] - zp, 95))
        out["platform"] = {"z": zp, "radius": rp, "rail_height": rail}
    out["open_azimuths"] = [float(a) for a in connectors_az] + [w["azimuth_deg"] for w in out["walkways"]]
    out["surface"] = None if surf is None else [[float(r), float(surf(r))] for r in np.linspace(0, R, 12)]
    return out


def platform_parts(prefix: str, tank, info: dict, segs: int = 24) -> list:
    parts = []
    cx, cy = tank.center_x, tank.center_y
    pf = info.get("platform")
    opens = info.get("open_azimuths", [])
    if pf:
        zp, rp = pf["z"], pf["radius"]
        poly = [(cx + rp * math.cos(2 * math.pi * k / segs), cy + rp * math.sin(2 * math.pi * k / segs)) for k in range(segs)]
        parts.append(prism(f"{prefix}_platform", poly, zp - 0.1, zp))
        rh = pf.get("rail_height") or 1.05
        for k in range(segs):
            a_mid = math.degrees(2 * math.pi * (k + 0.5) / segs) % 360
            gap = max(8.0, math.degrees(0.6 / max(rp, 0.3)))
            if any(abs(((a_mid - o + 180) % 360) - 180) < gap for o in opens):
                continue
            parts += railing(f"{prefix}_platform_rail", poly[k], poly[(k + 1) % segs], zp, rh, post_step=1.2)
    surf = info.get("surface")
    for j, w in enumerate(info.get("walkways", []), start=1):
        a = math.radians(w["azimuth_deg"])
        uvec = np.array([math.cos(a), math.sin(a)])
        nvec = np.array([-uvec[1], uvec[0]])
        rr = np.linspace(w["r0"], w["r1"], 8)
        if surf:
            S_ = np.array(surf)
            zz = np.interp(rr, S_[:, 0], S_[:, 1]) + 0.12
        else:
            zz = np.full_like(rr, info.get("platform", {}).get("z", tank.z_base + tank.total_height))
        c0 = np.array([cx, cy]) + nvec * w["offset"]
        for k in range(len(rr) - 1):
            p0 = c0 + uvec * rr[k]
            p1 = c0 + uvec * rr[k + 1]
            parts.append(beam(f"{prefix}_roof_walkway_{j:02d}", (*p0, zz[k]), (*p1, zz[k + 1]), w["width"], 0.08))
            for sgn, tag in ((1, "L"), (-1, "R")):
                e = nvec * sgn * (w["width"] / 2 - 0.03)
                parts += railing(f"{prefix}_roof_walkway_{j:02d}_rail_{tag}", p0 + e, p1 + e, zz[k], w["rail_height"],
                                 post_step=1.5, z_base_b=zz[k + 1])
    return parts


def wall_stair(F: np.ndarray, tank) -> dict | None:
    """Helical stair on a cylinder wall: treads (upward faces just outside the wall) lie on
    a line z = z0 + k * s in the unrolled wall (s = arc length).  Hough over the slope."""
    R = tank.radius
    eave = tank.z_base + tank.wall_height
    d = np.hypot(F[:, 0] - tank.center_x, F[:, 1] - tank.center_y)
    m = (d > R + 0.15) & (d < R + 1.8) & (F[:, 2] > tank.z_ground + 0.3) & (F[:, 2] < eave + 0.6) & (F[:, 5] > 0.6)
    if m.sum() < 60 or tank.wall_height < 3.0:
        return None
    az = np.arctan2(F[m, 1] - tank.center_y, F[m, 0] - tank.center_x) % (2 * math.pi)
    s_ = az * R
    z_ = F[m, 2]
    rho = d[m]
    circ = 2 * math.pi * R
    S2 = np.concatenate([s_, s_ + circ])
    Z2 = np.concatenate([z_, z_])
    RH = np.concatenate([rho, rho])
    best = None
    for sign in (1, -1):
        for k in np.arange(0.35, 1.25, 0.025):
            b = Z2 - sign * k * S2
            h, e = np.histogram(b, bins=np.arange(b.min(), b.max() + 0.2, 0.15))
            i = int(np.argmax(h))
            if best is None or h[i] > best[0]:
                best = (int(h[i]), sign, float(k), float(e[i] + 0.075))
    _cnt, sign, k, b0 = best
    inl = np.abs(Z2 - (b0 + sign * k * S2)) < 0.3
    ss = np.sort(np.unique(np.round(S2[inl] / 0.25) * 0.25))
    if len(ss) < 4:
        return None
    # longest run with gaps < 1.5 m
    runs, start = [], ss[0]
    for a_, b_ in zip(ss[:-1], ss[1:]):
        if b_ - a_ > 3.0:  # flights may be split by a landing / sparse mesh
            runs.append((start, a_))
            start = b_
    runs.append((start, ss[-1]))
    s0, s1 = max(runs, key=lambda r: r[1] - r[0])
    z0, z1 = b0 + sign * k * s0, b0 + sign * k * s1
    rise = abs(z1 - z0)
    if rise < max(3.0, 0.4 * tank.wall_height) or (s1 - s0) < 3.0:
        return None
    sel = inl & (S2 >= s0) & (S2 <= s1)
    r_in, r_out = np.percentile(RH[sel], [5, 95])
    lo_z, hi_z = (z0, z1) if z0 < z1 else (z1, z0)
    a_lo = ((s0 if z0 < z1 else s1) / R) % (2 * math.pi)
    a_hi = ((s1 if z0 < z1 else s0) / R) % (2 * math.pi)
    # Tank stairs run from the ground to the roof: extend a partly seen helix along its
    # own slope when one end is clearly cut short by a sparse mesh.
    sweep = (s1 - s0) / R
    extended = []
    eave_top = eave + 0.1
    turn = 1 if sign > 0 else -1  # +1: azimuth increases while climbing (ccw)
    if lo_z > tank.z_ground + 1.5 and hi_z > eave - 2.0:
        extra = (lo_z - tank.z_ground) / (k * R)
        a_lo = (a_lo - turn * extra) % (2 * math.pi)
        sweep += extra
        lo_z = tank.z_ground
        extended.append("bottom")
    if hi_z < eave - 1.5 and lo_z < tank.z_ground + 2.0:
        extra = (eave_top - hi_z) / (k * R)
        a_hi = (a_hi + turn * extra) % (2 * math.pi)
        sweep += extra
        hi_z = eave_top
        extended.append("top")
    s0, s1 = 0.0, sweep * R
    return {"az_bottom_deg": math.degrees(a_lo), "az_top_deg": math.degrees(a_hi), "extended": extended,
            "z_bottom": float(max(lo_z, tank.z_ground)), "z_top": float(hi_z),
            "inner_radius": float(max(r_in, R + 0.05)), "outer_radius": float(r_out),
            "width": float(r_out - max(r_in, R + 0.05)), "slope_deg": math.degrees(math.atan(k)),
            "direction": "ccw" if turn > 0 else "cw", "sweep_deg": math.degrees(sweep),
            "faces": int(sel.sum())}


def wall_stair_parts(prefix: str, tank, st: dict, rail: float = 1.0) -> list:
    parts = []
    cx, cy = tank.center_x, tank.center_y
    a0 = math.radians(st["az_bottom_deg"])
    sweep = math.radians(st["sweep_deg"]) * (1 if st["direction"] == "ccw" else -1)
    z0, z1 = st["z_bottom"], st["z_top"]
    ri, ro = st["inner_radius"], st["outer_radius"]
    n = max(4, int(round((z1 - z0) / 0.2)))
    rm = 0.5 * (ri + ro)
    for i in range(n):
        f = (i + 0.5) / n
        a = a0 + sweep * f
        z = z0 + (z1 - z0) * (i + 1) / n
        tread = abs(sweep) * rm / n + 0.02
        parts.append(box(f"{prefix}_stair_treads", (cx + rm * math.cos(a), cy + rm * math.sin(a), z - 0.025),
                         (tread, ro - ri, 0.05), math.degrees(a) + 90))
    m = max(6, int(abs(sweep) / math.radians(6)))
    for rad, tag in ((ri, "inner"), (ro, "outer")):
        pts = [(cx + rad * math.cos(a0 + sweep * j / m), cy + rad * math.sin(a0 + sweep * j / m), z0 + (z1 - z0) * j / m) for j in range(m + 1)]
        for j in range(m):
            # stringer: a 0.22 m deep beam hangs below its top line, so lift the line by the
            # depth to have its underside on the stair line and its foot on the ground
            q0, q1 = pts[j], pts[j + 1]
            parts.append(beam(f"{prefix}_stair_stringer_{tag}", (q0[0], q0[1], q0[2] + 0.22), (q1[0], q1[1], q1[2] + 0.22), 0.06, 0.22))
        if tag == "outer":
            up = [(x, y, z + rail) for x, y, z in pts]
            mid = [(x, y, z + rail / 2) for x, y, z in pts]
            parts += polyline_rods(f"{prefix}_stair_rail", up, 0.025)
            parts += polyline_rods(f"{prefix}_stair_rail", mid, 0.02)
            for j in range(0, m + 1, 2):
                parts.append(rod(f"{prefix}_stair_rail_posts", pts[j], up[j], 0.025))
    # top landing
    at = a0 + sweep
    parts.append(box(f"{prefix}_stair_landing", (cx + rm * math.cos(at), cy + rm * math.sin(at), z1 - 0.05),
                     (1.0, ro - ri, 0.1), math.degrees(at) + 90))
    return parts


def wall_pipes(F: np.ndarray, tank, exclude_az=(), max_standoff: float = 1.0) -> list[dict]:
    """Vertical pipes running down a tank wall (or past a sphere's legs).

    Unrolled wall: arc-length bins whose vertical faces cover most of the height are pipe
    columns; diameter from RANSAC circles on horizontal slices."""
    sph = getattr(tank, "sphere", None)
    cxy = np.array([tank.center_x, tank.center_y])
    d = np.hypot(F[:, 0] - cxy[0], F[:, 1] - cxy[1])
    if sph:
        r_lo, r_hi = float(sph.get("leg_rho", tank.radius)) + 0.5, tank.radius + 1.5
        z_lo, z_hi = tank.z_base + 1.2, float(sph.get("leg_contact_z", sph["center_z"] - 2)) - 0.3
        R = 0.5 * (r_lo + r_hi)
    else:
        R = tank.radius
        r_lo, r_hi = R + 0.05, R + max_standoff
        z_lo, z_hi = tank.z_ground + 0.6, tank.z_base + tank.wall_height - 0.4
    if z_hi - z_lo < 2.5:
        return []
    m = (d > r_lo) & (d < r_hi) & (F[:, 2] > z_lo) & (F[:, 2] < z_hi) & (np.abs(F[:, 5]) < 0.35)
    if m.sum() < 50:
        return []
    az = np.arctan2(F[m, 1] - cxy[1], F[m, 0] - cxy[0]) % (2 * math.pi)
    s_ = az * R
    z_ = F[m, 2]
    nb = int(2 * math.pi * R / 0.1)
    zb = np.arange(z_lo, z_hi + 1e-6, 0.25)
    occ = np.zeros((nb, len(zb)), bool)
    occ[np.clip((s_ / 0.1).astype(int), 0, nb - 1), np.clip(((z_ - z_lo) / 0.25).astype(int), 0, len(zb) - 1)] = True
    cover = occ.mean(axis=1)
    cover2 = np.maximum(cover, np.maximum(np.roll(cover, 1), np.roll(cover, -1)))
    good = cover2 >= 0.55
    out = []
    idx = np.flatnonzero(good)
    if len(idx) == 0:
        return out
    groups = [[idx[0]]]
    for i in idx[1:]:
        if i - groups[-1][-1] <= 2:
            groups[-1].append(i)
        else:
            groups.append([i])
    if len(groups) > 1 and groups[0][0] == 0 and groups[-1][-1] == nb - 1:
        groups[0] = groups[-1] + groups[0]
        groups.pop()
    for g in groups:
        if len(g) > 10:  # wider than 1 m: not a pipe (stair tower, ladder cage, ...)
            continue
        a_c = (np.mean(np.unwrap(np.array(g) * 0.1 / R)) + 0.05 / R) % (2 * math.pi)
        a_deg = math.degrees(a_c)
        if any(abs(((a_deg - e + 180) % 360) - 180) < ex for e, ex in exclude_az):
            continue
        sel = m.copy()
        sel[m] = np.abs(((az - a_c + math.pi) % (2 * math.pi)) - math.pi) * R < 0.5
        if sel.sum() < 30:
            continue
        rr = float(np.median(d[sel]))
        px, py = cxy + rr * np.array([math.cos(a_c), math.sin(a_c)])
        meas = measure_round_member(F, px, py, z_lo, z_hi, reach=0.45, rmax=0.35)
        dia = meas["diameter"] if meas else float(np.clip(len(g) * 0.1, 0.1, 0.5))
        if meas:
            px, py = px + meas["dx"], py + meas["dy"]
        col = occ[g].any(axis=0)
        zz = zb[col]
        out.append({"x": float(px), "y": float(py), "z0": float(zz.min()), "z1": float(zz.max() + 0.25),
                    "diameter": float(np.clip(dia, 0.06, 0.7)), "azimuth_deg": a_deg,
                    "standoff": float(rr - tank.radius), "diameter_source": "measured" if meas else "width"})
    return out


# ------------------------------------------------------------- whole site ---

def tank_boxes(tanks, margin: float = 14.0):
    return [(t.center_x - t.radius - margin, t.center_y - t.radius - margin,
             t.center_x + t.radius + margin, t.center_y + t.radius + margin) for t in tanks]


def _conn_az(tank, connectors) -> list[float]:
    out = []
    for c in connectors:
        for t, (x, y) in ((getattr(c, "tank_refs", (None, None))[0], (c.x0, c.y0)),
                          (getattr(c, "tank_refs", (None, None))[-1] if getattr(c, "tank_refs", ()) else None, (c.x1, c.y1))):
            if t is tank:
                out.append(math.degrees(math.atan2(y - tank.center_y, x - tank.center_x)) % 360)
    return out


def _deck_side(tower: dict, conn) -> tuple[int, dict | None]:
    """Ring side of a stair tower facing a walkway deck, and a bridge plate if there is a gap."""
    corners = rect_corners(tower["x"], tower["y"], tower["length"], tower["width"], tower["axis_deg"])
    p0, p1 = np.array([conn.x0, conn.y0]), np.array([conn.x1, conn.y1])
    L = float(np.linalg.norm(p1 - p0)) or 1.0
    u = (p1 - p0) / L
    n = np.array([-u[1], u[0]])
    best = None
    for k in range(4):
        a, b = corners[k], corners[(k + 1) % 4]
        mid = 0.5 * (a + b)
        dist = abs(float((mid - p0) @ n))
        if best is None or dist < best[0]:
            best = (dist, k, a, b)
    dist, k, a, b = best
    gap = dist - conn.width / 2
    bridge = None
    if gap > 0.05:
        mid = 0.5 * (a + b)
        sgn = -1.0 if float((mid - p0) @ n) > 0 else 1.0
        edge_a = a + n * sgn * gap
        edge_b = b + n * sgn * gap
        s_mid = float((mid - p0) @ u) / L
        zdeck = conn.z0 + (conn.z1 - conn.z0) * min(max(s_mid, 0), 1)
        bridge = {"polygon": [list(map(float, a)), list(map(float, b)), list(map(float, edge_b)), list(map(float, edge_a))],
                  "z": float(zdeck), "gap": float(gap)}
    return k, bridge


def detect_structures(tanks, connectors, F: np.ndarray, log=print, pipes: bool = False,
                      ground_rows: list | None = None) -> tuple[list[Structure], dict]:
    """All ancillary structures of a block.  Tank-attached parts go to ``tank.extra_parts`` and
    measurements to ``tank.accessories``; free-standing ones are returned as Structures."""
    stats = {"faces": int(len(F))}
    if len(F) == 0:
        return [], stats
    gfun = ground_grid(F)
    ground = gfun(F[:, 0], F[:, 1])
    axis = site_axis(F)
    stats["site_axis_deg"] = axis
    found: list[Structure] = []
    grounding = ground_rows is not None
    rows = ground_rows if grounding else []
    # 0) ground contact of the tanks (foundation top or ground), before anything that uses z_base
    if grounding:
        for tank in tanks:
            reach = tank.radius + 6.0
            sub = F[(np.abs(F[:, 0] - tank.center_x) < reach) & (np.abs(F[:, 1] - tank.center_y) < reach)]
            G.ground_tank(sub, tank, rows)
        # connector supports / stair-tower footprints stand on the ground round them
        for conn in connectors:
            for j, sp in enumerate(conn.supports, start=1):
                g, _n = G.rect_ground(F, sp["x"], sp["y"], sp.get("length", 1.0), sp.get("width", 1.0), sp.get("axis_deg", 0.0),
                                      sp["z_bottom"])
                before = float(sp["z_bottom"])
                sp["z_bottom_measured"] = before
                if g is not None:
                    sp["z_bottom"] = g
                    sp["ground_gap_before_m"] = before - g
                    sp["ground_gap_m"] = 0.0
                r = G.row(f"{conn.name}_support_{j:02d}", "support", conn.name, before, g, "ground", g, sp["z_bottom"])
                r["_support"] = sp
                rows.append(r)
    # 1) stair towers that carry walkways (support boxes of the connectors)
    towers = []
    for conn in connectors:
        for sp in conn.supports:
            if min(sp.get("length", 0), sp.get("width", 0)) < 1.5 or sp["z_top"] - sp["z_bottom"] < 4.0:
                continue
            deck_z = max(conn.z0, conn.z1)
            t = detect_stair_tower(F, sp["x"], sp["y"], sp["z_bottom"], deck_z + 0.2, sp["length"], sp["width"], sp["axis_deg"],
                                   rail_height=conn.handrail_height or 1.05)
            if not t:
                continue
            side, bridge = _deck_side(t, conn)
            t["open_sides"] = [side]
            t["bridges"] = [bridge] if bridge else []
            t["connector"] = conn.name
            towers.append((t, conn, sp))
    for k, (t, conn, sp) in enumerate(towers, start=1):
        name = f"Stair_{k:03d}"
        st = Structure(name=name, kind="stair_tower", parent=conn.name, anchor=(t["x"], t["y"], t["z_ground"]))
        st.dims = {k_: t[k_] for k_ in ("x", "y", "length", "width", "axis_deg", "z_ground", "z_top", "rise", "levels", "levels_measured")}
        st.dims["flights"] = len(t["levels"])
        st.dims["connector"] = conn.name
        st.dims["bridge_gap"] = t["bridges"][0]["gap"] if t["bridges"] else 0.0
        st.parts = stair_tower_parts(t, name)
        st.geometry = {"type": "Polygon", "coordinates": [[list(map(float, p)) for p in rect_corners(t["x"], t["y"], t["length"], t["width"], t["axis_deg"])]]}
        st.geometry["coordinates"][0].append(st.geometry["coordinates"][0][0])
        sp["_tower"] = st
        sp["stair_tower"] = name
        if "ground_gap_before_m" in sp:
            st.dims["ground_gap_before_m"] = sp["ground_gap_before_m"]
            st.dims["ground_gap_m"] = sp["ground_gap_m"]
            for r in rows:
                if r.get("_support") is sp:
                    r["kind"], r["_ref"] = "stair_tower", st
        found.append(st)
    stats["stair_towers"] = len(towers)
    # 2) tank accessories (each on the faces around its own tank)
    full = F
    for tank in tanks:
        reach = max(tank.radius, float((tank.sphere or {}).get("leg_rho", 0.0))) + 3.0
        F = full[(np.abs(full[:, 0] - tank.center_x) < reach) & (np.abs(full[:, 1] - tank.center_y) < reach)]
        acc = {}
        parts = []
        name = tank.name
        if tank.sphere:
            sup = sphere_supports(F, tank)
            if sup:
                acc["sphere_supports"] = sup
                s = tank.sphere
                s["leg_diameter_v5"] = s.get("leg_diameter")
                if "leg_diameter" in sup:
                    s["leg_diameter"] = sup["leg_diameter"]
                    s["leg_radius"] = sup["leg_diameter"] / 2
                ped = sup.get("pedestal")
                legs_ = s.get("legs") or []
                zb_before = float(tank.z_base)
                grounds = G.leg_grounds(F, tank) if grounding else [None] * len(legs_)
                grounds = [g if g is not None else zb_before for g in grounds]
                if grounding and legs_:
                    tank.z_base_measured = zb_before
                    tank.z_base = tank.z_ground = float(np.median(grounds))
                    tank.ground_gap, tank.ground_gap_before = 0.0, zb_before - tank.z_base
                if ped:
                    s["leg_bottom"] = ped["top"]
                    rho = float(s.get("leg_rho"))
                    gaps = []
                    for i, a in enumerate(legs_, start=1):
                        ar = math.radians(a)
                        px, py = tank.center_x + rho * math.cos(ar), tank.center_y + rho * math.sin(ar)
                        g_i = grounds[i - 1]
                        h_i = ped["top"] - g_i
                        parts.append(box(f"{name}_pedestal_{i:02d}", (px, py, g_i + h_i / 2),
                                         (ped["size_radial"], ped["size_tangential"], h_i), a))
                        gaps.append(zb_before - g_i)
                        if grounding:
                            rows.append(G.row(f"{name}_pedestal_{i:02d}", "sphere_pedestal", name, zb_before, g_i, "ground", g_i, g_i))
                    ped["grounds"] = grounds
                    ped["ground_gap_before_m"] = float(max(gaps, key=abs)) if gaps else 0.0
                    if grounding:
                        rows.append(G.row(f"{name}_legs", "sphere_legs", name, ped["top"], None, "pedestal top", ped["top"], ped["top"]))
                elif grounding:
                    s["leg_bottom"] = tank.z_base
                    rows.append(G.row(f"{name}_legs", "sphere_legs", name, zb_before, tank.z_base, "ground", tank.z_base, tank.z_base,
                                      "no pedestal seen"))
                if "brace_z0" in sup:
                    s["brace_z0"], s["brace_z1"] = sup["brace_z0"], sup["brace_z1"]
                    s["bracing"] = "X (measured)"
        else:
            st = wall_stair(F, tank)
            if st and grounding:
                # stair foot: ground round the first metre of the helix, outside the shell / foundation
                fd = getattr(tank, "foundation", None)
                r_out_t = tank.radius + (fd["protrusion"] if fd else 0.0) + 0.15
                af = math.radians(st["az_bottom_deg"])
                rm = 0.5 * (st["inner_radius"] + st["outer_radius"])
                fx, fy = tank.center_x + rm * math.cos(af), tank.center_y + rm * math.sin(af)
                dd = np.hypot(F[:, 0] - fx, F[:, 1] - fy)
                dt = np.hypot(F[:, 0] - tank.center_x, F[:, 1] - tank.center_y)
                mm = (dd < 1.8) & (dt > r_out_t) & (F[:, 5] > 0.85) & (F[:, 2] > st["z_bottom"] - 2.0) & (F[:, 2] < st["z_bottom"] + 1.2)
                g = G.floor_z(F[mm, 2], F[mm, 6])
                if g is None:
                    g = tank.z_ground
                before = st["z_bottom"]
                note = ""
                if -1.0 < before - g < 1.2:
                    st["z_bottom"] = g
                else:
                    note = f"stair foot {before - g:+.2f} m from the ground: left as measured (landing / platform?)"
                st["ground_z"] = g
                st["ground_gap_before_m"] = before - g
                st["ground_gap_m"] = st["z_bottom"] - g
                rows.append(G.row(f"{name}_stair", "wall_stair", name, before, g, "ground", g, st["z_bottom"], note))
            if st:
                acc["wall_stair"] = st
                parts += wall_stair_parts(name, tank, st, rail=tank.handrail_height or 1.0)
        plat = roof_platform(F, tank, _conn_az(tank, connectors))
        if plat:
            acc["roof"] = {k: v for k, v in plat.items() if k != "surface"}
            parts += platform_parts(name, tank, plat)
        ex = []
        if tank.sphere:
            ex = [(a, math.degrees((float(tank.sphere.get("leg_radius", 0.3)) + 0.25) / max(float(tank.sphere.get("leg_rho", 7)), 1))) for a in tank.sphere.get("legs") or []]
        st = acc.get("wall_stair")
        if st:
            a0, sw = st["az_bottom_deg"], st["sweep_deg"] * (1 if st["direction"] == "ccw" else -1)
            for f in np.linspace(0, 1, 12):
                ex.append(((a0 + sw * f) % 360, 4.0))
        wp = wall_pipes(F, tank, ex) if pipes else []
        if wp:
            acc["wall_pipes"] = wp
            for i, p in enumerate(wp, start=1):
                parts.append(rod(f"{name}_pipe_{i:02d}", (p["x"], p["y"], p["z0"]), (p["x"], p["y"], p["z1"]), p["diameter"] / 2))
        fd = getattr(tank, "foundation", None)
        if fd:
            parts.insert(0, cylinder(f"{name}_foundation", (tank.center_x, tank.center_y), fd["z0"], fd["z1"], fd["radius"]))
            acc["foundation"] = fd
        tank.accessories = acc
        tank.extra_parts = parts
    F = full
    if not pipes:
        # pipes and pipe racks are off by default (modelled separately, e.g. from laser scans)
        stats["pipe_racks"] = stats["pipe_runs"] = stats["wall_pipes"] = 0
        stats["wall_stairs"] = sum(1 for t in tanks if getattr(t, "accessories", {}).get("wall_stair"))
        stats["roof_platforms"] = sum(1 for t in tanks if getattr(t, "accessories", {}).get("roof", {}).get("platform"))
        stats["roof_walkways"] = sum(len(getattr(t, "accessories", {}).get("roof", {}).get("walkways", [])) for t in tanks)
        return found, stats
    # 3) pipe racks and pipe runs on sleepers / supports
    excl = [(t["x"], t["y"], t["length"] + 1.0, t["width"] + 1.0, t["axis_deg"]) for t, _c, _s in towers]
    for conn in connectors:
        p0, p1 = np.array([conn.x0, conn.y0]), np.array([conn.x1, conn.y1])
        mid = 0.5 * (p0 + p1)
        excl.append((float(mid[0]), float(mid[1]), conn.length + 1.0, conn.width + 1.5,
                     math.degrees(math.atan2(*(p1 - p0)[::-1])), min(conn.z0, conn.z1) - 1.5, 1e9))
        for sp in conn.supports:
            excl.append((sp["x"], sp["y"], sp.get("length", 1) + 1.0, sp.get("width", 1) + 1.0, sp.get("axis_deg", 0)))
    elev = elevated_mask(F, tanks, ground, exclude_boxes=excl)
    segs = rack_segments(F, elev, axis)
    racks, runs = [], []
    for seg in segs:
        m = measure_rack(F, ground, elev, seg)
        if m:
            # pipe racks are 3-10 m tall; anything higher is a tank fitting / tower
            m["tiers"] = [z for z in m["tiers"] if z - m["ground"] <= 10.0]
            m["pipes"] = [p for p in m["pipes"] if p["z"] - m["ground"] <= 10.5]
        if not m or not m["tiers"]:
            continue
        if m["wall_frac"] > 0.5 or (m["roof_frac"] > 0.85 and not m["pipes"]):
            continue  # building / shed
        if not m["pipes"]:
            continue  # no pipe seen: a frame / canopy / equipment, not a pipe rack
        if seg["width"] < 1.0:
            runs.append((seg, m))  # narrow: pipes on single posts, not a two-column rack
        else:
            racks.append((seg, m))
    for k, (seg, m) in enumerate(racks, start=1):
        name = f"PipeRack_{k:03d}"
        u, v = _rot(seg["axis_deg"])
        a = u * seg["u0"] + v * (seg["v0"] + seg["v1"]) / 2
        st = Structure(name=name, kind="pipe_rack", anchor=(float(a[0]), float(a[1]), m["ground"]))
        st.parts = rack_parts(name, seg, m)
        st.dims = {"axis_deg": seg["axis_deg"], "length": seg["length"], "width": seg["width"], "ground": m["ground"],
                   "tiers": m["tiers"], "bents": len(m["bents"]), "bent_spacing": float(np.median(np.diff(m["bents"]))) if len(m["bents"]) > 1 else None,
                   "bents_source": m.get("bents_source", "measured"),
                   "pipes": [{"diameter": 2 * p["r"], "z": p["z"], "offset": p["v"] - seg["v0"], "length": p["u1"] - p["u0"]} for p in m["pipes"]]}
        corners = [u * uu + v * vv for uu, vv in ((seg["u0"], seg["v0"]), (seg["u1"], seg["v0"]), (seg["u1"], seg["v1"]), (seg["u0"], seg["v1"]), (seg["u0"], seg["v0"]))]
        st.geometry = {"type": "Polygon", "coordinates": [[[float(c[0]), float(c[1])] for c in corners]]}
        found.append(st)
    for k, (seg, m) in enumerate(runs, start=1):
        name = f"Pipe_{k:03d}"
        u, v = _rot(seg["axis_deg"])
        vc = (seg["v0"] + seg["v1"]) / 2
        a, b = u * seg["u0"] + v * vc, u * seg["u1"] + v * vc
        st = Structure(name=name, kind="pipe_run", anchor=(float(a[0]), float(a[1]), m["ground"]))
        pipes = m["pipes"]
        parts = []
        for j, p in enumerate(pipes, start=1):
            pa = u * p["u0"] + v * p["v"]
            pb = u * p["u1"] + v * p["v"]
            parts.append(rod(f"{name}_pipe_{j:02d}", (pa[0], pa[1], p["z"]), (pb[0], pb[1], p["z"]), p["r"]))
        z_sup = min(p["z"] - p["r"] for p in pipes)
        for j, bu in enumerate(m["bents"], start=1):
            c = u * bu + v * vc
            parts.append(box(f"{name}_support_{j:02d}", (c[0], c[1], (m["ground"] + z_sup) / 2), (0.2, max(seg["width"], 0.4), z_sup - m["ground"]), seg["axis_deg"]))
        st.parts = parts
        st.dims = {"axis_deg": seg["axis_deg"], "length": seg["length"], "z": pipes[0]["z"],
                   "diameter": 2 * pipes[0]["r"], "diameter_source": "measured",
                   "supports": len(m["bents"]), "supports_source": m.get("bents_source", "measured"),
                   "pipes": [{"diameter": 2 * p["r"], "z": p["z"], "offset": p["v"] - seg["v0"], "length": p["u1"] - p["u0"]} for p in pipes]}
        st.geometry = {"type": "LineString", "coordinates": [[float(a[0]), float(a[1]), float(pipes[0]["z"])], [float(b[0]), float(b[1]), float(pipes[0]["z"])]]}
        found.append(st)
    stats["pipe_racks"] = len(racks)
    stats["pipe_runs"] = len(runs)
    stats["wall_pipes"] = sum(len(getattr(t, "accessories", {}).get("wall_pipes", [])) for t in tanks)
    stats["wall_stairs"] = sum(1 for t in tanks if getattr(t, "accessories", {}).get("wall_stair"))
    stats["roof_platforms"] = sum(1 for t in tanks if getattr(t, "accessories", {}).get("roof", {}).get("platform"))
    stats["roof_walkways"] = sum(len(getattr(t, "accessories", {}).get("roof", {}).get("walkways", [])) for t in tanks)
    return found, stats
