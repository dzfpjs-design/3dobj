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
        deg = float(azimuth) % 360.0
        # A measured stair already opens this arrival. Do not widen it to 1 m.
        if any(abs(((deg - gap["azimuth_deg"] + 180.0) % 360.0) - 180.0) < 12.0 for gap in gaps):
            continue
        gaps.append({"azimuth_deg": deg, "width_m": 1.0})
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
        inside = tank.radius - dist
        if inside > 0.15:
            landing = "inside_shell"
        elif inside < -0.15:
            landing = "short_of_wall"
        else:
            landing = "at_wall"
        row = {
            "end": end,
            "tank": tank.name,
            "inside_shell_m": round(inside, 3),
            "platform": "not_measured",
            "landing": landing,
            "end_stop": (getattr(conn, "end_stops", None) or {}).get(end, ""),
            "end_source": (getattr(conn, "end_sources", None) or {}).get(end, "measured"),
            # A small height difference is a measurement, not a completed joint.
            "accepted": False,
        }
        if platform is None and _has_roof(tank):
            formula = _roof_z_at(tank, dist)
            measured = _profile_z_at(tank, dist)
            dz_formula = z - formula
            row["roof_z_formula_m"] = round(formula, 3)
            row["deck_z_m"] = round(z, 3)
            row["dz_formula_m"] = round(dz_formula, 3)
            row["radial_inside_m"] = round(tank.radius - dist, 3)
            if measured is None:
                row["confidence"] = "formula_only"
                row["dz_m"] = round(dz_formula, 3)
                row["relation"] = "formula_only"
            else:
                row["roof_z_profile_m"] = round(measured, 3)
                fit = (getattr(conn, "end_fit", None) or {}).get(end) or {}
                landing = fit.get("landing_roof_z")
                compared = float(landing) if landing is not None else measured
                dz = z - compared
                row["roof_z_measured_m"] = round(compared, 3)
                row["dz_profile_m"] = round(z - measured, 3)
                row["dz_measured_m"] = round(dz, 3)
                row["dz_m"] = round(dz, 3)
                row["confidence"] = "landing_sector" if landing is not None else "measured_roof_profile"
                kind = classify_end_fit(fit.get("line_z"), fit.get("local_z"), measured)
                row["deck_z_line_m"] = fit.get("line_z")
                row["deck_z_local_m"] = fit.get("local_z")
                row["deck_z_used"] = fit.get("used") or "model"
                row["fit_class"] = kind
                row["dz_line_m"] = None if fit.get("line_z") is None else round(float(fit["line_z"]) - measured, 3)
                row["dz_local_m"] = None if fit.get("local_z") is None else round(float(fit["local_z"]) - measured, 3)
                row["ramp_z_m"] = fit.get("ramp_z")
                row["landing_roof_z_m"] = fit.get("landing_roof_z")
                row["landing_band"] = fit.get("landing_band")
                source = (getattr(conn, "end_sources", None) or {}).get(end, "measured")
                seated = "seat_to_measured_roof" in str(source)
                conflict = getattr(conn, "seat_conflict", None) or {}
                row["seat_rule"] = source if source not in ("", "measured") else "off"
                row["seat_target_z_m"] = fit.get("landing_roof_z") if fit.get("landing_roof_z") is not None else round(measured, 3)
                row["constraint_conflict"] = bool(conflict) and "conflict" in str(source)
                if conflict:
                    row["slope_measured_deg"] = conflict.get("slope_before_deg")
                    row["slope_if_seated_deg"] = conflict.get("slope_if_seated_deg")
                    row["seat_preview_z_m"] = conflict.get("preview_z0") if end == "a" else conflict.get("preview_z1")
                before = getattr(conn, "z0_before_rule" if end == "a" else "z1_before_rule", None)
                if before is not None:
                    row["deck_z_before_rule_m"] = round(float(before), 3)
                    row["dz_before_rule_m"] = round(float(before) - measured, 3)
                    row["correction_dz_m"] = round(z - float(before), 3)
                if row["constraint_conflict"]:
                    row["relation"] = "seat_conflict"
                    row["source_mesh"] = True
                    row["correction_note"] = conflict.get("reason") or "贴屋面与实测桥坡冲突，桥面未改。"
                elif "already_on_landing" in str(source):
                    row["correction_note"] = "已请求贴桥头屋面。该处屋面与实测桥面相差不超过 15 cm，桥面未改。"
                    row["relation"] = "deck_on_measured_roof"
                    row["source_mesh"] = True
                elif seated:
                    row["relation"] = "manual_seated_on_measured_roof"
                    row["source_mesh"] = False
                    row["correction_note"] = "人工校正把端点放到实测屋面板。校正前桥面高程仍是网格测量。"
                elif abs(dz) <= 0.15:
                    row["relation"] = "deck_on_measured_roof"
                    row["source_mesh"] = True
                elif dz > 0:
                    row["relation"] = "deck_above_measured_roof"
                    row["source_mesh"] = True
                else:
                    row["relation"] = "deck_below_measured_roof"
                    row["source_mesh"] = True
            row["platform"] = "not_measured"
        if platform:
            radius = float(platform["radius"])
            level = platform.get("z")
            outside = max(0.0, dist - radius)
            dz = None if level is None else z - float(level)
            # A walkable joint is the end on the platform disk and on its
            # measured walking surface. Being inside the tank shell is not enough.
            meets = outside <= 0.30 and dz is not None and abs(dz) <= 0.15
            row["platform"] = "meets_platform" if meets else "misses_platform"
            row["platform_radius_m"] = round(radius, 3)
            row["platform_z_m"] = None if level is None else round(float(level), 3)
            row["deck_z_m"] = round(z, 3)
            row["dz_m"] = None if dz is None else round(dz, 3)
            row["radial_outside_m"] = round(outside, 3)
            row["junction_gap_m"] = None if dz is None else round(math.hypot(outside, dz), 3)
            before = getattr(conn, "z0_before_platform" if end == "a" else "z1_before_platform", None)
            if before is not None:
                row["deck_z_before_m"] = round(float(before), 3)
                row["seat_dz_m"] = round(z - float(before), 3)
            row["confidence"] = "measured" if meets else "measured_miss"
        rows.append(row)
    return rows


