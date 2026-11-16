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
    min_radius: float = 1.5
    max_radius: float = 80.0
    min_height: float = 2.0
    max_height: float = 80.0
    max_rms: float = 0.20
    min_coverage: float = 260.0
    cell_size: float = 0.25


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


class SceneAccumulator:
    """Pass-1 state. Wall bins only; triangle meshes are discarded by the caller."""

    def __init__(self, config: DetectConfig):
        self.config = config
        # (ix, iy) -> [zmin, zmax, count, sumx, sumy]
        self.wall: dict[tuple[int, int], list[float]] = {}
        # (ix, iy) -> [zmin, count] for upward faces, 2 m cells
        self.ground: dict[tuple[int, int], list[float]] = {}
        self.tile_boxes: list[tuple[str, np.ndarray]] = []

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
        upward = valid & (nz > 0.72)
        if np.any(upward):
            centroid = (a[upward] + b[upward] + c[upward]) / 3.0
            _accumulate_ground(centroid, self.ground)
        box = np.vstack([absolute.min(axis=0), absolute.max(axis=0)])
        self.tile_boxes.append((name, box))

    def candidates(self) -> list[_Candidate]:
        if not self.wall:
            return []
        components = _components(self.wall, gap=2)
        found: list[_Candidate] = []
        for keys in components:
            candidate = _fit_component(keys, self.wall, self.config)
            if candidate is not None:
                found.append(candidate)
        return _merge_candidates(found, self.wall, self.config)

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
) -> list[Tank]:
    """Pass 2. ``tiles`` are absolute-coordinate meshes overlapping the candidates."""
    tanks: list[Tank] = []
    for candidate in candidates:
        samples = _collect_samples(candidate, tiles)
        tank = _measure(candidate, samples, config, ground_at, block, srs, origin)
        if tank is not None:
            tanks.append(tank)
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
    # Drop a candidate that sits inside a larger accepted tank (stair-only cluster).
    kept = []
    for item in merged:
        swallowed = False
        for other in merged:
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
        horizontal = nz_v > 0.55
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


def _measure(candidate, samples, config: DetectConfig, ground_at, block, srs, origin) -> Tank | None:
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
    if rms > config.max_rms or not (config.min_radius <= radius <= config.max_radius):
        return None
    coverage = _coverage(wall[keep, :2], center)
    if coverage < config.min_coverage:
        return None
    # Wall height is the vertical run of the cylinder itself. A dome or cone
    # that leaves the radius is not part of the wall, so use a tight radial band.
    shell = np.abs(dist - radius) <= max(0.03, 3.0 * rms)
    if shell.sum() < 20:
        shell = keep
    z_base = float(np.percentile(wall[shell, 2], 1))
    z_top = float(np.percentile(wall[shell, 2], 99))
    wall_h = z_top - z_base
    if wall_h < config.min_height or wall_h > config.max_height:
        return None
    roof_cloud = _roof_cloud(samples, center, radius, z_top)
    if len(roof_cloud):
        z_max = float(np.percentile(roof_cloud[:, 2], 99.5))
    else:
        z_max = z_top
    total = max(wall_h, z_max - z_base)
    rise = max(0.0, total - wall_h)
    roof = _classify_roof(roof_cloud, center, radius, z_top, rise)
    if roof == "flat":
        total = wall_h
        rise = 0.0
    else:
        rise, total = _roof_height(roof_cloud, center, radius, z_base, z_top, roof)
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
        z_ground=float(ground_at(float(center[0]), float(center[1]))),
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
    mask = (np.abs(outer_pts[:, 2] - z_base) < 0.45) & (rho > radius + 0.04) & (rho < radius + 0.8)
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
