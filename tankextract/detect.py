"""Detect vertical cylindrical tanks from streamed triangle meshes.

Wall vertices are binned in the horizontal plane (absolute metres) so a tank
that crosses a tile edge lands in one cluster. A circle is fitted with weights
equal to each cell's vertical extent, which keeps spiral stairs and wind
girders from pulling the radius. A second pass over the overlapping tiles
measures height, roof shape and the optional rings / stair.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from tankextract.model import Tank


@dataclass
class DetectConfig:
    min_radius: float = 2.0
    max_radius: float = 80.0
    min_height: float = 2.0
    max_height: float = 80.0
    max_rms: float = 0.20
    min_coverage: float = 260.0
    cell_size: float = 0.25
    # Wall cells are grouped into connected components and each component is
    # fitted with one circle. In real photogrammetry the low, noisy vertical
    # faces of trees, fences, pipes and ground clutter link almost the whole
    # scene into one giant component, so tanks standing close to anything are
    # lost. Components are therefore also built from cells whose vertical
    # extent is at least each of these values (metres); 0 keeps every cell.
    wall_extent_levels: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 5.0, 8.0)
    # Reject cylinders whose base sits this far above the surrounding ground
    # (fan stacks and vessels on building roofs).
    max_base_above_ground: float = 3.0
    # Reject cylinders taller than this many diameters (chimneys, columns).
    max_aspect: float = 4.0
    # Search for several circles inside a wall component that does not fit
    # one circle (tanks linked by walkways or pipe racks).
    split_components: bool = True
    # A tank is a closed shell: almost no mesh inside it between floor and
    # roof. Circles traced through process-plant clutter fail this.
    max_interior_ratio: float = 0.2
    # Cone / dome roof rise limit, in radii (real roofs: about 0.1-0.6 R).
    max_roof_rise: float = 1.0
    # Mean fraction of the wall height present per 10-degree sector.
    min_wall_fill: float = 0.6
    split_max_cells: int = 80000


@dataclass
class _Candidate:
    cx: float
    cy: float
    radius: float
    z_min: float
    z_max: float
    rms: float
    inlier_ratio: float
    coverage: float
    samples: dict[str, np.ndarray] = field(default_factory=dict)
    level: float = 0.0


class SceneAccumulator:
    """Pass-1 state. Wall bins only; triangle meshes are discarded by the caller."""

    def __init__(self, config: DetectConfig):
        self.config = config
        # (ix, iy) -> [zmin, zmax, count, sumx, sumy]
        self.wall: dict[tuple[int, int], list[float]] = {}
        # (ix, iy) -> [zmin, count] for upward faces, 2 m cells
        self.ground: dict[tuple[int, int], list[float]] = {}
        self.tile_boxes: list[tuple[str, np.ndarray]] = []
        self._ground_arrays = None

    def add_mesh(
        self,
        name: str,
        vertices: np.ndarray,
        triangles: np.ndarray,
        origin: tuple[float, float, float],
    ) -> None:
        if len(vertices) == 0 or len(triangles) == 0:
            return
        absolute = np.array(vertices, dtype=np.float64, copy=True)
        absolute[:, 0] += origin[0]
        absolute[:, 1] += origin[1]
        absolute[:, 2] += origin[2]
        tri = triangles
        a = absolute[tri[:, 0]]
        b = absolute[tri[:, 1]]
        c = absolute[tri[:, 2]]
        normal = np.cross(b - a, c - a)
        length = np.linalg.norm(normal, axis=1)
        valid = length > 1e-8
        nz = np.zeros(len(normal))
        nz[valid] = normal[valid, 2] / length[valid]
        vertical = valid & (np.abs(nz) < 0.40)
        if np.any(vertical):
            pts = np.concatenate([a[vertical], b[vertical], c[vertical]], axis=0)
            _accumulate_wall(pts, self.wall, self.config.cell_size)
        # Near-horizontal faces of either winding: some exporters flip it.
        upward = valid & (np.abs(nz) > 0.72)
        if np.any(upward):
            centroid = (a[upward] + b[upward] + c[upward]) / 3.0
            _accumulate_ground(centroid, self.ground)
        box = np.vstack([absolute.min(axis=0), absolute.max(axis=0)])
        self.tile_boxes.append((name, box))

    def candidates(self) -> list[_Candidate]:
        if not self.wall:
            return []
        levels = sorted(set(float(v) for v in (self.config.wall_extent_levels or (0.0,))))
        found_all: list[_Candidate] = []
        for level in levels:
            if level <= 0:
                bins = self.wall
            else:
                bins = {key: rec for key, rec in self.wall.items() if rec[1] - rec[0] >= level}
            if not bins:
                continue
            found: list[_Candidate] = []
            for keys in _components(bins, gap=2):
                candidate = _fit_component(keys, bins, self.config)
                if candidate is not None:
                    found.append(candidate)
                elif level > 0 and self.config.split_components:
                    # Several tanks joined by walkways, pipes or a shared
                    # stair: pull the circles out one at a time.
                    found.extend(_split_component(keys, bins, self.config))
            for candidate in found:
                candidate.level = level
            found_all.extend(_merge_candidates(found, bins, self.config))
        return _drop_swallowed(_dedupe_candidates(found_all))

    def ground_near(self, x: float, y: float, radius: float = 0.0) -> float:
        """Local terrain height around a footprint.

        Uses the low end of the upward-face cells in an annulus outside the
        footprint, so the tank's own roof (or the roof of a building under a
        rooftop object) is not taken as ground.
        """
        if not self.ground:
            return 0.0
        if self._ground_arrays is None:
            keys = np.array(list(self.ground.keys()), dtype=np.float64)
            zs = np.array([rec[0] for rec in self.ground.values()], dtype=np.float64)
            self._ground_arrays = ((keys + 0.5) * 2.0, zs)
        centers, zs = self._ground_arrays
        dist = np.hypot(centers[:, 0] - x, centers[:, 1] - y)
        for outer in (25.0, 50.0, 100.0):
            ring = (dist > radius + 1.5) & (dist < radius + outer)
            if ring.sum() >= 20:
                return float(np.percentile(zs[ring], 10))
        return self.ground_z_at(x, y)

    def ground_z_at(self, x: float, y: float) -> float:
        if not self.ground:
            return 0.0
        ix = int(np.floor(x / 2.0))
        iy = int(np.floor(y / 2.0))
        best = None
        best_d = 1e9
        for (gx, gy), rec in self.ground.items():
            d = (gx - ix) ** 2 + (gy - iy) ** 2
            if d < best_d:
                best_d = d
                best = rec[0]
        if best is None:
            return float(np.median([rec[0] for rec in self.ground.values()]))
        return float(best)


def refine_candidates(
    candidates: list[_Candidate],
    tiles: list[tuple[np.ndarray, np.ndarray]],
    config: DetectConfig,
    ground_at,
    block: str,
    srs: str,
    origin: tuple[float, float, float],
    debug: list | None = None,
) -> list[Tank]:
    """Pass 2. ``tiles`` are absolute-coordinate meshes overlapping the candidates.

    ``ground_at(x, y, radius)`` returns the local terrain height. When ``debug``
    is a list, one record per candidate (kept or rejected, with the reason) is
    appended to it.
    """
    tanks: list[Tank] = []
    # xy bounds of every tile, once: each candidate then only scans the tiles it touches
    # (scanning every tile per candidate made big blocks quadratic: hours on A_OSGB_cip)
    bounds = _tile_bounds(tiles)
    for candidate in candidates:
        m = candidate.radius + 4.2
        near = np.flatnonzero((bounds[:, 0] < candidate.cx + m) & (bounds[:, 2] > candidate.cx - m)
                              & (bounds[:, 1] < candidate.cy + m) & (bounds[:, 3] > candidate.cy - m))
        samples = _collect_samples(candidate, [tiles[i] for i in near])
        info: dict = {}
        tank = _measure(candidate, samples, config, ground_at, block, srs, origin, info)
        if tank is not None:
            tanks.append(tank)
        if debug is not None:
            record = {
                "cx": candidate.cx,
                "cy": candidate.cy,
                "radius": candidate.radius,
                "rms": candidate.rms,
                "coverage": candidate.coverage,
                "z_min": candidate.z_min,
                "z_max": candidate.z_max,
                "level": candidate.level,
                "kept": tank is not None,
                "reason": "ok" if tank is not None else info.get("reason", "rejected"),
            }
            record.update({k: v for k, v in info.items() if k != "reason"})
            if tank is not None:
                record["tank"] = tank
            debug.append(record)
    tanks.sort(key=lambda item: (round(item.center_x, 2), round(item.center_y, 2)))
    for index, tank in enumerate(tanks, start=1):
        tank.name = f"Tank_{index:03d}"
    return tanks


def _accumulate_wall(points: np.ndarray, bins: dict, cell: float) -> None:
    ix = np.floor(points[:, 0] / cell).astype(np.int64)
    iy = np.floor(points[:, 1] / cell).astype(np.int64)
    order = np.lexsort((iy, ix))
    ix, iy = ix[order], iy[order]
    xyz = points[order]
    change = np.empty(len(ix), dtype=bool)
    change[0] = True
    if len(ix) > 1:
        change[1:] = (ix[1:] != ix[:-1]) | (iy[1:] != iy[:-1])
    starts = np.flatnonzero(change)
    ends = np.append(starts[1:], len(ix))
    for start, end in zip(starts, ends):
        key = (int(ix[start]), int(iy[start]))
        slab = xyz[start:end]
        zmin = float(slab[:, 2].min())
        zmax = float(slab[:, 2].max())
        count = end - start
        sx = float(slab[:, 0].sum())
        sy = float(slab[:, 1].sum())
        rec = bins.get(key)
        if rec is None:
            bins[key] = [zmin, zmax, count, sx, sy]
        else:
            rec[0] = min(rec[0], zmin)
            rec[1] = max(rec[1], zmax)
            rec[2] += count
            rec[3] += sx
            rec[4] += sy


def _accumulate_ground(centroids: np.ndarray, bins: dict) -> None:
    ix = np.floor(centroids[:, 0] / 2.0).astype(np.int64)
    iy = np.floor(centroids[:, 1] / 2.0).astype(np.int64)
    for x_i, y_i, z in zip(ix, iy, centroids[:, 2]):
        key = (int(x_i), int(y_i))
        rec = bins.get(key)
        if rec is None:
            bins[key] = [float(z), 1.0]
        else:
            # Keep a low estimate: running mean of the lower of current and new.
            rec[0] = min(rec[0], float(z))
            rec[1] += 1.0


def _components(bins: dict, gap: int) -> list[list[tuple[int, int]]]:
    parent: dict[tuple[int, int], tuple[int, int]] = {key: key for key in bins}

    def find(key: tuple[int, int]) -> tuple[int, int]:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a, b) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    keys = list(bins)
    keyset = set(keys)
    offsets = range(-gap, gap + 1)
    for key in keys:
        ix, iy = key
        for dx in offsets:
            for dy in offsets:
                if dx == 0 and dy == 0:
                    continue
                other = (ix + dx, iy + dy)
                if other in keyset:
                    union(key, other)
    groups: dict[tuple[int, int], list] = {}
    for key in keys:
        groups.setdefault(find(key), []).append(key)
    return list(groups.values())


def _fit_component(keys: list, bins: dict, config: DetectConfig) -> _Candidate | None:
    if len(keys) < 24:
        return None
    xy = np.empty((len(keys), 2), dtype=np.float64)
    weight = np.empty(len(keys), dtype=np.float64)
    zmin = np.empty(len(keys))
    zmax = np.empty(len(keys))
    cell = config.cell_size
    for i, key in enumerate(keys):
        rec = bins[key]
        count = max(rec[2], 1.0)
        xy[i, 0] = rec[3] / count
        xy[i, 1] = rec[4] / count
        zmin[i] = rec[0]
        zmax[i] = rec[1]
        weight[i] = max(rec[1] - rec[0], cell)
    height = float(zmax.max() - zmin.min())
    if height < config.min_height or height > config.max_height * 1.5:
        return None
    extents = zmax - zmin
    if np.percentile(extents, 80) < 0.30 * height:
        return None
    center, radius, rms, inliers = _robust_circle(xy, weight, config)
    if center is None:
        return None
    if not (config.min_radius <= radius <= config.max_radius):
        return None
    if rms > config.max_rms:
        return None
    coverage = _coverage(xy[inliers], center)
    if coverage < config.min_coverage * 0.75:
        # Arcs split by a tile gap are merged later; keep a generous preliminary cut.
        return None
    return _Candidate(
        cx=float(center[0]),
        cy=float(center[1]),
        radius=float(radius),
        z_min=float(zmin[inliers].min()) if np.any(inliers) else float(zmin.min()),
        z_max=float(zmax[inliers].max()) if np.any(inliers) else float(zmax.max()),
        rms=float(rms),
        inlier_ratio=float(np.average(inliers, weights=weight)) if weight.sum() else 0.0,
        coverage=float(coverage),
    )


def _circle_from_three(p1, p2, p3):
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2 = ax * ax + ay * ay
    b2 = bx * bx + by * by
    c2 = cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy, float(np.hypot(ax - ux, ay - uy))


def _split_component(keys: list, bins: dict, config: DetectConfig, max_circles: int = 40) -> list[_Candidate]:
    """RANSAC: extract several circles from one wall component.

    Coordinates are taken relative to the component centroid so the
    three-point circle stays well conditioned at EPSG magnitudes.
    """
    n = len(keys)
    if n < 60 or n > config.split_max_cells:
        return []
    from scipy.spatial import cKDTree

    xy = np.empty((n, 2), dtype=np.float64)
    ext = np.empty(n, dtype=np.float64)
    for i, key in enumerate(keys):
        rec = bins[key]
        count = max(rec[2], 1.0)
        xy[i, 0] = rec[3] / count
        xy[i, 1] = rec[4] / count
        ext[i] = rec[1] - rec[0]
    shift = xy.mean(axis=0)
    local = xy - shift
    cell = config.cell_size
    alive = np.ones(n, dtype=bool)
    rng = np.random.default_rng(12345)
    search = min(2.0 * config.max_radius, 45.0)
    found: list[_Candidate] = []
    misses = 0
    while len(found) < max_circles and alive.sum() >= 24 and misses < 3:
        idx = np.flatnonzero(alive)
        tree = cKDTree(local[idx])
        trials = int(min(4000, max(300, len(idx) // 4)))
        best = None
        best_score = 0.0
        for _ in range(trials):
            i = idx[rng.integers(len(idx))]
            near = tree.query_ball_point(local[i], search)
            if len(near) < 24:
                continue
            j, k = rng.choice(len(near), 2, replace=False)
            circle = _circle_from_three(local[i], local[idx[near[j]]], local[idx[near[k]]])
            if circle is None:
                continue
            ux, uy, r = circle
            if not (config.min_radius <= r <= config.max_radius):
                continue
            tol = max(0.15, 0.025 * r)
            expected = 2.0 * np.pi * r / cell
            # cheap pre-check: points in the ring = inside r+tol minus inside r-tol (counts only,
            # no index lists), so the many hopeless trials on big plant-wide components cost little
            n_out = tree.query_ball_point((ux, uy), r + tol, return_length=True)
            if n_out < 24:
                continue
            n_in = tree.query_ball_point((ux, uy), r - tol, return_length=True) if r > tol else 0
            if n_out - n_in < 0.35 * expected:
                continue
            cand = tree.query_ball_point((ux, uy), r + tol)
            cand = idx[np.asarray(cand)]
            dist = np.hypot(local[cand, 0] - ux, local[cand, 1] - uy)
            on = cand[np.abs(dist - r) <= tol]
            if len(on) < 0.35 * expected:
                continue
            ang = np.arctan2(local[on, 1] - uy, local[on, 0] - ux)
            hist, _ = np.histogram(ang, bins=36, range=(-np.pi, np.pi))
            filled = float((hist > 0).mean())
            if filled * 360.0 < config.min_coverage * 0.75:
                continue
            score = filled * min(1.0, len(on) / expected)
            if score > best_score:
                best_score = score
                best = (ux, uy, r)
        if best is None:
            break
        ux, uy, r = best
        dist = np.hypot(local[:, 0] - ux, local[:, 1] - uy)
        band = alive & (np.abs(dist - r) <= max(0.45, 0.05 * r))
        sub = [keys[i] for i in np.flatnonzero(band)]
        candidate = _fit_component(sub, bins, config) if len(sub) >= 24 else None
        # Remove the ring (and whatever is inside it) either way, so the
        # next round looks for a different circle.
        alive &= ~(dist <= r + max(0.6, 0.06 * r))
        if candidate is None:
            misses += 1
            continue
        found.append(candidate)
    return found


def _robust_circle(xy: np.ndarray, weight: np.ndarray, config: DetectConfig):
    keep = np.ones(len(xy), dtype=bool)
    center = None
    radius = None
    rms = 1e9
    for _ in range(4):
        if keep.sum() < 12:
            return None, None, None, keep
        center, radius = _fit_circle(xy[keep], weight[keep])
        if center is None or not np.isfinite(radius) or radius <= 0:
            return None, None, None, keep
        dist = np.hypot(xy[:, 0] - center[0], xy[:, 1] - center[1])
        error = np.abs(dist - radius)
        rms = float(np.sqrt(np.average(error[keep] ** 2, weights=weight[keep])))
        limit = max(0.12, 0.03 * radius)
        new_keep = error <= limit
        if new_keep.sum() < 12 or np.array_equal(new_keep, keep):
            keep = new_keep if new_keep.sum() >= 12 else keep
            break
        keep = new_keep
    if center is None:
        return None, None, None, keep
    dist = np.hypot(xy[:, 0] - center[0], xy[:, 1] - center[1])
    error = np.abs(dist - radius)
    rms = float(np.sqrt(np.average((error[keep]) ** 2, weights=weight[keep])))
    return center, radius, rms, keep


def _fit_circle(xy: np.ndarray, weight: np.ndarray):
    """Algebraic circle fit followed by a few geometric Gauss-Newton steps.

    The fit is done after subtracting the weighted centroid. Absolute EPSG
    coordinates (millions of metres) otherwise swamp the metre-scale radius.
    """
    w = np.clip(weight, 1e-6, None)
    origin = np.average(xy, axis=0, weights=w)
    local = xy - origin
    x = local[:, 0]
    y = local[:, 1]
    # x^2 + y^2 = 2 a x + 2 b y + c
    design = np.column_stack([2 * x, 2 * y, np.ones(len(x))])
    target = x * x + y * y
    weighted = design * np.sqrt(w)[:, None]
    try:
        sol, *_ = np.linalg.lstsq(weighted, target * np.sqrt(w), rcond=None)
    except np.linalg.LinAlgError:
        return None, None
    a, b, c = sol
    r2 = a * a + b * b + c
    if r2 <= 0:
        return None, None
    radius = float(np.sqrt(r2))
    center = np.array([a, b], dtype=np.float64)
    for _ in range(6):
        delta = local - center
        dist = np.hypot(delta[:, 0], delta[:, 1])
        dist = np.maximum(dist, 1e-8)
        residual = dist - radius
        j_a = (center[0] - x) / dist
        j_b = (center[1] - y) / dist
        jacobian = np.column_stack([j_a, j_b, -np.ones(len(x))])
        weighted_j = jacobian * np.sqrt(w)[:, None]
        try:
            step, *_ = np.linalg.lstsq(weighted_j, residual * np.sqrt(w), rcond=None)
        except np.linalg.LinAlgError:
            break
        center = center - step[:2]
        radius = float(radius - step[2])
        if radius <= 0 or abs(step).max() < 1e-6:
            break
    if radius <= 0 or not np.isfinite(center).all():
        return None, None
    return center + origin, radius


def _coverage(xy: np.ndarray, center: np.ndarray, bins: int = 36) -> float:
    if len(xy) == 0:
        return 0.0
    ang = np.arctan2(xy[:, 1] - center[1], xy[:, 0] - center[0])
    hist, _ = np.histogram(ang, bins=bins, range=(-np.pi, np.pi))
    return float((hist > 0).mean() * 360.0)


def _merge_candidates(found: list[_Candidate], bins: dict, config: DetectConfig) -> list[_Candidate]:
    if not found:
        return []
    used = [False] * len(found)
    merged: list[_Candidate] = []
    order = sorted(range(len(found)), key=lambda i: -found[i].coverage)
    for i in order:
        if used[i]:
            continue
        group = [found[i]]
        used[i] = True
        for j in order:
            if used[j]:
                continue
            if _same_tank(found[i], found[j]):
                group.append(found[j])
                used[j] = True
        if len(group) == 1:
            item = group[0]
            if item.coverage >= config.min_coverage and item.rms <= config.max_rms:
                merged.append(item)
            continue
        # Refit is unnecessary for the centre (they already agree); average with coverage weights.
        weight = np.array([max(item.coverage, 1.0) for item in group])
        cx = float(np.average([item.cx for item in group], weights=weight))
        cy = float(np.average([item.cy for item in group], weights=weight))
        radius = float(np.average([item.radius for item in group], weights=weight))
        rms = float(min(item.rms for item in group))
        coverage = float(min(360.0, sum(item.coverage for item in group)))
        if coverage < config.min_coverage or rms > config.max_rms:
            continue
        merged.append(
            _Candidate(
                cx=cx,
                cy=cy,
                radius=radius,
                z_min=min(item.z_min for item in group),
                z_max=max(item.z_max for item in group),
                rms=rms,
                inlier_ratio=float(np.mean([item.inlier_ratio for item in group])),
                coverage=coverage,
            )
        )
    return _drop_swallowed(merged)


def _dedupe_candidates(found: list[_Candidate]) -> list[_Candidate]:
    """The same tank is usually found at several wall-extent levels; keep the best fit."""
    kept: list[_Candidate] = []
    for item in sorted(found, key=lambda c: (-round(c.coverage, -1), c.rms)):
        if not any(_same_tank(item, other) for other in kept):
            kept.append(item)
    return kept


def _drop_swallowed(found: list[_Candidate]) -> list[_Candidate]:
    """Drop a candidate that sits inside a larger one (stair-only or roof-fitting cluster)."""
    kept = []
    for item in found:
        swallowed = False
        for other in found:
            if other is item:
                continue
            dist = np.hypot(item.cx - other.cx, item.cy - other.cy)
            if dist + item.radius < other.radius * 1.15 and item.radius < other.radius * 0.85:
                swallowed = True
                break
        if not swallowed:
            kept.append(item)
    return kept


def _same_tank(a: _Candidate, b: _Candidate) -> bool:
    dist = np.hypot(a.cx - b.cx, a.cy - b.cy)
    return dist < 0.35 * max(a.radius, b.radius) and abs(a.radius - b.radius) < 0.12 * max(a.radius, b.radius)


def _tile_bounds(tiles) -> np.ndarray:
    """(N, 4) x0, y0, x1, y1 of each tile's vertices (empty tiles get an empty box)."""
    out = np.full((len(tiles), 4), [np.inf, np.inf, -np.inf, -np.inf])
    for i, (vertices, triangles) in enumerate(tiles):
        if len(vertices) and len(triangles):
            out[i, :2] = vertices[:, :2].min(axis=0)
            out[i, 2:] = vertices[:, :2].max(axis=0)
    return out