def seat_slope_conflict(x0, y0, z0, x1, y1, z1, prop0, prop1, limit_deg: float = 4.0):
    """True when seating the ends would bend the measured ramp by more than ``limit_deg``.

    The returned slopes are degrees. A conflict is not a completed joint.
    """
    run = math.hypot(x1 - x0, y1 - y0)
    if run < 0.5:
        return False, 0.0, 0.0
    before = math.degrees(math.atan2(z1 - z0, run))
    after = math.degrees(math.atan2(prop1 - prop0, run))
    return abs(after - before) > limit_deg, before, after


def landing_seat_z(conn, end: str, tanks) -> float | None:
    """Height a roof seat would use: the bridge-head band, or the radial profile if that band is absent."""
    fit = (getattr(conn, "end_fit", None) or {}).get(end) or {}
    if fit.get("landing_roof_z") is not None:
        return float(fit["landing_roof_z"])
    tank_name = conn.tank_a if end == "a" else conn.tank_b
    tank = next(
        (item for item in tanks if item.name == tank_name or getattr(item, "export_name", "") == tank_name),
        None,
    )
    if tank is None or not _has_roof(tank):
        return None
    x = conn.x0 if end == "a" else conn.x1
    y = conn.y0 if end == "a" else conn.y1
    return _profile_z_at(tank, math.hypot(x - tank.center_x, y - tank.center_y))


