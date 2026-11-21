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
        conn.junctions = _junctions(conn, by_name)
        _align_bridge_rail_to_measured_platform(conn, by_name)


def _junctions(conn, by_name) -> list[dict]:
    """Whether each end meets a measured roof platform.

    A deck that merely stops inside the shell radius is recorded as
    ``not_measured``. It is not treated as a platform connection.
    """
    rows = []
    for end, tank_name, x, y, z in (
        ("a", conn.tank_a, conn.x0, conn.y0, conn.z0),
        ("b", conn.tank_b, conn.x1, conn.y1, conn.z1),
    ):
        tank = by_name.get(tank_name)
        if tank is None:
            continue
        dist = math.hypot(x - tank.center_x, y - tank.center_y)
        platform = ((getattr(tank, "accessories", None) or {}).get("roof") or {}).get("platform")
        row = {
            "end": end,
            "tank": tank.name,
            "inside_shell_m": round(tank.radius - dist, 3),
            "platform": "not_measured",
        }
        if platform:
            radius = float(platform["radius"])
            level = platform.get("z")
            on_disk = dist <= radius + 0.15
            at_level = level is None or abs(z - float(level)) <= 0.40
            row["platform"] = "meets_platform" if on_disk and at_level else "misses_platform"
            row["platform_radius_m"] = round(radius, 3)
        rows.append(row)
    return rows


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


# Lowest shell division. Below this the wall stops reading as a cylinder.
# Rails, treads and legs do not use this number.
SHELL_FLOOR = 16


def plan_shell(
    radius: float,
    explicit: int,
    max_faces: int | None,
    roof: str = "flat",
    quality: str = "full",
    precision_m: float = 0.50,
) -> dict:
    """Tessellation for one large shell (wall, dome, sphere, foundation).

    ``quality="full"`` keeps ``explicit`` (the historical 96).
    ``quality="budget"`` sets the shell chord error from ``precision_m``
    (default 0.50 m) and then clamps that shell to ``max_faces``.
    The precision value is not applied to rails, stairs, legs or openings.
    """
    explicit = max(3, int(explicit))
    if quality != "budget":
        rings = max(12, explicit // 2) if roof == "sphere" else (12 if roof == "dome" else 1)
        return {
            "segments": explicit,
            "rings": rings,
            "quality": "full",
            "sagitta_m": _sagitta(radius, explicit),
            "chord_target_m": None,
            "exceeded_estimate": False,
            "reason": "quality full keeps the requested segment count",
        }
    chord = 0.50 if precision_m is None or precision_m <= 0 else float(precision_m)
    count = chord_segments(max(radius, 0.1), chord, minimum=SHELL_FLOOR, maximum=explicit)
    if roof == "sphere":
        rings = chord_segments(max(radius, 0.1), chord, minimum=8, maximum=max(8, explicit // 2))
        steps = rings
    elif roof == "dome":
        rings = chord_segments(max(radius, 0.1), chord, minimum=4, maximum=8)
        steps = 2 + rings
    else:
        rings = 1
        steps = 3
    reason = (
        f"shell chord target {chord:.2f} m from precision_m, floor {SHELL_FLOOR} sides; "
        "rails, stairs, legs and openings ignore precision_m"
    )
    while max_faces and count > SHELL_FLOOR and count * steps > int(max_faces):
        count -= 1
        reason = (
            f"shell segments reduced so this shell stays within {int(max_faces)} polygons "
            f"after the {chord:.2f} m chord; floor is {SHELL_FLOOR} sides"
        )
    if roof == "sphere":
        while max_faces and rings > 8 and count * rings > int(max_faces):
            rings -= 1
            steps = rings
            reason = f"sphere rings reduced to fit {int(max_faces)} polygons; legs and rails are kept"
    estimate = count * steps
    exceeded = bool(max_faces) and estimate > int(max_faces)
    if exceeded:
        reason = (
            f"shell still estimates {estimate} polygons at the {SHELL_FLOOR}-side floor "
            f"with {rings} rings, over {int(max_faces)}; the shell was kept"
        )
    return {
        "segments": count,
        "rings": rings,
        "quality": "budget",
        "sagitta_m": _sagitta(radius, count),
        "chord_target_m": chord,
        "exceeded_estimate": exceeded,
        "reason": reason,
    }


def _sagitta(radius: float, segments: int) -> float:
    if radius <= 0 or segments <= 0:
        return 0.0
    return float(radius * (1.0 - math.cos(math.pi / segments)))


def tessellation_segments(
    explicit: int,
    precision_m: float | None = None,
    max_faces: int | None = None,
    *,
    quality: str = "full",
    radius: float = 10.0,
    roof: str = "flat",
) -> int:
    """Segment count a shell uses. In budget mode this follows ``precision_m``."""
    chord = 0.50 if precision_m is None else precision_m
    return plan_shell(radius, explicit, max_faces, roof, quality, chord)["segments"]


def face_budget_row(name: str, polygons: int, limit: int | None) -> dict:
    exceeded = bool(limit) and polygons > int(limit)
    return {"object": name, "polygons": polygons, "limit": limit, "exceeded": exceeded, "kept": True}


def _align_bridge_rail_to_measured_platform(conn, by_name) -> None:
    """Set a walkway rail to the measured platform-rail height when they disagree.

    Only platform rails already measured on the joined tanks are used. There is
    no fallback constant. The previous walkway measurement is kept on
    ``handrail_height_measured``.
    """
    measured = getattr(conn, "handrail_height", None)
    if measured is None:
        return
    heights = []
    for name in (getattr(conn, "tank_a", ""), getattr(conn, "tank_b", "")):
        tank = by_name.get(name)
        if tank is None:
            continue
        platform = ((getattr(tank, "accessories", None) or {}).get("roof") or {}).get("platform") or {}
        if platform.get("rail_height"):
            heights.append(float(platform["rail_height"]))
    if not heights:
        return
    ordered = sorted(heights)
    target = ordered[len(ordered) // 2]
    if abs(float(measured) - target) <= 0.15:
        return
    conn.handrail_height_measured = float(measured)
    conn.handrail_height = float(target)
    conn.rail_source = "measured_platform_rail"


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