def _collect_samples(candidate: _Candidate, tiles: list[tuple[np.ndarray, np.ndarray]]) -> dict[str, np.ndarray]:
    margin = candidate.radius + 3.2
    z0 = candidate.z_min - 1.0
    z1 = candidate.z_max + candidate.radius + 2.0
    wall_pts = []
    roof_pts = []
    all_pts = []
    outer_pts = []
    cap = 20000
    for vertices, triangles in tiles:
        if len(vertices) == 0 or len(triangles) == 0:
            continue
        a = vertices[triangles[:, 0]]
        b = vertices[triangles[:, 1]]
        c = vertices[triangles[:, 2]]
        centroid = (a + b + c) / 3.0
        dist = np.hypot(centroid[:, 0] - candidate.cx, centroid[:, 1] - candidate.cy)
        near = (dist < margin + 1.0) & (centroid[:, 2] > z0) & (centroid[:, 2] < z1)
        if not np.any(near):
            continue
        normal = np.cross(b - a, c - a)
        length = np.linalg.norm(normal, axis=1)
        valid = length > 1e-8
        nz = np.zeros(len(normal))
        nz[valid] = normal[valid, 2] / length[valid]
        take = near & valid
        if not np.any(take):
            continue
        pts = np.concatenate([a[take], b[take], c[take]], axis=0)
        # Repeat the normal of each triangle for its three vertices.
        nz_v = np.concatenate([nz[take], nz[take], nz[take]])
        rho = np.hypot(pts[:, 0] - candidate.cx, pts[:, 1] - candidate.cy)
        vertical = np.abs(nz_v) < 0.40
        horizontal = np.abs(nz_v) > 0.55
        wall = vertical & (np.abs(rho - candidate.radius) < max(0.8, 0.15 * candidate.radius))
        if np.any(wall):
            wall_pts.append(pts[wall])
        inside = rho < candidate.radius * 1.05
        if np.any(inside):
            all_pts.append(pts[inside])
        roof = horizontal & (rho < candidate.radius * 0.98)
        if np.any(roof):
            roof_pts.append(pts[roof])
        outer = (rho > candidate.radius + 0.15) & (rho < candidate.radius + 3.2)
        if np.any(outer):
            outer_pts.append(np.column_stack([pts[outer], nz_v[outer]]))
    return {
        "wall": _cap(wall_pts, cap),
        "all": _cap(all_pts, cap),
        "roof": _cap(roof_pts, cap),
        "outer": _cap(outer_pts, cap),
    }


