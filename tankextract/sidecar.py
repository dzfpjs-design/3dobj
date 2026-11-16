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
    "width_m", "depth_m", "handrail_height_m", "support_count", "supports", "fill", "srs",
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
                "support_count": len(conn.supports),
                "supports": json.dumps([{k: round(v, 3) for k, v in sp.items()} for sp in conn.supports]),
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
