"""Structures between tanks: roof-to-roof walkways / pipes, and their supports.

Works on mesh faces (centroid, normal z, area) collected around tank pairs.
For every pair of nearby tanks the corridor between their walls is searched
for a continuous elevated deck at roof level:

* deck   - upward faces in the corridor, present along most of the gap;
           its height per station gives the elevation at both ends;
* width  - lateral spread of the deck faces;
* depth  - deck top to the downward faces just under it;
* railing- faces 0.3-1.6 m above the deck near both deck edges;
* supports - vertical faces under the deck that reach down to the ground.

A narrow deck without railing is reported as a pipe (diameter = width).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np


@dataclass
class Connector:
    name: str
    kind: str  # "walkway" or "pipe"
    tank_a: str
    tank_b: str
    x0: float
    y0: float
    z0: float  # deck top at end A (absolute)
    x1: float
    y1: float
    z1: float
    width: float
    depth: float
    handrail_height: float | None
    fill: float
    supports: list[dict] = field(default_factory=list)  # x, y, z_bottom, z_top, size
    block: str = ""
    srs: str = ""
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rail_sides: list = field(default_factory=list)  # sides where the rail was seen (L = left of A->B)
    rail_source: str = ""  # measured / assumed (site median, rail not visible in the mesh)
    tank_refs: tuple = field(default=(), repr=False, compare=False)

    @property
    def length(self) -> float:
        return float(np.hypot(self.x1 - self.x0, self.y1 - self.y0))

    def to_dict(self) -> dict:
        out = {k: v for k, v in asdict(self).items() if k != "tank_refs"}
        out["length"] = self.length
        return out

    def refresh_names(self) -> None:
        if self.tank_refs:
            self.tank_a, self.tank_b = (getattr(t, "name", "") for t in self.tank_refs)


def tank_levels(tank) -> tuple[float, float]:
    """(eave, crown) absolute heights of a tank's top."""
    sphere = getattr(tank, "sphere", None)
    if sphere:
        return float(sphere["center_z"]), float(sphere["center_z"] + sphere["radius"])
    eave = float(tank.z_base + tank.wall_height)
    return eave, float(tank.z_base + tank.total_height)


def candidate_pairs(tanks, max_gap: float = 30.0) -> list[tuple[int, int]]:
    """Pairs whose walls are closer than max_gap and with no tank in between."""
    pairs = []
    xy = np.array([[t.center_x, t.center_y] for t in tanks], dtype=float)
    rr = np.array([t.radius for t in tanks], dtype=float)
    for i in range(len(tanks)):
        for j in range(i + 1, len(tanks)):
            d = float(np.hypot(*(xy[j] - xy[i])))
            gap = d - rr[i] - rr[j]
            if gap < 0.3 or gap > max_gap:
                continue
            u = (xy[j] - xy[i]) / d
            blocked = False
            for k in range(len(tanks)):
                if k in (i, j):
                    continue
                rel = xy[k] - xy[i]
                s = float(rel @ u)
                t = abs(float(rel[0] * u[1] - rel[1] * u[0]))
                if rr[i] < s < d - rr[j] and t < rr[k] + 0.5:
                    blocked = True
                    break
            if not blocked:
                pairs.append((i, j))
    return pairs


def corridor_box(a, b, half_width: float = 3.0) -> tuple[float, float, float, float]:
    xs = [a.center_x, b.center_x]
    ys = [a.center_y, b.center_y]
    return min(xs) - half_width, min(ys) - half_width, max(xs) + half_width, max(ys) + half_width