def _cap(chunks: list[np.ndarray], limit: int) -> np.ndarray:
    if not chunks:
        return np.zeros((0, 3))
    pts = np.concatenate(chunks, axis=0)
    if len(pts) > limit:
        rng = np.random.default_rng(0)
        pts = pts[rng.choice(len(pts), limit, replace=False)]
    return pts


def _shell_checks(samples, shell_pts, center, radius, z_base, z_top) -> tuple[float, float]:
    inside = samples["all"]
    interior = 0.0
    if len(inside):
        rho = np.hypot(inside[:, 0] - center[0], inside[:, 1] - center[1])
        within = rho < radius * 1.05
        core = within & (rho < radius * 0.85) & (inside[:, 2] > z_base + 1.0) & (inside[:, 2] < z_top - 1.0)
        interior = float(core.sum()) / max(1.0, float(within.sum()))
    fill = 0.0
    wall_h = max(z_top - z_base, 1e-6)
    if len(shell_pts):
        ang = np.arctan2(shell_pts[:, 1] - center[1], shell_pts[:, 0] - center[0])
        sector = np.clip(((ang + np.pi) / (2 * np.pi) * 36).astype(int), 0, 35)
        spans = np.zeros(36)
        for k in range(36):
            zs = shell_pts[sector == k, 2]
            if len(zs):
                spans[k] = min(1.0, (zs.max() - zs.min()) / wall_h)
        fill = float(spans.mean())
    return interior, fill


