"""Offline rule that drops tree crowns mistaken for tanks.

A dense, round tree crown can pass the cylinder test: its outline fits a circle and its
foliage gives "wall" faces all round. Two independent cues tell it apart, and both are
required, so a green-painted tank (clean geometry) or a rough grey tank (no foliage colour)
is never dropped:

* colour: the OSGB texture over the footprint is mostly vegetation green
  (hue 55-180 deg, saturated, excess green 2G-R-B high). Read from the textures embedded
  in the tiles themselves; no imagery service or model is involved.
* irregular geometry: the "wall" is rough (circle-fit residual), patchy over its height,
  and the top does not fit a flat / cone / dome lid.

When no texture can be read (osgconv reader, untextured tiles) nothing is dropped: small,
rough candidates are only flagged in the report for a manual look.

Everything here is plain numpy (+ Pillow to decode JPEG textures) and runs offline.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np


@dataclass
class TreeFilterConfig:
    # colour
    min_green_fraction: float = 0.60  # share of footprint texture that is foliage green (来安: trees 73-99%, real tanks <= 43%, green-painted BC Tank_064 76%)
    min_texture_samples: int = 40  # fewer textured triangles: colour is unknown
    # geometry irregularity (each a vote; tree needs >= min_votes)
    rough_wall_rms: float = 0.05  # circle-fit RMS (m); clean steel shells are ~0.02-0.04
    patchy_wall_fill: float = 0.85  # mean fraction of the wall height present per sector
    rough_roof_rms: float = 0.06  # best of flat / cone / dome lid fits (m)
    min_votes: int = 2
    # no texture: flag (never drop) candidates that are small, rough, patchy and lumpy at once
    fallback_max_radius: float = 3.5
    fallback_wall_rms: float = 0.05
    fallback_roof_rms: float = 0.08
    fallback_wall_fill: float = 0.82


def geometry_votes(geom: dict, config: TreeFilterConfig) -> tuple[int, list[str]]:
    votes = []
    rms = geom.get("rms")
    fill = geom.get("wall_fill")
    roof = geom.get("roof_rms")
    if rms is not None and rms >= config.rough_wall_rms:
        votes.append(f"罐壁粗糙 rms={rms:.3f}m")
    if fill is not None and fill < config.patchy_wall_fill:
        votes.append(f"罐壁不连续 fill={fill:.2f}")
    if roof is None or roof >= config.rough_roof_rms:
        votes.append("顶面不规则" + (f" rms={roof:.3f}m" if roof is not None else "（无法拟合罐顶）"))
    return len(votes), votes


def judge(geom: dict, colour: dict | None, radius: float, config: TreeFilterConfig | None = None) -> tuple[bool, str]:
    """(is_tree, reason). ``geom``: rms, wall_fill, roof_rms. ``colour``: green_fraction, samples."""
    config = config or TreeFilterConfig()
    n, votes = geometry_votes(geom, config)
    have_colour = bool(colour) and colour.get("samples", 0) >= config.min_texture_samples
    if have_colour:
        green = float(colour.get("green_fraction", 0.0))
        if green >= config.min_green_fraction and n >= config.min_votes:
            return True, f"树冠：纹理绿色占比 {green:.0%}；" + "；".join(votes)
        if green >= config.min_green_fraction:
            return False, f"绿色（{green:.0%}）但几何规整，保留（涂成绿色的罐）"
        return False, ""
    rms = geom.get("rms") or 0.0
    roof = geom.get("roof_rms")
    fill = geom.get("wall_fill")
    if (
        radius <= config.fallback_max_radius
        and rms >= config.fallback_wall_rms
        and (roof is None or roof >= config.fallback_roof_rms)
        and fill is not None and fill <= config.fallback_wall_fill
    ):
        # Geometry alone also matches small, weathered real tanks, so without colour the
        # candidate is only flagged for review, never dropped.
        return False, "疑似树冠（无纹理颜色，仅几何可疑，未剔除，请人工核对）：" + "；".join(votes)
    return False, ""


# --- texture colour --------------------------------------------------------------------

def _decode(image: bytes, cache: dict):
    key = id(image)
    if key in cache:
        return cache[key][1]
    try:
        from PIL import Image  # Pillow, local decode only

        with Image.open(io.BytesIO(image)) as im:
            im = im.convert("RGB")
            # textures are only sampled for a mean colour class; a smaller copy is plenty
            if max(im.size) > 1024:
                im.thumbnail((1024, 1024))
            pixels = np.asarray(im, dtype=np.uint8)
    except Exception:  # noqa: BLE001 - unreadable / unsupported texture: colour unknown
        pixels = None
    cache[key] = (image, pixels)  # keep the bytes alive so id() stays unique
    return pixels


def green_mask(rgb: np.ndarray) -> np.ndarray:
    """Foliage-green pixels in an (N, 3) uint8/float array."""
    rgb = np.asarray(rgb, dtype=np.float64)
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    mx = rgb.max(axis=1)
    mn = rgb.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    # hue in degrees
    delta = np.maximum(mx - mn, 1e-6)
    hue = np.where(
        mx == r, ((g - b) / delta) % 6.0, np.where(mx == g, (b - r) / delta + 2.0, (r - g) / delta + 4.0)
    ) * 60.0
    exg = 2.0 * g - r - b
    return (hue >= 55.0) & (hue <= 180.0) & (sat >= 0.15) & (exg >= 18.0) & (mx >= 25.0)


def sample_colours(meshes, cx: float, cy: float, radius: float, z_lo: float, z_hi: float,
                   cache: dict | None = None) -> np.ndarray:
    """Texture colour (N, 3) at the centroid of every textured triangle inside the cylinder.

    ``meshes``: iterable of (absolute vertices, triangles, uv, image bytes).
    """
    cache = {} if cache is None else cache
    out = []
    for vertices, triangles, uv, image in meshes:
        if uv is None or image is None or len(triangles) == 0:
            continue
        cen = (vertices[triangles[:, 0]] + vertices[triangles[:, 1]] + vertices[triangles[:, 2]]) / 3.0
        rho = np.hypot(cen[:, 0] - cx, cen[:, 1] - cy)
        take = (rho < radius + 0.3) & (cen[:, 2] > z_lo) & (cen[:, 2] < z_hi)
        if not np.any(take):
            continue
        pixels = _decode(image, cache)
        if pixels is None:
            continue
        h, w = pixels.shape[:2]
        tri = triangles[take]
        tuv = (uv[tri[:, 0]] + uv[tri[:, 1]] + uv[tri[:, 2]]) / 3.0
        u = np.mod(tuv[:, 0], 1.0)
        v = np.mod(tuv[:, 1], 1.0)
        # OpenSceneGraph texture origin is the lower-left corner of the image
        px = np.clip((u * w).astype(int), 0, w - 1)
        py = np.clip(((1.0 - v) * h).astype(int), 0, h - 1)
        out.append(pixels[py, px])
    if not out:
        return np.zeros((0, 3), dtype=np.uint8)
    return np.concatenate(out, axis=0)


def colour_stats(rgb: np.ndarray) -> dict:
    if len(rgb) == 0:
        return {"samples": 0}
    mask = green_mask(rgb)
    mean = np.asarray(rgb, dtype=np.float64).mean(axis=0)
    return {
        "samples": int(len(rgb)),
        "green_fraction": float(mask.mean()),
        "mean_rgb": [round(float(c), 1) for c in mean],
    }


def roof_roughness(roof_pts: np.ndarray, cx: float, cy: float, radius: float) -> float | None:
    """RMS (m) of the lid about a smooth radial profile z = a + b*rho + c*rho^2."""
    if roof_pts is None or len(roof_pts) < 30:
        return None
    rho = np.hypot(roof_pts[:, 0] - cx, roof_pts[:, 1] - cy) / max(radius, 1e-6)
    z = roof_pts[:, 2]
    a = np.column_stack([np.ones_like(rho), rho, rho * rho])
    coef, *_ = np.linalg.lstsq(a, z, rcond=None)
    res = np.abs(z - a @ coef)
    keep = res <= np.percentile(res, 95)  # vents / ladders on the lid are not lumps
    return float(np.sqrt(np.mean(res[keep] ** 2)))