def apply_seat_ends(conn, proposals: dict, source_prefix: str) -> dict:
    """Move proposed ends, or refuse a seat that would change the measured slope by more than 4°.

    ``proposals`` maps ``a``/``b`` to ``(target_z, rule)`` or ``(target_z, rule, spec)``.
    An explicit numeric target is not slope-gated. A seat already within 15 cm of the
    deck is recorded and does not move the deck. That 15 cm is not a completed joint.
    """
    if getattr(conn, "end_sources", None) is None:
        conn.end_sources = {}
    prop0 = proposals["a"][0] if "a" in proposals else conn.z0
    prop1 = proposals["b"][0] if "b" in proposals else conn.z1
    seating = any(item[1] == "seat_to_measured_roof" for item in proposals.values())
    conflict, before, after = seat_slope_conflict(
        conn.x0, conn.y0, conn.z0, conn.x1, conn.y1, conn.z1, prop0, prop1,
    )
    if seating and conflict and proposals:
        conn.seat_conflict = {
            "applied": False,
            "slope_before_deg": round(before, 2),
            "slope_if_seated_deg": round(after, 2),
            "preview_z0": round(float(prop0), 3),
            "preview_z1": round(float(prop1), 3),
            "reason": "贴两端屋面会改变实测桥坡，已拒绝，桥面保持测量值",
        }
        for end in proposals:
            conn.end_sources[end] = f"{source_prefix}:conflict_not_applied"
        return {
            "status": "conflict",
            "moved": 0,
            "slope_before_deg": before,
            "slope_after_deg": after,
            "ends": {end: "conflict" for end in proposals},
        }
    ends = {}
    moved = 0
    for end, item in proposals.items():
        target = float(item[0])
        rule = item[1]
        spec = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
        current = conn.z0 if end == "a" else conn.z1
        if abs(target - current) <= 0.15 and rule == "seat_to_measured_roof":
            conn.end_sources[end] = f"{source_prefix}:already_on_landing"
            ends[end] = "already_on_landing"
            continue
        if end == "a":
            conn.z0_before_rule = current
            conn.z0 = target
            if spec.get("x") is not None and spec.get("y") is not None:
                conn.x0, conn.y0 = float(spec["x"]), float(spec["y"])
        else:
            conn.z1_before_rule = current
            conn.z1 = target
            if spec.get("x") is not None and spec.get("y") is not None:
                conn.x1, conn.y1 = float(spec["x"]), float(spec["y"])
        conn.end_sources[end] = f"{source_prefix}:{rule}" if rule else source_prefix
        ends[end] = "moved"
        moved += 1
    return {"status": "applied", "moved": moved, "ends": ends}


def apply_junction_rule(tanks, connectors, rule: str | None, log=None) -> int:
    """Seat walkway ends on the bridge-head roof only when the rule is named.

    The same decision as a corrections file: the landing sector is the target,
    a slope change above 4° is refused, and an end already within 15 cm is not
    moved. Ends a corrections file already decided are left alone. The measured
    deck stays on ``z_before_rule`` when an end does move. Sphere and platform
    bridges are not seated. The default rule is off.
    """
    if rule != "seat_to_measured_roof":
        return 0
    moved = 0
    for conn in connectors:
        if getattr(conn, "kind", "") != "walkway":
            continue
        if getattr(conn, "end_sources", None) is None:
            conn.end_sources = {}
        proposals = {}
        for end in ("a", "b"):
            prior = conn.end_sources.get(end) or ""
            if prior not in ("", "measured"):
                continue
            tank_name = conn.tank_a if end == "a" else conn.tank_b
            tank = next(
                (item for item in tanks if item.name == tank_name or getattr(item, "export_name", "") == tank_name),
                None,
            )
            if tank is None:
                continue
            platform = ((getattr(tank, "accessories", None) or {}).get("roof") or {}).get("platform")
            if platform:
                continue
            target = landing_seat_z(conn, end, tanks)
            if target is None:
                continue
            proposals[end] = (float(target), "seat_to_measured_roof")
        if not proposals:
            continue
        result = apply_seat_ends(conn, proposals, "rule")
        moved += int(result["moved"])
        if log is None:
            continue
        if result["status"] == "conflict":
            log(
                f"  {getattr(conn, 'name', '')}：桥端规则冲突，不贴屋面。"
                f"实测坡度 {result['slope_before_deg']:.1f}°，贴合后 {result['slope_after_deg']:.1f}°"
            )
            continue
        for end, outcome in result["ends"].items():
            if outcome == "already_on_landing":
                log(f"  {getattr(conn, 'name', '')} 端 {end}：桥头屋面与实测桥面相差不超过 15 cm，桥面未改")
            elif outcome == "moved":
                log(f"  {getattr(conn, 'name', '')} 端 {end}：已贴到桥头实测屋面，原桥面高程保留")
    return moved