def _call_ground(ground_at, x: float, y: float, radius: float) -> float:
    try:
        return float(ground_at(x, y, radius))
    except TypeError:
        return float(ground_at(x, y))


def _measure(candidate, samples, config: DetectConfig, ground_at, block, srs, origin, info=None) -> Tank | None:
    if info is None:
        info = {}
    info["reason"] = "too few wall points"
    wall = samples["wall"]
    if len(wall) < 30:
        return None
    # Keep the pass-1 circle as the seed so stairs and girders outside the
    # wall are not part of the refined fit.
    seed_rho = np.hypot(wall[:, 0] - candidate.cx, wall[:, 1] - candidate.cy)
    wall = wall[np.abs(seed_rho - candidate.radius) <= max(0.18, 0.04 * candidate.radius)]
    if len(wall) < 30:
        return None
    center, radius = _fit_circle(wall[:, :2], np.ones(len(wall)))
    if center is None:
        return None
    dist = np.hypot(wall[:, 0] - center[0], wall[:, 1] - center[1])
    error = np.abs(dist - radius)
    limit = max(0.10, 0.025 * radius)
    keep = error <= limit
    if keep.sum() >= 30:
        center, radius = _fit_circle(wall[keep, :2], np.ones(int(keep.sum())))
        dist = np.hypot(wall[:, 0] - center[0], wall[:, 1] - center[1])
        error = np.abs(dist - radius)
        keep = error <= limit
    if center is None or keep.sum() < 30:
        return None
    rms = float(np.sqrt(np.mean(error[keep] ** 2)))
    info.update(fit_radius=float(radius), fit_rms=rms)
    if rms > config.max_rms:
        info["reason"] = "rms"
        return None
    if not (config.min_radius <= radius <= config.max_radius):
        info["reason"] = "radius"
        return None
    coverage = _coverage(wall[keep, :2], center)
    info["fit_coverage"] = coverage
    if coverage < config.min_coverage:
        info["reason"] = "coverage"
        return None
    # Wall height is the vertical run of the cylinder itself. A dome or cone
    # that leaves the radius is not part of the wall, so use a tight radial band.
    shell = np.abs(dist - radius) <= max(0.03, 3.0 * rms)
    if shell.sum() < 20:
        shell = keep
    z_base = float(np.percentile(wall[shell, 2], 1))
    z_top = float(np.percentile(wall[shell, 2], 99))
    wall_h = z_top - z_base
    ground = _call_ground(ground_at, float(center[0]), float(center[1]), float(radius))
    info.update(z_base=z_base, wall_height=wall_h, ground=ground)
    if wall_h < config.min_height or wall_h > config.max_height:
        info["reason"] = "height"
        return None
    if config.max_base_above_ground is not None and z_base - ground > config.max_base_above_ground:
        info["reason"] = "elevated base (on a roof or structure)"
        return None
    if config.max_aspect and wall_h > config.max_aspect * 2.0 * radius:
        info["reason"] = "slender (chimney or column)"
        return None
    sphere_fit = _sphere_check(samples["all"], center, radius, z_base, z_top)
    info["sphere_check"] = sphere_fit
    interior, fill = _shell_checks(samples, wall[shell], center, radius, z_base, z_top)
    info.update(interior_ratio=interior, wall_fill=fill)
    if sphere_fit.get("sphere"):
        pass  # a sphere's own lower hemisphere lies inside the cylinder test volume
    elif interior > config.max_interior_ratio:
        info["reason"] = "geometry inside the cylinder (not a closed tank)"
        return None
    if fill < config.min_wall_fill:
        info["reason"] = "wall not continuous over its height"
        return None
    roof_cloud = _roof_cloud(samples, center, radius, z_top)
    info["roof_profile"] = _roof_profile(roof_cloud, center, radius, z_top)
    fit = _roof_analysis(samples["roof"], center, radius, z_top)
    info["roof_fit"] = {k: v for k, v in fit.items() if k != "profile"}
    handrail = None
    sphere = None
    if sphere_fit.get("sphere"):
        # The surface keeps curving on a sphere of the wall radius for most
        # of a radius above the vertical stretch: a spherical tank on legs.
        center_z = sphere_fit["center_z"]
        radius = sphere_fit["radius"]
        legs = _sphere_legs(samples["wall"], center, radius, ground, center_z)
        sphere = {"center_z": float(center_z), "radius": float(radius), **legs}
        roof = "sphere"
        rise = 0.0
        z_base = ground
        wall_h = 0.0
        total = center_z + radius - ground
        rim = None
        girders = []
        base_ring = None
        stair = None
    else:
        if fit["ok"]:
            roof = fit["roof"]
            wall_top = fit["wall_top"]
            if z_top - wall_top > 0.3:
                handrail = float(z_top - wall_top)
                z_top = wall_top
                wall_h = z_top - z_base
            rise = 0.0 if roof == "flat" else max(0.0, fit["crown"] - z_top)
        else:
            z_max = float(np.percentile(roof_cloud[:, 2], 99.5)) if len(roof_cloud) else z_top
            rise = max(0.0, max(wall_h, z_max - z_base) - wall_h)
            roof = _classify_roof(roof_cloud, center, radius, z_top, rise)
            roof = _cone_or_dome(roof, roof_cloud, center, radius, z_top)
            if roof == "flat":
                rise = 0.0
            else:
                rise, _total = _roof_height(roof_cloud, center, radius, z_base, z_top, roof)
        total = wall_h + rise
        # After the handrail / eave split the shell must still be a wall, and a storage-tank
        # roof never rises more than about one radius: anything taller sitting on the circle
        # is other equipment stacked on a short drum or a plinth (e.g. a small tank on a pump
        # house), not a roof.
        if wall_h < config.min_height:
            info["reason"] = "height (wall below the roof too short)"
            return None
        if roof != "flat" and rise > config.max_roof_rise * radius:
            info["reason"] = "roof too tall for a tank (stacked equipment)"
            return None
        rim = _top_rim(samples["roof"], samples["outer"], samples["all"], center, radius, z_top)
        girders = _wind_girders(samples["outer"], center, radius, z_base, z_top)
        base_ring = _base_ring(samples["outer"], center, radius, z_base)
        stair = _spiral_stair(samples["outer"], center, radius, z_base, wall_h)
    return Tank(
        block=block,
        srs=srs,
        origin=origin,
        center_x=float(center[0]),
        center_y=float(center[1]),
        z_base=z_base,
        z_ground=ground,
        radius=float(radius),
        wall_height=float(wall_h),
        total_height=float(total),
        roof_type=roof,
        roof_rise=float(rise),
        rms=rms,
        inlier_ratio=float(keep.mean()),
        coverage_deg=coverage,
        top_rim_width=rim,
        wind_girders=girders,
        base_ring=base_ring,
        spiral_stair=stair,
        handrail_height=handrail,
        sphere=sphere,
    )