def detect_connector(a, b, faces: np.ndarray, ground_at=None, half_width: float = 3.0, step: float = 0.5):
    """faces: (N, 5) centroid x, y, z, normal z, area (absolute coords)."""
    pa = np.array([a.center_x, a.center_y])
    pb = np.array([b.center_x, b.center_y])
    d = float(np.linalg.norm(pb - pa))
    u = (pb - pa) / d
    v = np.array([-u[1], u[0]])
    s0, s1 = a.radius + 0.3, d - b.radius - 0.3
    if s1 - s0 < 0.2:
        return None, {"reason": "touching"}
    rel = faces[:, :2] - pa
    s = rel @ u
    t = rel @ v
    z = faces[:, 2]
    nz = faces[:, 3]
    ea, ca = tank_levels(a)
    eb, cb = tank_levels(b)
    zlo, zhi = min(ea, eb) - 2.5, max(ca, cb) + 2.5
    gap = (s > s0) & (s < s1) & (np.abs(t) < half_width) & (z > zlo) & (z < zhi)
    up = gap & (nz > 0.7)
    info = {"gap": s1 - s0, "zlo": zlo, "zhi": zhi}
    if up.sum() < 10:
        info["reason"] = "no deck faces"
        return None, info
    # Deck candidate: densest lateral band of upward faces (walkways are 0.5-2 m wide).
    tb = np.round(t[up] / 0.25) * 0.25
    vals, counts = np.unique(tb, return_counts=True)
    t_mid = float(vals[np.argmax(counts)])
    near = up & (np.abs(t - t_mid) < 1.5)
    corridor_up = (np.abs(t) < half_width) & (z > zlo) & (z < zhi) & (nz > 0.7) & (np.abs(t - t_mid) < 1.5)
    edges = np.arange(s0, s1 + 1e-6, step)
    if len(edges) < 2:
        edges = np.array([s0, s1])
    area = faces[:, 4]
    stations = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = near & (s >= lo) & (s < hi)
        if sel.sum() >= 2:
            stations.append((0.5 * (lo + hi), z[sel]))
    nbins = len(edges) - 1
    fill = len(stations) / max(nbins, 1)
    info.update(fill=fill, t_mid=t_mid)
    if fill < 0.75 or len(stations) < 2:
        info["reason"] = "deck not continuous"
        return None, info
    # Deck line: the straight line (any slope up to ~30 deg) that the most
    # stations have upward faces on. A pipe, rail or stair landing covers
    # only part of the span; the walkway covers all of it.
    best = None
    s_ref = 0.5 * (s0 + s1)
    for slope in np.arange(-0.6, 0.601, 0.02):
        votes = {}
        for sc, zz in stations:
            keys = np.unique(np.round((zz - slope * (sc - s_ref)) / 0.1).astype(int))
            for key in np.unique(np.concatenate([keys - 1, keys, keys + 1])):
                votes[key] = votes.get(key, 0) + 1
        key, count = max(votes.items(), key=lambda kv: (kv[1], -abs(slope)))
        if best is None or count > best[0] or (count == best[0] and abs(slope) < abs(best[1])):
            best = (count, slope, key * 0.1)
    _count, slope, level = best
    st, zs = [], []
    for sc, zz in stations:
        line = level + slope * (sc - s_ref)
        hit = zz[np.abs(zz - line) < 0.15]
        if len(hit):
            st.append(sc)
            zs.append(float(np.median(hit)))
    st, zs = np.array(st), np.array(zs)
    coef = np.polyfit(st, zs, 1) if len(st) >= 3 else np.array([slope, level - slope * s_ref])
    if len(st) < 0.7 * len(stations):
        info["reason"] = "deck not level/straight"
        return None, info
    # Extend each end over the tank top while the deck stays above the tank
    # surface (a sphere's walkway runs on to the top platform).
    s_lo = _extend(s0, -step, 0.0, corridor_up, s, z, area, coef, a, pa, u, v, step)
    s_hi = _extend(s1, step, d, corridor_up, s, z, area, coef, b, pa, u, v, step)
    info.update(extend_a=s0 - s_lo, extend_b=s_hi - s1)
    # Roof-to-roof only: the deck must reach each roof at (or above) its eave.
    # Pipe racks and side platforms run between tanks lower down.
    z_a, z_b = float(np.polyval(coef, s_lo)), float(np.polyval(coef, s_hi))
    info.update(z_a=z_a, z_b=z_b, eave_a=ea, eave_b=eb)
    # 0.5 m tolerance: sloped walkways can land just under a roof rim (E 025-028 lands 0.45 m
    # below 028's eave); E pipe racks sit >= 0.6 m below the eaves.
    if z_a < ea - 0.5 or z_b < eb - 0.5:
        info["reason"] = "below roof level (pipe rack / side platform)"
        return None, info
    deck = np.polyval(coef, s)
    on_deck = near & (np.abs(z - deck) < 0.15)
    tt = t[on_deck]
    if len(tt) < 6:
        info["reason"] = "deck too sparse"
        return None, info
    lo_t, hi_t = np.percentile(tt, [3, 97])
    width = float(hi_t - lo_t)
    t_c = float(0.5 * (lo_t + hi_t))
    lat = np.abs(t - t_c)
    # Depth: downward faces just under the deck.
    down = gap & (nz < -0.7) & (lat < width / 2 + 0.1) & (z < deck - 0.03) & (z > deck - 1.5)
    depth = float(np.clip(np.median(deck[down] - z[down]), 0.08, 1.5)) if down.sum() >= 5 else 0.2
    # Railing: anything 0.3-1.6 m above the deck near either edge.  A continuous rail
    # covers most stations; on thin meshes only the posts survive, so a side whose
    # stations are hit at >= 25 % (posts every ~1-2 m) also counts.
    rail = None
    rail_sides = []
    side_t = t - t_c
    above = gap & (z > deck + 0.3) & (z < deck + 1.6) & (lat > width / 2 - 0.35) & (lat < width / 2 + 0.25)
    heights = []
    for name, sgn in (("L", 1.0), ("R", -1.0)):
        sel = above & (side_t * sgn > 0)
        if sel.sum() < 6:
            continue
        cover = len(np.unique(np.floor((s[sel] - s0) / step))) / max(nbins, 1)
        if cover >= 0.25:
            rail_sides.append(name)
            heights.append(z[sel] - deck[sel])
    if heights:
        rail = float(np.percentile(np.concatenate(heights), 95))
    info["rail_sides"] = rail_sides
    info.update(width=width, depth=depth, rail=rail)
    if width < 0.25:
        info["reason"] = "too narrow"
        return None, info
    kind = "walkway" if (rail is not None or width >= 0.6) else "pipe"
    if kind == "pipe":
        depth = width
    # Supports: vertical faces under the deck reaching towards the ground.
    supports = []
    vert_all = (s > s0) & (s < s1) & (np.abs(t - t_c) < width / 2 + 2.0) & (np.abs(nz) < 0.3)
    if vert_all.any():
        sv = s[vert_all]
        zv = z[vert_all]
        tv = t[vert_all]
        deck_v = np.polyval(coef, sv)
        ground = None
        mid = pa + u * 0.5 * (s0 + s1)
        if ground_at is not None:
            try:
                ground = float(ground_at(float(mid[0]), float(mid[1])))
            except Exception:  # noqa: BLE001
                ground = None
        if ground is None:
            ground = float(np.percentile(z, 1))
        span = deck_v - 1.0 - (ground + 0.5)
        good = (zv < deck_v - 0.5) & (zv > ground + 0.3)
        sb = np.floor((sv[good] - s0) / 0.4).astype(int)
        hits = []
        for k in np.unique(sb):
            sel = sb == k
            zz = zv[good][sel]
            if len(zz) < 15:
                continue
            hist, _ = np.histogram(zz, bins=np.linspace(ground + 0.3, float(np.max(deck_v)) - 0.5, 12))
            if (hist > 0).mean() >= 0.8:
                hits.append(k)
        merged = []
        for k in hits:
            if merged and k - merged[-1][1] <= 2:
                merged[-1][1] = k
            else:
                merged.append([k, k])
        for k0, k1 in merged:
            lo_s, hi_s = s0 + 0.4 * k0, s0 + 0.4 * (k1 + 1)
            sel = good & (sv >= lo_s) & (sv < hi_s)
            ss_, tt_ = sv[sel], tv[sel]
            s_a, s_b = np.percentile(ss_, [5, 95])
            t_a, t_b = np.percentile(tt_, [5, 95])
            sc, tc = 0.5 * (s_a + s_b), 0.5 * (t_a + t_b)
            p = pa + u * sc + v * tc
            supports.append({
                "x": float(p[0]), "y": float(p[1]), "z_bottom": float(ground),
                "z_top": float(np.polyval(coef, sc) - depth),
                "length": float(max(s_b - s_a, 0.2)), "width": float(max(t_b - t_a, 0.2)),
                "axis_deg": float(np.degrees(np.arctan2(u[1], u[0]))),
            })
        info["support_span"] = float(np.median(span))
    p0 = pa + u * s_lo + v * t_c
    p1 = pa + u * s_hi + v * t_c
    conn = Connector(
        name="", kind=kind, tank_a=getattr(a, "name", ""), tank_b=getattr(b, "name", ""),
        x0=float(p0[0]), y0=float(p0[1]), z0=float(np.polyval(coef, s_lo)),
        x1=float(p1[0]), y1=float(p1[1]), z1=float(np.polyval(coef, s_hi)),
        width=width, depth=depth, handrail_height=rail, fill=float(fill), supports=supports,
        rail_sides=rail_sides, rail_source="measured" if rail is not None else "",
    )
    info["reason"] = "ok"
    return conn, info