def classify_end_fit(line_z, local_z, roof_z) -> str:
    """Separate a bad straight-line fit from a deck the mesh really leaves above the plate.

    ``within_0.15_not_a_pass`` only means the two measured surfaces are close.
    It is not a completed joint.
    """
    if local_z is not None and line_z is not None and abs(local_z - line_z) > 0.25:
        return "local_conflict"
    if roof_z is None:
        return "no_roof"
    used = local_z if local_z is not None else line_z
    if used is None:
        return "not_split"
    if local_z is not None and line_z is not None and abs(local_z - line_z) > 0.08:
        if abs(local_z - roof_z) + 0.05 < abs(line_z - roof_z):
            return "line_bias"
    if abs(used - roof_z) <= 0.15:
        return "within_0.15_not_a_pass"
    return "source_step"


def _has_roof(tank) -> bool:
    return all(hasattr(tank, name) for name in ("z_base", "wall_height", "total_height", "roof_type", "radius"))


def _profile_z_at(tank, dist: float) -> float | None:
    profile = getattr(tank, "roof_profile_z", None) or []
    if not profile or tank.radius <= 0:
        return None
    u = min(max(dist, 0.0) / float(tank.radius), 0.999)
    index = min(int(u * len(profile)), len(profile) - 1)
    if profile[index] is not None:
        return float(profile[index])
    for step in range(1, len(profile)):
        for neighbor in (index - step, index + step):
            if 0 <= neighbor < len(profile) and profile[neighbor] is not None:
                return float(profile[neighbor])
    return None


def _roof_z_at(tank, dist: float) -> float:
    """Roof surface height at a horizontal distance from the tank centre."""
    eave = float(tank.z_base) + float(tank.wall_height)
    crown = float(tank.z_base) + float(tank.total_height)
    frac = min(max(dist, 0.0) / max(float(tank.radius), 1e-6), 1.0)
    if tank.roof_type == "cone":
        return crown - (crown - eave) * frac
    if tank.roof_type == "dome":
        return eave + (crown - eave) * math.sqrt(max(0.0, 1.0 - frac * frac))
    return eave


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
    level = platform.get("z")
    if dist > radius + 0.05 and dist > 1e-6:
        nx = tank.center_x + vx / dist * radius
        ny = tank.center_y + vy / dist * radius
        if math.hypot(nx - other[0], ny - other[1]) >= 0.4:
            nz = float(level if level is not None else (conn.z0 if end == "a" else conn.z1))
            if end == "a":
                conn.x0, conn.y0, conn.z0 = nx, ny, nz
            else:
                conn.x1, conn.y1, conn.z1 = nx, ny, nz
            return
    # Already on the disk: use the measured walking surface when it is close
    # to the deck. A larger step is left as a recorded miss, not filled in.
    if level is None or dist > radius + 0.30:
        return
    current = conn.z0 if end == "a" else conn.z1
    if abs(current - float(level)) > 0.25:
        return
    if end == "a":
        conn.z0_before_platform = current
        conn.z0 = float(level)
    else:
        conn.z1_before_platform = current
        conn.z1 = float(level)


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


_DETAIL_KEYS = ("tread", "flight", "rail", "handrail", "girder", "stringer", "leg_", "opening", "brace", "post")