def _roof_cloud(samples, center, radius: float, z_top: float) -> np.ndarray:
    """Points on the lid: pre-filtered horizontal faces plus anything above the wall."""
    chunks = []
    if len(samples["roof"]):
        chunks.append(samples["roof"][:, :3])
    inside = samples["all"]
    if len(inside):
        rho = np.hypot(inside[:, 0] - center[0], inside[:, 1] - center[1])
        upper = inside[(inside[:, 2] >= z_top - 0.05) & (rho <= radius * 1.02)]
        if len(upper):
            chunks.append(upper[:, :3])
    if not chunks:
        return np.zeros((0, 3))
    return np.concatenate(chunks, axis=0)


def _classify_roof(roof_pts: np.ndarray, center, radius: float, z_top: float, rise: float) -> str:
    if rise < max(0.12, 0.02 * radius) or len(roof_pts) < 20:
        return "flat"
    rho = np.hypot(roof_pts[:, 0] - center[0], roof_pts[:, 1] - center[1])
    upper = (roof_pts[:, 2] > z_top - 0.20) & (rho < radius * 0.96) & (rho > 0.04 * radius)
    if upper.sum() < 15:
        return "flat" if rise < 0.25 else "cone"
    rho_u = rho[upper]
    z_u = roof_pts[upper, 2]
    lin = np.polyfit(rho_u, z_u, 1)
    pred_l = lin[0] * rho_u + lin[1]
    rms_l = float(np.sqrt(np.mean((z_u - pred_l) ** 2)))
    quad = np.polyfit(rho_u ** 2, z_u, 1)
    pred_q = quad[0] * rho_u ** 2 + quad[1]
    rms_q = float(np.sqrt(np.mean((z_u - pred_q) ** 2)))
    if rms_q + 0.02 < rms_l and quad[0] < 0:
        return "dome"
    if lin[0] < -0.08:
        return "cone"
    if quad[0] < 0 and rms_q <= rms_l:
        return "dome"
    return "flat"


