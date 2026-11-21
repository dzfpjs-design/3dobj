"""Deliverable naming, tolerance and face budget.

``precision_m`` is the allowed sagitta of a large smooth shell (the tank wall
or a sphere). It is not a licence to delete rails, treads, openings or girder
pipes: those stay as their own objects even when they are thinner than the
tolerance. ``max_faces`` is a per-object polygon budget used to choose the
tessellation. Crossing it is reported; the part is kept.
"""

from __future__ import annotations

import math
import re

_TANK = re.compile(r"^Tank_(\d+)$")
_NUM = re.compile(r"^(Connector|Stair|PipeRack|Pipe)_(\d+)$")
_PREFIX = {"Connector": "CN", "Stair": "ST", "PipeRack": "PR", "Pipe": "PP"}


def chord_segments(radius: float, tolerance: float, *, minimum: int = 12, maximum: int = 96) -> int:
    """Sides of a regular polygon whose sagitta is within ``tolerance`` metres."""
    if radius <= 1e-6 or tolerance <= 0:
        return minimum
    ratio = 1.0 - min(tolerance / radius, 0.999)
    angle = math.acos(ratio)
    if angle <= 1e-6:
        count = maximum
    else:
        count = math.ceil(math.pi / angle)
    return int(max(minimum, min(maximum, count)))


def shell_segments(radius: float, tolerance: float | None, max_faces: int | None, explicit: int, roof: str = "flat") -> tuple[int, int]:
    """(circumference segments, roof rings) for one shell object.

    ``explicit`` is the upper bound (--segments). The tolerance only reduces
    that bound. The face budget reduces it further, but never below 12 sides,
    so a cylinder still reads as a cylinder.
    """
    rings = 1 if roof in ("flat", "cone", "sphere") else 8
    if roof == "dome":
        rings = chord_segments(max(radius, 1.0), tolerance or 0.5, minimum=4, maximum=8)
    count = explicit
    if tolerance and tolerance > 0 and radius > 0:
        count = min(count, chord_segments(radius, tolerance, minimum=12, maximum=explicit))
    budget = max_faces or 0
    while budget and count > 12 and count * (rings + 3) > budget:
        count -= 1
    return max(12, count), rings


def latin_token(text: str) -> str:
    """ASCII letters, digits and underscores. A door mark is kept as 'door'."""
    raw = text.replace("门", "door")
    out = []
    for ch in raw:
        if ch.isascii() and (ch.isalnum() or ch == "_"):
            out.append(ch)
        elif ch in "- ./":
            out.append("_")
    token = "".join(out).strip("_")
    token = re.sub(r"_+", "_", token)
    return token or "part"


def is_door(name: str, kind: str = "") -> bool:
    blob = f"{name} {kind}"
    return "门" in blob or "door" in blob.lower()


def latin_part(name: str, kind: str = "") -> str:
    token = latin_token(name)
    if is_door(name, kind) and "door" not in token.lower():
        token = f"{token}_door"
    return token


def export_name_for(source_id: str, prefix: str = "LA") -> str:
    """Stable Latin code. Tank_007 stays LA0007 so the old id is recoverable."""
    match = _TANK.fullmatch(source_id or "")
    if match:
        return f"{prefix}{int(match.group(1)):04d}"
    match = _NUM.fullmatch(source_id or "")
    if match:
        return f"{_PREFIX[match.group(1)]}{int(match.group(2)):04d}"
    return latin_token(source_id or "part")


def assign_export_names(tanks, connectors, structures, prefix: str = "LA") -> list[dict]:
    rows = []
    for tank in tanks:
        code = export_name_for(tank.name, prefix)
        tank.export_name = code
        rows.append({"export_name": code, "source_id": tank.name, "kind": "tank"})
    for conn in connectors:
        code = export_name_for(conn.name, prefix)
        conn.export_name = code
        rows.append({"export_name": code, "source_id": conn.name, "kind": conn.kind})
    for structure in structures:
        code = export_name_for(structure.name, prefix)
        structure.export_name = code
        rows.append({"export_name": code, "source_id": structure.name, "kind": structure.kind})
    return rows


def handrail_gaps(tank) -> list[dict]:
    """Openings where a stair or a walkway arrives. No stair means no opening."""
    gaps = []
    accessories = getattr(tank, "accessories", None) or {}
    stair = accessories.get("wall_stair") or {}
    if stair.get("az_top_deg") is not None:
        gaps.append({
            "azimuth_deg": float(stair["az_top_deg"]) % 360.0,
            "width_m": max(float(stair.get("width") or 0.8), 0.8),
        })
    spiral = getattr(tank, "spiral_stair", None) or {}
    if spiral.get("az_top_deg") is not None:
        gaps.append({
            "azimuth_deg": float(spiral["az_top_deg"]) % 360.0,
            "width_m": max(float(spiral.get("width") or 0.8), 0.8),
        })
    roof = accessories.get("roof") or {}
    for azimuth in roof.get("open_azimuths") or []:
        gaps.append({"azimuth_deg": float(azimuth) % 360.0, "width_m": 1.0})
    return gaps


def seat_walkway_ends(tanks, connectors) -> None:
    """Pull a walkway end onto the tank-top platform so the deck meets the platform.

    Ends without a measured platform are left where the corridor detector put them.
    """
    by_name = {tank.name: tank for tank in tanks}
    for conn in connectors:
        if getattr(conn, "kind", "") != "walkway":
            continue
        for end in ("a", "b"):
            tank = by_name.get(conn.tank_a if end == "a" else conn.tank_b)
            if tank is None:
                continue
            platform = ((getattr(tank, "accessories", None) or {}).get("roof") or {}).get("platform")
            if platform:
                _move_end(conn, tank, end, platform)


def _move_end(conn, tank, end: str, platform: dict) -> None:
    if end == "a":
        x, y = conn.x0, conn.y0
        other = (conn.x1, conn.y1)
    else:
        x, y = conn.x1, conn.y1
        other = (conn.x0, conn.y0)
    vx, vy = x - tank.center_x, y - tank.center_y
    dist = math.hypot(vx, vy)
    radius = float(platform["radius"])
    if dist < 1e-6 or dist <= radius + 0.05:
        return
    nx = tank.center_x + vx / dist * radius
    ny = tank.center_y + vy / dist * radius
    if math.hypot(nx - other[0], ny - other[1]) < 0.4:
        return
    nz = float(platform.get("z", conn.z0 if end == "a" else conn.z1))
    if end == "a":
        conn.x0, conn.y0, conn.z0 = nx, ny, nz
    else:
        conn.x1, conn.y1, conn.z1 = nx, ny, nz


def tessellation_segments(explicit: int, precision_m: float | None = None, max_faces: int | None = None) -> int:
    """Shell segment count actually used.

    ``precision_m`` and ``max_faces`` are recorded for the panel and the report.
    They do not reduce the count: 50 cm and 2000 faces were not confirmed as
    thresholds that may delete rails, openings or girders.
    """
    del precision_m, max_faces
    return int(explicit)


def face_budget_row(name: str, polygons: int, limit: int | None) -> dict:
    exceeded = bool(limit) and polygons > int(limit)
    return {"object": name, "polygons": polygons, "limit": limit, "exceeded": exceeded, "kept": True}


def polygon_counts(faces) -> tuple[int, int]:
    """(polygon count, triangle count). An n-gon counts as n-2 triangles."""
    polygons = 0
    triangles = 0
    for face in faces:
        size = len(face)
        if size < 3:
            continue
        polygons += 1
        triangles += size - 2
    return polygons, triangles
