"""GeoJSON, CSV and .prj sidecars. Coordinates are absolute SRS metres."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from tankextract.connectors import Connector
from tankextract.model import Tank

_CSV_FIELDS = [
    "id",
    "block",
    "srs",
    "X",
    "Y",
    "Z_base",
    "Z_ground",
    "radius_m",
    "wall_height_m",
    "total_height_m",
    "roof_type",
    "roof_rise_m",
    "rms_m",
    "inlier_ratio",
    "coverage_deg",
    "top_rim",
    "top_rim_width_m",
    "wind_girder_count",
    "wind_girder_z",
    "base_ring",
    "base_ring_protrusion_m",
    "spiral_stair",
    "stair_outer_radius_m",
    "stair_width_m",
    "stair_turns",
    "handrail_height_m",
    "sphere_center_z",
    "sphere_leg_count",
    "sphere_leg_diameter_m",
    "sphere_leg_rho_m",
    "sphere_leg_height_m",
    "sphere_leg_azimuth_deg",
    "sphere_bracing",
    "Z_base_measured",
    "ground_gap_m",
    "ground_gap_before_m",
    "foundation",
    "foundation_top_z",
    "foundation_height_m",
    "foundation_radius_m",
    "sphere_leg_diameter_v5_m",
    "sphere_pedestal_height_m",
    "sphere_pedestal_size_m",
    "sphere_brace_z0",
    "sphere_brace_z1",
    "roof_platform_z",
    "roof_platform_radius_m",
    "roof_platform_rail_m",
    "roof_walkways",
    "wall_stair",
    "wall_stair_az_bottom_deg",
    "wall_stair_az_top_deg",
    "wall_stair_z_bottom",
    "wall_stair_z_top",
    "wall_stair_width_m",
    "wall_stair_slope_deg",
    "wall_stair_sweep_deg",
    "wall_stair_extended",
    "wall_pipe_count",
    "wall_pipes",
    "origin_x",
    "origin_y",
    "origin_z",
]


def write_sidecars(tanks: list[Tank], output: Path, coordinate_mode: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _write_geojson(tanks, output / "tanks.geojson", coordinate_mode)
    _write_csv(tanks, output / "tanks.csv")
    # One .prj per distinct SRS. The common case is a single EPSG code.
    by_srs: dict[str, tuple[float, float, float]] = {}
    for tank in tanks:
        by_srs.setdefault(tank.srs, tank.origin)
    if not by_srs and tanks:
        by_srs[tanks[0].srs] = tanks[0].origin
    for srs, origin in by_srs.items():
        slug = _srs_slug(srs)
        (output / f"tanks_{slug}.prj").write_text(_prj_text(srs), encoding="utf-8")
        (output / "tanks.prj").write_text(_prj_text(srs), encoding="utf-8")
        (output / "srs_origin.json").write_text(
            json.dumps(
                {
                    "srs": srs,
                    "origin": list(origin),
                    "coordinate_mode": coordinate_mode,
                    "note": "FBX 顶点坐标系见 coordinate_mode；本文件与 geojson/csv 的 X/Y/Z 始终是绝对坐标。",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def _write_geojson(tanks: list[Tank], path: Path, coordinate_mode: str) -> None:
    features = []
    srs = tanks[0].srs if tanks else ""
    for tank in tanks:
        props = tank.to_dict()
        props["coordinate_mode"] = coordinate_mode
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [tank.center_x, tank.center_y, tank.z_base],
                },
                "properties": props,
            }
        )
    document = {
        "type": "FeatureCollection",
        "name": "tanks",
        "crs": {"type": "name", "properties": {"name": srs}},
        "features": features,
    }
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(tanks: list[Tank], path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for tank in tanks:
            writer.writerow(tank.to_dict())


_CONNECTOR_FIELDS = [
    "id", "kind", "tank_a", "tank_b", "x0", "y0", "z0", "x1", "y1", "z1", "length_m", "slope_deg",
    "width_m", "depth_m", "handrail_height_m", "handrail_source", "rail_sides_seen", "support_count", "supports", "fill", "srs",
]


def write_connectors(connectors: list[Connector], output: Path, coordinate_mode: str) -> None:
    """connectors.csv / connectors.geojson (absolute SRS coordinates)."""
    import math

    output.mkdir(parents=True, exist_ok=True)
    with (output / "connectors.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CONNECTOR_FIELDS)
        writer.writeheader()
        for conn in connectors:
            run = conn.length
            writer.writerow({
                "id": conn.name, "kind": conn.kind, "tank_a": conn.tank_a, "tank_b": conn.tank_b,
                "x0": f"{conn.x0:.3f}", "y0": f"{conn.y0:.3f}", "z0": f"{conn.z0:.3f}",
                "x1": f"{conn.x1:.3f}", "y1": f"{conn.y1:.3f}", "z1": f"{conn.z1:.3f}",
                "length_m": f"{run:.3f}",
                "slope_deg": f"{math.degrees(math.atan2(conn.z1 - conn.z0, run)) if run > 0 else 0.0:.2f}",
                "width_m": f"{conn.width:.3f}", "depth_m": f"{conn.depth:.3f}",
                "handrail_height_m": "" if conn.handrail_height is None else f"{conn.handrail_height:.3f}",
                "handrail_source": conn.rail_source, "rail_sides_seen": "".join(conn.rail_sides),
                "support_count": len(conn.supports),
                "supports": json.dumps([{k: round(v, 3) if isinstance(v, float) else v for k, v in sp.items()} for sp in conn.supports]),
                "fill": f"{conn.fill:.2f}", "srs": conn.srs,
            })
    features = []
    for conn in connectors:
        props = {k: v for k, v in conn.to_dict().items() if k not in ("supports", "origin")}
        props["supports"] = conn.supports
        props["coordinate_mode"] = coordinate_mode
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[conn.x0, conn.y0, conn.z0], [conn.x1, conn.y1, conn.z1]]},
            "properties": props,
        })
        for index, sp in enumerate(conn.supports, start=1):
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [sp["x"], sp["y"], sp["z_bottom"]]},
                "properties": {"id": f"{conn.name}_Support_{index:02d}", "connector": conn.name, **sp},
            })
    srs = connectors[0].srs if connectors else ""
    document = {
        "type": "FeatureCollection", "name": "connectors",
        "crs": {"type": "name", "properties": {"name": srs}}, "features": features,
    }
    (output / "connectors.geojson").write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def _srs_slug(srs: str) -> str:
    token = "".join(ch if ch.isalnum() else "_" for ch in srs.strip())
    return token.strip("_") or "srs"


def _prj_text(srs: str) -> str:
    wkt = _cgcs2000_3deg_wkt(srs)
    if wkt:
        return wkt + "\n"
    return srs.strip() + "\n"


def _cgcs2000_3deg_wkt(srs: str) -> str | None:
    token = srs.strip().upper().replace(" ", "")
    if not token.startswith("EPSG:"):
        return None
    try:
        code = int(token.split(":", 1)[1])
    except ValueError:
        return None
    # EPSG:4534 is CM 75E, EPSG:4554 is CM 135E, step 3 degrees. 4548 = 117E.
    if not 4534 <= code <= 4554:
        return None
    central = 75 + (code - 4534) * 3
    return (
        f'PROJCS["CGCS2000 / 3-degree Gauss-Kruger CM {central}E",'
        'GEOGCS["China Geodetic Coordinate System 2000",'
        'DATUM["China_2000",'
        'SPHEROID["CGCS2000",6378137,298.257222101]],'
        'PRIMEM["Greenwich",0],'
        'UNIT["degree",0.0174532925199433]],'
        'PROJECTION["Transverse_Mercator"],'
        'PARAMETER["latitude_of_origin",0],'
        f'PARAMETER["central_meridian",{central}],'
        'PARAMETER["scale_factor",1],'
        'PARAMETER["false_easting",500000],'
        'PARAMETER["false_northing",0],'
        'UNIT["metre",1],'
        f'AUTHORITY["EPSG","{code}"]]'
    )


_STRUCTURE_FIELDS = ["id", "kind", "parent", "x", "y", "z_ground", "length_m", "width_m", "height_m", "axis_deg",
                     "ground_gap_m", "ground_gap_before_m", "part_objects", "dims", "srs"]


def _gap(v):
    return "" if v is None else f"{v:.3f}"


def write_structures(structures, tanks, output: Path, coordinate_mode: str) -> None:
    """structures.csv / structures.geojson: stair towers, pipe racks, pipe runs (absolute
    SRS coordinates) plus every tank-attached accessory (wall pipes, wall stairs, roof
    platforms and walkways, sphere pedestals)."""
    output.mkdir(parents=True, exist_ok=True)

    def rnd(v):
        if isinstance(v, float):
            return round(v, 3)
        if isinstance(v, list):
            return [rnd(x) for x in v]
        if isinstance(v, dict):
            return {k: rnd(x) for k, x in v.items()}
        return v

    rows, features = [], []
    for st in structures:
        d = st.dims
        names = sorted({p["name"] for p in st.parts})
        height = (d.get("z_top") or (max(d.get("tiers") or [0]) if d.get("tiers") else d.get("z"))) or 0.0
        rows.append({
            "id": st.name, "kind": st.kind, "parent": st.parent,
            "x": f"{st.anchor[0]:.3f}", "y": f"{st.anchor[1]:.3f}", "z_ground": f"{st.anchor[2]:.3f}",
            "length_m": f"{d.get('length', 0):.3f}", "width_m": f"{d.get('width', d.get('diameter', 0)):.3f}",
            "height_m": f"{(height - st.anchor[2]) if height else 0:.3f}", "axis_deg": f"{d.get('axis_deg', 0):.2f}",
            "ground_gap_m": _gap(d.get("ground_gap_m")), "ground_gap_before_m": _gap(d.get("ground_gap_before_m")),
            "part_objects": len(names), "dims": json.dumps(rnd(d), ensure_ascii=False), "srs": st.srs,
        })
        features.append({"type": "Feature", "geometry": st.geometry,
                         "properties": {"id": st.name, "kind": st.kind, "parent": st.parent, **rnd(d),
                                        "coordinate_mode": coordinate_mode}})
    for tank in tanks:
        acc = getattr(tank, "accessories", None) or {}
        for i, p in enumerate(acc.get("wall_pipes", []), start=1):
            rows.append({"id": f"{tank.name}_pipe_{i:02d}", "kind": "wall_pipe", "parent": tank.name,
                         "x": f"{p['x']:.3f}", "y": f"{p['y']:.3f}", "z_ground": f"{p['z0']:.3f}",
                         "length_m": f"{p['z1'] - p['z0']:.3f}", "width_m": f"{p['diameter']:.3f}",
                         "height_m": f"{p['z1'] - p['z0']:.3f}", "axis_deg": "90", "part_objects": 1,
                         "dims": json.dumps(rnd(p)), "srs": tank.srs})
            features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[p["x"], p["y"], p["z0"]], [p["x"], p["y"], p["z1"]]]},
                             "properties": {"id": f"{tank.name}_pipe_{i:02d}", "kind": "wall_pipe", "parent": tank.name, **rnd(p)}})
        st = acc.get("wall_stair")
        if st:
            import math
            pts = []
            sw = st["sweep_deg"] * (1 if st["direction"] == "ccw" else -1)
            rm = 0.5 * (st["inner_radius"] + st["outer_radius"])
            for k in range(13):
                a = math.radians(st["az_bottom_deg"] + sw * k / 12)
                pts.append([tank.center_x + rm * math.cos(a), tank.center_y + rm * math.sin(a), st["z_bottom"] + (st["z_top"] - st["z_bottom"]) * k / 12])
            rows.append({"id": f"{tank.name}_stair", "kind": "wall_stair", "parent": tank.name,
                         "x": f"{pts[0][0]:.3f}", "y": f"{pts[0][1]:.3f}", "z_ground": f"{st['z_bottom']:.3f}",
                         "length_m": f"{math.radians(abs(sw)) * rm:.3f}", "width_m": f"{st['width']:.3f}",
                         "height_m": f"{st['z_top'] - st['z_bottom']:.3f}", "axis_deg": "",
                         "ground_gap_m": _gap(st.get("ground_gap_m")), "ground_gap_before_m": _gap(st.get("ground_gap_before_m")),
                         "part_objects": 6,
                         "dims": json.dumps(rnd(st)), "srs": tank.srs})
            features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": pts},
                             "properties": {"id": f"{tank.name}_stair", "kind": "wall_stair", "parent": tank.name, **rnd(st)}})
        roof = acc.get("roof") or {}
        pf = roof.get("platform")
        if pf:
            rows.append({"id": f"{tank.name}_platform", "kind": "roof_platform", "parent": tank.name,
                         "x": f"{tank.center_x:.3f}", "y": f"{tank.center_y:.3f}", "z_ground": f"{pf['z']:.3f}",
                         "length_m": f"{2 * pf['radius']:.3f}", "width_m": f"{2 * pf['radius']:.3f}",
                         "height_m": f"{pf.get('rail_height') or 0:.3f}", "axis_deg": "", "part_objects": 2,
                         "dims": json.dumps(rnd(pf)), "srs": tank.srs})
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [tank.center_x, tank.center_y, pf["z"]]},
                             "properties": {"id": f"{tank.name}_platform", "kind": "roof_platform", "parent": tank.name, **rnd(pf)}})
        for j, w in enumerate(roof.get("walkways", []), start=1):
            import math
            a = math.radians(w["azimuth_deg"])
            nx, ny = -math.sin(a), math.cos(a)
            p0 = [tank.center_x + nx * w["offset"] + math.cos(a) * w["r0"], tank.center_y + ny * w["offset"] + math.sin(a) * w["r0"]]
            p1 = [tank.center_x + nx * w["offset"] + math.cos(a) * w["r1"], tank.center_y + ny * w["offset"] + math.sin(a) * w["r1"]]
            rows.append({"id": f"{tank.name}_roof_walkway_{j:02d}", "kind": "roof_walkway", "parent": tank.name,
                         "x": f"{p0[0]:.3f}", "y": f"{p0[1]:.3f}", "z_ground": "", "length_m": f"{w['r1'] - w['r0']:.3f}",
                         "width_m": f"{w['width']:.3f}", "height_m": f"{w['rail_height']:.3f}", "axis_deg": f"{w['azimuth_deg']:.2f}",
                         "part_objects": 3, "dims": json.dumps(rnd(w)), "srs": tank.srs})
            features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [p0, p1]},
                             "properties": {"id": f"{tank.name}_roof_walkway_{j:02d}", "kind": "roof_walkway", "parent": tank.name, **rnd(w)}})
        sup = acc.get("sphere_supports") or {}
        if sup.get("pedestal"):
            ped = sup["pedestal"]
            rows.append({"id": f"{tank.name}_pedestals", "kind": "sphere_pedestals", "parent": tank.name,
                         "x": f"{tank.center_x:.3f}", "y": f"{tank.center_y:.3f}", "z_ground": f"{tank.z_base:.3f}",
                         "length_m": f"{ped['size_radial']:.3f}", "width_m": f"{ped['size_tangential']:.3f}",
                         "height_m": f"{ped['height']:.3f}", "axis_deg": "",
                         "ground_gap_m": _gap(0.0 if ped.get("grounds") else None),
                         "ground_gap_before_m": _gap(ped.get("ground_gap_before_m")),
                         "part_objects": ped.get("count", 0),
                         "dims": json.dumps(rnd(sup)), "srs": tank.srs})
        fd = acc.get("foundation")
        if fd:
            rows.append({"id": f"{tank.name}_foundation", "kind": "foundation", "parent": tank.name,
                         "x": f"{tank.center_x:.3f}", "y": f"{tank.center_y:.3f}", "z_ground": f"{fd['z0']:.3f}",
                         "length_m": f"{2 * fd['radius']:.3f}", "width_m": f"{2 * fd['radius']:.3f}",
                         "height_m": f"{fd['height']:.3f}", "axis_deg": "", "ground_gap_m": _gap(0.0), "ground_gap_before_m": "",
                         "part_objects": 1, "dims": json.dumps(rnd(fd)), "srs": tank.srs})
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [tank.center_x, tank.center_y, fd["z0"]]},
                             "properties": {"id": f"{tank.name}_foundation", "kind": "foundation", "parent": tank.name, **rnd(fd)}})
    with (output / "structures.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_STRUCTURE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    srs = tanks[0].srs if tanks else ""
    document = {"type": "FeatureCollection", "name": "structures",
                "crs": {"type": "name", "properties": {"name": srs}}, "features": features}
    (output / "structures.geojson").write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")


_GROUND_FIELDS = ["object", "kind", "parent", "stands_on", "ground_z", "support_top_z", "bottom_before_z",
                  "gap_to_ground_before_m", "gap_before_m",
                  "bottom_after_z", "gap_after_m", "note"]


def write_ground_report(rows, output: Path) -> None:
    """ground_report.csv: every object that stands on something, its measured ground / support
    top, and its bottom before and after snapping (absolute Z)."""
    output.mkdir(parents=True, exist_ok=True)
    with (output / "ground_report.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_GROUND_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: (f"{v:.3f}" if isinstance(v, float) else ("" if v is None else v)) for k, v in r.items()
                             if k in _GROUND_FIELDS})