def _roof_analysis(roof_pts, center, radius: float, z_top: float, small_tank_radius: float = 4.0) -> dict:
    """Roof shape from the lower envelope of the upward-facing roof surface.

    Handrails, walkways, vents and the central platform sit *on* the roof and
    cover only part of each ring, so a low percentile per radial ring follows
    the roof plate. The wall-shell top usually includes the handrail around
    the eave; the roof plate near the rim gives the true wall top.

    Three models are fitted to the ring heights for u = r/R in [0.1, 0.95],
    each with a few rounds of outlier trimming: flat, cone (linear in u) and
    spherical cap (dome, exact cap with fitted rise).
    """
    out = {"ok": False}
    if len(roof_pts) < 60:
        return out
    rho = np.hypot(roof_pts[:, 0] - center[0], roof_pts[:, 1] - center[1]) / radius
    z = roof_pts[:, 2]
    up = (rho < 0.97) & (z > z_top - 3.0)
    rim = up & (rho > 0.85)
    if rim.sum() < 20:
        return out
    # Roof edge: lowest ring of the roof plate next to the wall. Large tanks
    # have a level walkway ring inside the handrail; the plate meets the wall
    # there. A single wide band would be pulled up by the dome curvature.
    ring_edges = np.arange(0.85, 0.981, 0.03)
    rings = []
    for lo, hi in zip(ring_edges[:-1], ring_edges[1:]):
        sel = up & (rho >= lo) & (rho < hi)
        rings.append((float(np.percentile(z[sel], 30)), int(sel.sum())) if sel.sum() >= 8 else (None, int(sel.sum())))
    eave = None
    most = max(c for _z, c in rings)
    # Lowest ring that is well populated and level with a neighbouring ring;
    # a lone low ring is a gap or a piece of the inner wall, not the plate.
    for idx in sorted((i for i, (rz, _c) in enumerate(rings) if rz is not None), key=lambda i: rings[i][0]):
        rz, count = rings[idx]
        neighbours = [rings[j][0] for j in (idx - 1, idx + 1) if 0 <= j < len(rings) and rings[j][0] is not None]
        if count >= 0.3 * most and any(abs(rz - nz_) < 0.2 for nz_ in neighbours):
            eave = rz
            break
    if eave is None:
        eave = float(np.percentile(z[rim], 30))
    out["eave_rings"] = [(None if rz is None else round(rz, 3), c) for rz, c in rings]
    wall_top = eave if z_top - eave > 0.3 else z_top
    edges = np.arange(0.10, 0.901, 0.05)
    us, zs = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = up & (rho >= lo) & (rho < hi) & (z > wall_top - 0.5)
        if sel.sum() >= 8:
            us.append(0.5 * (lo + hi))
            zs.append(float(np.percentile(z[sel], 30)))
    if len(us) < 6:
        return out
    u = np.array(us)
    zz = np.array(zs)

    def trimmed(predict):
        keep = np.ones(len(u), dtype=bool)
        best = None
        for _ in range(4):
            pred, params = predict(u[keep], zz[keep])
            full = pred(u)
            err = zz - full
            rms = float(np.sqrt(np.mean(err[keep] ** 2)))
            best = (rms, full, params)
            new = np.abs(err) <= max(0.12, 2.5 * rms)
            if new.sum() < max(5, int(0.6 * len(u))) or np.array_equal(new, keep):
                break
            keep = new
        return best

    def flat(uu, z_):
        a = float(np.mean(z_))
        return (lambda x: np.full(len(x), a)), {"crown": a, "at_rim": a}

    def cone(uu, z_):
        coef = np.polyfit(1.0 - uu, z_, 1)
        return (lambda x: coef[1] + coef[0] * (1.0 - x)), {"crown": float(coef[1] + coef[0]), "at_rim": float(coef[1])}

    def cap(uu, z_):
        best = None
        for h in np.arange(0.05, 1.2 * radius, 0.02):
            rs = (radius * radius + h * h) / (2.0 * h)
            shape = np.sqrt(np.maximum(rs * rs - (uu * radius) ** 2, 0.0)) - (rs - h)
            a = float(np.mean(z_ - shape))
            e = float(np.mean((z_ - shape - a) ** 2))
            if best is None or e < best[0]:
                best = (e, h, rs, a)
        _e, h, rs, a = best

        def pred(x):
            return a + np.sqrt(np.maximum(rs * rs - (x * radius) ** 2, 0.0)) - (rs - h)

        return pred, {"crown": float(a + h), "sphere_radius": float(rs), "at_rim": float(a)}

    fits = {"flat": trimmed(flat), "cone": trimmed(cone), "dome": trimmed(cap)}
    rise_c = fits["cone"][2]["crown"] - wall_top
    rise_d = fits["dome"][2]["crown"] - wall_top
    rise = max(rise_c, rise_d)
    if rise < max(0.15, 0.03 * radius):
        roof = "flat"
    elif radius < small_tank_radius and rise < max(0.2, 0.12 * radius):
        # Small tanks: a near-flat plate (flatter than about 1:8) carrying a
        # platform, vents or a manhole is a flat roof. Steeper low cones and
        # low domes on small tanks are real (seen in the mesh sections) and
        # are kept as such.
        roof = "flat"
    else:
        roof = "dome" if fits["dome"][0] <= fits["cone"][0] else "cone"
    out.update(
        ok=True,
        roof=roof,
        wall_top=float(wall_top),
        crown=float(fits[roof][2]["crown"]) if roof != "flat" else float(wall_top),
        rms_flat=fits["flat"][0],
        rms_cone=fits["cone"][0],
        rms_dome=fits["dome"][0],
        dome_sphere_radius=fits["dome"][2].get("sphere_radius"),
        profile=list(zip(u.tolist(), zz.tolist())),
    )
    if roof == "flat":
        out["crown"] = float(max(wall_top, fits["flat"][2]["crown"]))
    return out


def _sphere_check(all_pts, center, radius: float, z_base: float, z_top: float) -> dict:
    """Is this a spherical tank of the fitted radius?

    Searches the sphere centre height that puts most of the surface above the
    equator on a sphere of the wall radius. A cylinder with a dome or cone
    roof has its cap far inside that sphere; a sphere does not.
    """
    out = {"sphere": False}
    if radius < 3.0 or len(all_pts) < 200:
        return out
    rho = np.hypot(all_pts[:, 0] - center[0], all_pts[:, 1] - center[1])
    z = all_pts[:, 2]
    sel = rho < radius * 1.02
    rho, z = rho[sel], z[sel]
    tol = max(0.15, 0.03 * radius)
    best = None
    for zc in np.arange(z_base, z_top + 0.5 * radius, 0.1):
        upper = z > zc + 0.3 * radius
        if upper.sum() < 50:
            continue
        on = upper & (np.abs(np.hypot(rho, z - zc) - radius) < tol)
        frac = on.sum() / upper.sum()
        if best is None or on.sum() > best[0]:
            best = (int(on.sum()), float(zc), float(frac))
    if best is None:
        return out
    count, zc, frac = best
    upper = z > zc + 0.3 * radius
    top = float(np.percentile(z[upper], 99)) if upper.any() else zc
    out.update(center_z=zc, upper_fraction=frac, top=top, radius=float(radius))
    # Most of the upper surface on the sphere, it reaches near the pole, and
    # it rises well above the last vertical stretch (a dome roof does not).
    # Where the surface stops being a vertical wall at the fitted radius:
    # the equator for a sphere, the eave/handrail for a cylinder.
    cyl = np.abs(rho - radius) < tol
    cyl_top = float(np.percentile(z[cyl], 98)) if cyl.sum() >= 20 else z_top
    out["cylinder_top"] = cyl_top
    is_sphere = frac >= 0.38 and top >= zc + 0.85 * radius and top - cyl_top >= 0.5 * radius and count >= 100
    out["sphere"] = bool(is_sphere)
    if is_sphere:
        # Refine centre height and radius on the upper hemisphere; the wall
        # fit is pulled by support legs and mesh skirts under the equator.
        near = (z > zc + 0.2 * radius) & (np.abs(np.hypot(rho, z - zc) - radius) < 0.6)
        r_n, z_n = rho[near], z[near]
        best_e = None
        for dz in np.arange(-0.6, 0.61, 0.05):
            d = np.hypot(r_n, z_n - (zc + dz))
            for rr in np.arange(radius - 0.6, radius + 0.61, 0.02):
                e = float(np.median(np.abs(d - rr)))
                if best_e is None or e < best_e[0]:
                    best_e = (e, zc + dz, rr)
        out.update(center_z=float(best_e[1]), radius=float(best_e[2]))
    return out


