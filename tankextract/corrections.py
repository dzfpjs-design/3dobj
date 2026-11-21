"""Optional, saved corrections addressed by tank or connector ID.

A value is applied only when its record says ``source`` is ``manual`` or
``assumed`` and the number is present. An empty template does not create a
rail, a column, or a moved bridge end.
"""

from __future__ import annotations

import json
from pathlib import Path


APPLIED = {"manual", "assumed"}
_SUPPORT_KEYS = ("x", "y", "z_bottom", "z_top", "length", "width")


def load_corrections(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("--corrections 文件必须是 JSON 对象")
    return data


def _source(record: dict) -> str:
    return str(record.get("source") or "").strip().lower()


def _number(record: dict, key: str):
    if key not in record or record[key] is None:
        return None
    return float(record[key])


def apply_corrections(tanks, connectors, data: dict | None, log=print) -> None:
    """Apply an explicit corrections file. Missing numbers stay missing."""
    if not data:
        return
    tanks_by = {}
    for tank in tanks:
        tanks_by[tank.name] = tank
        export = getattr(tank, "export_name", "") or ""
        if export:
            tanks_by[export] = tank
    for key, record in (data.get("tanks") or {}).items():
        if not isinstance(record, dict):
            continue
        source = _source(record)
        tank = tanks_by.get(key)
        if tank is None:
            log(f"  校正 {key}：没有这个罐，已跳过")
            continue
        if source not in APPLIED:
            log(f"  校正 {key}：source 为 {source or '空'}，不建护栏")
            continue
        height = _number(record, "handrail_height_m")
        if height is None:
            log(f"  校正 {key}：没有 handrail_height_m，不建护栏")
            continue
        tank.handrail_height = height
        tank.handrail_source = source
        log(f"  校正 {tank.name}：按 {source} 设置护栏 {height:.3f} m")

    links_by = {}
    for conn in connectors:
        links_by[conn.name] = conn
        export = getattr(conn, "export_name", "") or ""
        if export:
            links_by[export] = conn
    for key, record in (data.get("connectors") or {}).items():
        if not isinstance(record, dict):
            continue
        source = _source(record)
        conn = links_by.get(key)
        if conn is None:
            log(f"  校正 {key}：没有这条连桥，已跳过")
            continue
        if source not in APPLIED:
            log(f"  校正 {key}：source 为 {source or '空'}，不改连桥")
            continue
        ends = record.get("ends") or {}
        for end, spec in ends.items():
            if end not in ("a", "b") or not isinstance(spec, dict):
                continue
            rule = str(spec.get("rule") or "")
            if rule == "seat_to_measured_roof" and spec.get("z") is None:
                from tankextract.deliverable import _has_roof, _profile_z_at

                tank_name = conn.tank_a if end == "a" else conn.tank_b
                tank = next((item for item in tanks if item.name == tank_name or getattr(item, "export_name", "") == tank_name), None)
                if tank is None or not _has_roof(tank):
                    log(f"  校正 {conn.name} 端 {end}：没有屋面剖面，不贴屋面")
                    continue
                x = conn.x0 if end == "a" else conn.x1
                y = conn.y0 if end == "a" else conn.y1
                import math
                dist = math.hypot(x - tank.center_x, y - tank.center_y)
                roof = _profile_z_at(tank, dist)
                if roof is None:
                    log(f"  校正 {conn.name} 端 {end}：屋面剖面为空，不贴屋面")
                    continue
                spec = dict(spec)
                spec["z"] = roof
            if spec.get("z") is None:
                log(f"  校正 {conn.name} 端 {end}：没有 z，也没有 seat_to_measured_roof，不移动")
                continue
            current = conn.z0 if end == "a" else conn.z1
            if end == "a":
                conn.z0_before_rule = current
                conn.z0 = float(spec["z"])
            else:
                conn.z1_before_rule = current
                conn.z1 = float(spec["z"])
            if spec.get("x") is not None and spec.get("y") is not None:
                if end == "a":
                    conn.x0, conn.y0 = float(spec["x"]), float(spec["y"])
                else:
                    conn.x1, conn.y1 = float(spec["x"]), float(spec["y"])
            conn.end_sources[end] = f"{source}:{rule}" if rule else source
            log(f"  校正 {conn.name} 端 {end}：按 {source} 设置端点高程")
        height = _number(record, "handrail_height_m")
        if height is not None:
            conn.handrail_height = height
            conn.rail_source = source
            log(f"  校正 {conn.name}：按 {source} 设置走道护栏 {height:.3f} m")
        for support in record.get("supports") or []:
            if not isinstance(support, dict) or any(support.get(name) is None for name in _SUPPORT_KEYS):
                log(f"  校正 {conn.name}：支座缺 x/y/z_bottom/z_top/length/width，不建")
                continue
            built = {name: float(support[name]) for name in _SUPPORT_KEYS}
            built["axis_deg"] = float(support.get("axis_deg") or 0.0)
            built["source"] = source
            conn.supports.append(built)
            log(f"  校正 {conn.name}：按 {source} 增加一支座")


def write_open_corrections(path: Path, tanks, connectors, rail_reference_m: float | None) -> None:
    """IDs that were not given a part, with null sizes. Nothing here is built."""
    tank_rows = {}
    for tank in tanks:
        if tank.handrail_height is not None or getattr(tank, "sphere", None):
            continue
        tank_rows[tank.name] = {
            "export_name": getattr(tank, "export_name", "") or "",
            "handrail_height_m": None,
            "source": "unmeasured",
            "note": "没有测到绕罐栏杆，不生成。这只说明当前网格没有表达，不是现场没有。填写 handrail_height_m 并把 source 改成 manual 或 assumed 后才会生成。",
        }
    link_rows = {}
    for conn in connectors:
        ends = {}
        for row in getattr(conn, "junctions", None) or []:
            dz = row.get("dz_measured_m")
            inside = row.get("inside_shell_m")
            if dz is None and inside is None:
                continue
            if (dz is not None and abs(dz) > 0.15) or (inside is not None and abs(inside) > 0.15) or row.get("fit_class") == "source_step":
                ends[row["end"]] = {
                    "z": None,
                    "rule": None,
                    "measured_deck_z_m": row.get("deck_z_m"),
                    "deck_z_line_m": row.get("deck_z_line_m"),
                    "deck_z_local_m": row.get("deck_z_local_m"),
                    "measured_roof_z_m": row.get("roof_z_measured_m"),
                    "dz_measured_m": dz,
                    "fit_class": row.get("fit_class"),
                    "inside_shell_m": inside,
                    "landing": row.get("landing"),
                    "end_stop": row.get("end_stop"),
                    "rule_hint": "要贴到已测屋面，把 rule 写成 seat_to_measured_roof 并设 source 为 manual。不要手填已有的 z。默认不贴。",
                }
        supports = None
        note = ""
        if not conn.supports and conn.length > 6.0:
            supports = None
            note = "网格里没有通到地面的柱。要加柱必须给出 x、y、z_bottom、z_top、length、width，并把 source 改成 manual 或 assumed。"
        if conn.handrail_height is None or ends or note:
            link_rows[conn.name] = {
                "export_name": getattr(conn, "export_name", "") or "",
                "tank_a": conn.tank_a,
                "tank_b": conn.tank_b,
                "source": "unmeasured",
                "handrail_height_m": None,
                "measured_handrail_height_m": conn.handrail_height,
                "site_median_rail_m_not_applied": rail_reference_m,
                "ends": ends,
                "supports": supports,
                "note": note,
            }
    document = {
        "applied": False,
        "rule": "本文件不参与建模。复制需要的条目到另一个 JSON，写上数值，并把 source 设为 manual 或 assumed，再用 --corrections 读入。",
        "tanks": tank_rows,
        "connectors": link_rows,
    }
    path.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")