def _top_level(z: np.ndarray, area: np.ndarray, bin_size: float = 0.1) -> float:
    """Highest height level carrying a good share of the upward area.

    Deck plates carry far more area than the thin top rail of a handrail, and
    the walkway sits above lower stair landings in the same corridor.
    """
    lo = float(z.min())
    idx = np.floor((z - lo) / bin_size).astype(int)
    weights = np.bincount(idx, weights=area)
    if len(weights) > 2:
        weights = np.convolve(weights, [1, 1, 1], mode="same")
    good = np.where(weights >= 0.3 * weights.max())[0]
    top = good.max()
    sel = np.abs(idx - top) <= 1
    return float(np.median(z[sel]))


def _extend(start, step, limit, near, s, z, area, coef, tank, pa, u, v, bin_step) -> float:
    """Walk from a rim along the axis over the tank while deck faces continue."""
    eave, crown = tank_levels(tank)
    centre = np.array([tank.center_x, tank.center_y])
    pos = start
    while (pos + step - limit) * np.sign(step) <= 0 if step > 0 else pos + step >= limit:
        lo, hi = sorted((pos, pos + step))
        level = float(np.polyval(coef, 0.5 * (lo + hi)))
        point = pa + u * 0.5 * (lo + hi)
        rho = float(np.linalg.norm(point - centre))
        surface = _surface_z(tank, rho)
        if surface > level - 0.3:
            break
        sel = near & (s >= lo) & (s < hi) & (np.abs(z - level) < 0.2)
        if sel.sum() < 2:
            break
        pos += step
    return float(pos)