def _sphere_legs(wall_pts, center, radius: float, ground: float, center_z: float) -> dict:
    """Support columns under a spherical tank: peaks in the azimuth histogram
    of vertical faces between the ground and the lower hemisphere."""
    if len(wall_pts) == 0:
        return {"legs": []}
    rho = np.hypot(wall_pts[:, 0] - center[0], wall_pts[:, 1] - center[1])
    z = wall_pts[:, 2]
    sel = (rho > 0.6 * radius) & (rho < 1.1 * radius) & (z > ground + 0.5) & (z < center_z - 0.6 * radius)
    if sel.sum() < 100:
        return {"legs": []}
    ang = np.degrees(np.arctan2(wall_pts[sel, 1] - center[1], wall_pts[sel, 0] - center[0])) % 360.0
    bins = 72
    hist, _ = np.histogram(ang, bins=bins, range=(0.0, 360.0))
    peak = hist > 2.5 * max(1.0, float(np.median(hist)))
    if peak.all() or not peak.any():
        return {"legs": []}
    start = int(np.flatnonzero(~peak)[0])
    order = [(start + k) % bins for k in range(bins)]
    runs, current = [], []
    for b in order:
        if peak[b]:
            current.append(b)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    width = 360.0 / bins
    legs = []
    for run in runs:
        w = hist[run].astype(float)
        a = np.radians((np.array(run) + 0.5) * width)
        mean = np.degrees(np.arctan2((np.sin(a) * w).sum(), (np.cos(a) * w).sum())) % 360.0
        legs.append(float(mean))
    if not (3 <= len(legs) <= 24):
        return {"legs": []}
    legs = _regular_legs(sorted(legs))
    # Leg size and position: a circle fit to the vertical faces of each leg
    # low down, where nothing else is attached.
    z_hi = min(ground + 4.0, center_z - 0.6 * radius)
    low = (z > ground + 1.0) & (z < z_hi) & (rho > 0.6 * radius) & (rho < 1.1 * radius)
    ang_all = np.degrees(np.arctan2(wall_pts[:, 1] - center[1], wall_pts[:, 0] - center[0])) % 360.0
    diameters, rhos = [], []
    for leg in legs:
        near = low & (np.abs((ang_all - leg + 180.0) % 360.0 - 180.0) < 7.0)
        if near.sum() < 12:
            continue
        px = wall_pts[near, 0] - center[0]
        py = wall_pts[near, 1] - center[1]
        a_ = np.column_stack([px, py, np.ones(len(px))])
        sol = np.linalg.lstsq(a_, px * px + py * py, rcond=None)[0]
        xc, yc = sol[0] / 2.0, sol[1] / 2.0
        rr = float(np.sqrt(max(sol[2] + xc * xc + yc * yc, 0.0)))
        if 0.05 < rr < 0.8:
            diameters.append(2.0 * rr)
            rhos.append(float(np.hypot(xc, yc)))
    leg_d = float(np.clip(np.median(diameters), 0.2, 1.2)) if diameters else 0.6
    leg_rho = float(np.median(rhos)) if rhos else float(np.median(rho[sel]))
    # Where a vertical leg at leg_rho meets the sphere (its welded top).
    contact = center_z - float(np.sqrt(max(radius * radius - leg_rho * leg_rho, 0.0)))
    return {
        "legs": legs,
        "leg_rho": leg_rho,
        "leg_radius": leg_d / 2.0,
        "leg_diameter": leg_d,
        "leg_diameter_samples": len(diameters),
        "leg_bottom": float(ground),
        "leg_top": float(center_z),
        "leg_contact_z": float(contact),
    }


def _regular_legs(peaks: list[float]) -> list[float]:
    """Snap leg azimuths to an even spacing when the peaks support one.

    Support legs of a sphere are evenly spaced; mesh clutter (stairs, pipes)
    adds or merges a peak now and then.
    """
    if len(peaks) < 3:
        return peaks
    arr = np.sort(np.asarray(peaks) % 360.0)
    gaps = np.diff(np.concatenate([arr, arr[:1] + 360.0]))
    spacing = float(np.median(gaps))
    n = int(round(360.0 / spacing)) if spacing > 0 else 0
    if not (3 <= n <= 24):
        return peaks
    step = 360.0 / n
    phase = np.degrees(np.angle(np.mean(np.exp(1j * np.radians(arr * n))))) / n
    slots = (phase + step * np.arange(n)) % 360.0
    hit = [np.min(np.abs((slots - p + 180.0) % 360.0 - 180.0)) < 0.25 * step for p in arr]
    if np.mean(hit) < 0.7:
        return peaks
    return sorted(float(a) for a in slots)


def _cone_or_dome(roof: str, roof_pts, center, radius: float, z_top: float) -> str:
    """Re-check cone vs dome on a median radial profile.

    The least-squares test in ``_classify_roof`` is easily swayed by vents,
    walkways and handrails on real roofs. Medians per radial bin are not.
    At half the radius a cone has dropped to about half its rise; a
    spherical cap is still at roughly three quarters of it.
    """
    if roof not in ("cone", "dome") or len(roof_pts) < 50:
        return roof
    rho = np.hypot(roof_pts[:, 0] - center[0], roof_pts[:, 1] - center[1]) / radius
    z = roof_pts[:, 2] - z_top
    keep = (rho < 0.98) & (z > -0.5)

    def band(lo, hi):
        sel = keep & (rho >= lo) & (rho < hi)
        return float(np.median(z[sel])) if sel.sum() >= 8 else None

    crown = band(0.0, 0.2)
    mid = band(0.4, 0.6)
    eave = band(0.85, 0.98)
    if crown is None or mid is None:
        return roof
    base = min(0.0, eave) if eave is not None else 0.0
    rise = crown - base
    if rise < max(0.3, 0.04 * radius):
        return roof
    ratio = (mid - base) / rise
    return "dome" if ratio >= 0.62 else "cone"


def _roof_profile(roof_pts, center, radius: float, z_top: float, bins: int = 10) -> list:
    """Median roof height above the wall top in radial bins (debug output)."""
    if len(roof_pts) == 0:
        return []
    rho = np.hypot(roof_pts[:, 0] - center[0], roof_pts[:, 1] - center[1]) / radius
    out = []
    for i in range(bins):
        sel = (rho >= i / bins) & (rho < (i + 1) / bins) & (roof_pts[:, 2] >= z_top - 0.2)
        out.append(round(float(np.median(roof_pts[sel, 2]) - z_top), 3) if sel.sum() >= 5 else None)
    return out


