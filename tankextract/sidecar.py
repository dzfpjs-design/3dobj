"""GeoJSON, CSV and .prj sidecars. Coordinates are absolute SRS metres."""

from __future__ import annotations

import csv
import json
from pathlib import Path

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