def _surface_z(tank, rho: float) -> float:
    sphere = getattr(tank, "sphere", None)
    if sphere:
        r = float(sphere["radius"])
        return float(sphere["center_z"] + np.sqrt(max(r * r - rho * rho, 0.0))) if rho < r else -1e9
    eave, crown = tank_levels(tank)
    if rho > tank.radius:
        return -1e9
    return float(eave + (crown - eave) * max(0.0, 1.0 - rho / tank.radius))


def collect_corridor_faces(tiles, boxes) -> np.ndarray:
    """Faces (centroid xyz, normal z, area) of absolute-coordinate tiles inside any box."""
    if not boxes:
        return np.zeros((0, 5))
    bx = np.asarray(boxes, dtype=float)
    lo = bx[:, :2].min(axis=0)
    hi = bx[:, 2:].max(axis=0)
    chunks = []
    for vertices, triangles in tiles:
        if len(vertices) == 0 or len(triangles) == 0:
            continue
        vlo, vhi = vertices[:, :2].min(axis=0), vertices[:, :2].max(axis=0)
        hit = (bx[:, 0] < vhi[0]) & (bx[:, 2] > vlo[0]) & (bx[:, 1] < vhi[1]) & (bx[:, 3] > vlo[1])
        if not hit.any():
            continue
        a = vertices[triangles[:, 0]]
        b = vertices[triangles[:, 1]]
        c = vertices[triangles[:, 2]]
        cen = (a + b + c) / 3.0
        sel = (cen[:, 0] > lo[0]) & (cen[:, 0] < hi[0]) & (cen[:, 1] > lo[1]) & (cen[:, 1] < hi[1])
        if not sel.any():
            continue
        inside = np.zeros(len(cen), bool)
        for x0, y0, x1, y1 in bx[hit]:
            inside |= sel & (cen[:, 0] > x0) & (cen[:, 0] < x1) & (cen[:, 1] > y0) & (cen[:, 1] < y1)
        if not inside.any():
            continue
        n = np.cross(b[inside] - a[inside], c[inside] - a[inside])
        length = np.linalg.norm(n, axis=1)
        nz = np.where(length > 1e-12, n[:, 2] / np.maximum(length, 1e-12), 0.0)
        chunks.append(np.column_stack([cen[inside], nz, length / 2.0]))
    return np.concatenate(chunks) if chunks else np.zeros((0, 5))


def find_connectors(tanks, tiles, ground_at=None, debug: list | None = None) -> list[Connector]:
    pairs = candidate_pairs(tanks)
    boxes = [corridor_box(tanks[i], tanks[j]) for i, j in pairs]
    faces = collect_corridor_faces(tiles, boxes)
    return detect_connectors(tanks, faces, ground_at, debug, pairs=pairs)


def detect_connectors(tanks, faces: np.ndarray, ground_at=None, debug: list | None = None, pairs=None) -> list[Connector]:
    found = []
    for i, j in (candidate_pairs(tanks) if pairs is None else pairs):
        a, b = tanks[i], tanks[j]
        x0, y0, x1, y1 = corridor_box(a, b)
        sel = (faces[:, 0] > x0) & (faces[:, 0] < x1) & (faces[:, 1] > y0) & (faces[:, 1] < y1)
        conn, info = detect_connector(a, b, faces[sel], ground_at)
        if debug is not None:
            debug.append({"tank_a": getattr(a, "name", i), "tank_b": getattr(b, "name", j), **info})
        if conn is not None:
            conn.block = getattr(a, "block", "")
            conn.srs = getattr(a, "srs", "")
            conn.origin = tuple(getattr(a, "origin", (0.0, 0.0, 0.0)))
            conn.tank_refs = (a, b)
            found.append(conn)
    for index, conn in enumerate(found, start=1):
        conn.name = f"Connector_{index:03d}"
    return found