def _roof_height(roof_pts, center, radius, z_base, z_top, roof: str) -> tuple[float, float]:
    if len(roof_pts) == 0:
        return 0.0, z_top - z_base
    rho = np.hypot(roof_pts[:, 0] - center[0], roof_pts[:, 1] - center[1])
    cap = roof_pts[(rho < radius * 0.97) & (roof_pts[:, 2] >= z_top - 0.3)]
    if len(cap) == 0:
        return 0.0, z_top - z_base
    z_max = float(np.percentile(cap[:, 2], 99))
    rise = max(0.0, z_max - z_top)
    if roof == "flat":
        return 0.0, z_top - z_base
    return rise, (z_top - z_base) + rise


def _angular_coverage(xy: np.ndarray, center, bins: int = 36) -> float:
    return _coverage(xy, center, bins)


def _top_rim(roof_pts, outer_pts, inside_pts, center, radius, z_top) -> float | None:
    chunks = []
    for cloud in (roof_pts, inside_pts):
        if len(cloud) == 0:
            continue
        rho = np.hypot(cloud[:, 0] - center[0], cloud[:, 1] - center[1])
        mask = (np.abs(cloud[:, 2] - z_top) < 0.22) & (rho > radius - 0.15) & (rho < radius + 0.6)
        if np.any(mask):
            chunks.append(np.column_stack([cloud[mask, :2], rho[mask]]))
    if len(outer_pts):
        rho = np.hypot(outer_pts[:, 0] - center[0], outer_pts[:, 1] - center[1])
        nz = outer_pts[:, 3] if outer_pts.shape[1] > 3 else np.ones(len(outer_pts))
        mask = (np.abs(outer_pts[:, 2] - z_top) < 0.22) & (rho < radius + 0.6) & (nz > 0.35)
        if np.any(mask):
            chunks.append(np.column_stack([outer_pts[mask, :2], rho[mask]]))
    if not chunks:
        return None
    data = np.concatenate(chunks, axis=0)
    if _angular_coverage(data[:, :2], center) < 280:
        return None
    outer = float(np.percentile(data[:, 2], 90))
    # A cone or dome stays inside the wall radius; a top rim sticks out past it.
    if outer < radius + 0.08:
        return None
    width = float(np.percentile(data[:, 2], 95) - np.percentile(data[:, 2], 5))
    if width < 0.10:
        return None
    return width


def _wind_girders(outer_pts, center, radius, z_base, z_top) -> list[dict]:
    if len(outer_pts) < 40:
        return []
    rho = np.hypot(outer_pts[:, 0] - center[0], outer_pts[:, 1] - center[1])
    mask = (rho > radius + 0.04) & (rho < radius + 0.65)
    pts = outer_pts[mask]
    rho = rho[mask]
    if len(pts) < 40:
        return []
    z = pts[:, 2]
    bin_h = 0.10
    z0 = z.min()
    count = int(np.floor((z.max() - z0) / bin_h)) + 1
    hits = []
    for i in range(count):
        lo = z0 + i * bin_h
        sel = (z >= lo) & (z < lo + bin_h)
        if sel.sum() < 25:
            continue
        if _angular_coverage(pts[sel, :2], center) < 300:
            continue
        zz = float(np.median(z[sel]))
        if zz < z_base + 0.45 or zz > z_top - 0.35:
            continue
        hits.append((zz, float(np.median(rho[sel] - radius))))
    return _merge_z_hits(hits, gap=0.22)


def _base_ring(outer_pts, center, radius, z_base) -> dict | None:
    if len(outer_pts) < 30:
        return None
    rho = np.hypot(outer_pts[:, 0] - center[0], outer_pts[:, 1] - center[1])
    # A base ring is a band on the shell, above its base: points level with or below the base
    # belong to a concrete foundation, which grounding models separately.
    dz = outer_pts[:, 2] - z_base
    mask = (dz > 0.04) & (dz < 0.45) & (rho > radius + 0.04) & (rho < radius + 0.8)
    if mask.sum() < 25:
        return None
    if _angular_coverage(outer_pts[mask, :2], center) < 280:
        return None
    protrusion = float(np.median(rho[mask] - radius))
    if protrusion < 0.06:
        return None
    return {"protrusion": protrusion, "height": 0.25, "z": z_base}


def _merge_z_hits(hits: list[tuple[float, float]], gap: float) -> list[dict]:
    if not hits:
        return []
    hits = sorted(hits)
    groups = [[hits[0]]]
    for item in hits[1:]:
        if item[0] - groups[-1][-1][0] <= gap:
            groups[-1].append(item)
        else:
            groups.append([item])
    result = []
    for group in groups:
        result.append(
            {
                "z": float(np.mean([item[0] for item in group])),
                "protrusion": float(np.mean([item[1] for item in group])),
                "height": 0.15,
            }
        )
    return result


def _spiral_stair(outer_pts, center, radius, z_base, wall_h) -> dict | None:
    if len(outer_pts) < 80 or wall_h < 1.5:
        return None
    rho = np.hypot(outer_pts[:, 0] - center[0], outer_pts[:, 1] - center[1])
    z = outer_pts[:, 2]
    mask = (rho > radius + 0.28) & (rho < radius + 2.8) & (z > z_base + 0.25) & (z < z_base + wall_h + 0.4)
    if mask.sum() < 80:
        return None
    pts = outer_pts[mask]
    rho = rho[mask]
    z = z[mask]
    ang = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    order = np.argsort(z)
    z = z[order]
    ang = np.unwrap(ang[order])
    # The stair only occupies a moving window of angle; a global unwrap of all
    # points mixes treads. Use the per-height circular mean instead.
    bin_h = max(0.25, wall_h / 40.0)
    means_z = []
    means_a = []
    z0 = z.min()
    steps = int(np.ceil((z.max() - z0) / bin_h))
    raw_ang = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    raw_z = outer_pts[mask][:, 2]
    for i in range(steps):
        lo = z0 + i * bin_h
        sel = (raw_z >= lo) & (raw_z < lo + bin_h)
        if sel.sum() < 6:
            continue
        mean = _circular_mean(raw_ang[sel])
        means_z.append(lo + 0.5 * bin_h)
        means_a.append(mean)
    if len(means_z) < 8:
        return None
    zz = np.array(means_z)
    aa = np.unwrap(np.array(means_a))
    slope, intercept = np.polyfit(zz, aa, 1)
    pred = slope * zz + intercept
    if np.sqrt(np.mean((aa - pred) ** 2)) > 0.55:
        return None
    sweep = float(aa[-1] - aa[0])
    if abs(sweep) < 0.65 * 2 * np.pi:
        return None
    if abs(zz[-1] - zz[0]) < 0.45 * wall_h:
        return None
    outer = float(np.percentile(rho, 90))
    inner = float(np.percentile(rho, 15))
    width = max(0.2, outer - inner)
    turns = abs(sweep) / (2 * np.pi)
    return {
        "outer_radius": outer,
        "width": float(width),
        "turns": float(turns),
        "z_base": float(z_base),
        "height": float(wall_h),
    }


def _circular_mean(angles: np.ndarray) -> float:
    return float(np.arctan2(np.sin(angles).mean(), np.cos(angles).mean()))