def write_seat_report(connectors, path) -> None:
    """One row per walkway end so a connector ID can be checked without reading the mesh."""
    import json
    from pathlib import Path

    rows = []
    for conn in connectors:
        if getattr(conn, "kind", "") != "walkway":
            continue
        run = math.hypot(conn.x1 - conn.x0, conn.y1 - conn.y0)
        slope = math.degrees(math.atan2(conn.z1 - conn.z0, run)) if run > 0 else 0.0
        rows.append({
            "id": conn.name,
            "export_name": getattr(conn, "export_name", "") or "",
            "tank_a": conn.tank_a,
            "tank_b": conn.tank_b,
            "z0_m": round(float(conn.z0), 3),
            "z1_m": round(float(conn.z1), 3),
            "slope_built_deg": round(slope, 2),
            "seat_conflict": getattr(conn, "seat_conflict", None),
            "ends": list(getattr(conn, "junctions", None) or []),
        })
    document = {
        "junction_rule_default": "none",
        "seat_slope_limit_deg": 4.0,
        "note": (
            "默认不贴屋面。seat_to_measured_roof 与校正文件使用同一判断："
            "目标是桥头扇区屋面，不是整圈径向中位。"
            "贴合会使实测桥坡变化超过 4° 时拒绝，桥面保持测量值，并在 seat_conflict 中给出贴合后的预览。"
            "桥头屋面与桥面相差不超过 15 cm 时不移动；这 15 cm 不是连接合格。"
            "source_mesh 为 false 才表示端点被校正移过。"
        ),
        "connectors": rows,
    }
    Path(path).write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")


def detail_floors_from_counts(face_counts: list[dict], limit: int = 2000) -> list[dict]:
    """Per exported model: total polygons and the floor of the parts that stay."""
    groups: dict[str, list] = {}
    for row in face_counts:
        name = str(row.get("object") or "")
        model = name.split("_", 1)[0] if name else ""
        groups.setdefault(model, []).append(row)
    floors = []
    for model, rows in groups.items():
        floor = detail_floor(rows)
        total = sum(int(item.get("polygons") or 0) for item in rows)
        floors.append({
            "model": model,
            "polygons": total,
            "detail_floor_polygons": floor["polygons"],
            "detail_floor_parts": floor["parts"],
            "detail_floor_above_2000": floor["above_2000"],
            "detail_floor_reason": floor["reason"],
            "limit": int(limit) if limit else 2000,
            "over_budget": bool(limit) and total > int(limit),
        })
    return floors


def annotate_detail_floors(path) -> list[dict]:
    """Add ``detail_floors`` to a Blender report. The mesh files are not rewritten."""
    import json
    from pathlib import Path

    report_path = Path(path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    limit = report.get("max_faces") or 2000
    floors = detail_floors_from_counts(report.get("face_counts") or [], int(limit))
    report["detail_floors"] = floors
    report["detail_floor_note"] = (
        "detail_floor_polygons counts treads, rails, girders, stringers, legs, openings, braces and posts. "
        "Those parts are kept. A model whose floor is already above 2000 cannot reach the budget "
        "without deleting them or changing a round tube into a square bar."
    )
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return floors


def detail_floor(face_counts: list[dict]) -> dict:
    """Polygons in parts that stay when treads, rails, legs, openings and round tubes are kept.

    The shell can be reduced separately. This sum is the floor under that choice.
    """
    kept = []
    for row in face_counts:
        name = str(row.get("object") or "")
        if any(key in name for key in _DETAIL_KEYS):
            kept.append({"object": name, "polygons": int(row.get("polygons") or 0)})
    total = sum(item["polygons"] for item in kept)
    return {
        "polygons": total,
        "parts": len(kept),
        "above_2000": total > 2000,
        "reason": (
            "treads, rails, legs, openings and round tubes are kept; "
            "their polygons already exceed 2000"
            if total > 2000 else
            "kept detail is within 2000 before the shell is added"
        ),
    }


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
