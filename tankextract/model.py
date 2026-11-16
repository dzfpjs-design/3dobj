"""Measured tank, in absolute SRS coordinates (metres, Z up)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Tank:
    block: str
    srs: str
    origin: tuple[float, float, float]
    center_x: float
    center_y: float
    z_base: float
    z_ground: float
    radius: float
    wall_height: float
    total_height: float
    roof_type: str
    roof_rise: float
    rms: float
    inlier_ratio: float
    coverage_deg: float
    top_rim_width: float | None = None
    wind_girders: list[dict] = field(default_factory=list)
    base_ring: dict | None = None
    spiral_stair: dict | None = None
    handrail_height: float | None = None
    # Spherical tank: {"center_z", "radius", "legs": [azimuth deg], "leg_rho", "leg_radius"}.
    sphere: dict | None = None
    name: str = ""

    @property
    def center_local(self) -> tuple[float, float]:
        return (self.center_x - self.origin[0], self.center_y - self.origin[1])

    @property
    def z_base_local(self) -> float:
        return self.z_base - self.origin[2]

    def to_dict(self) -> dict:
        stair = self.spiral_stair or {}
        ring = self.base_ring or {}
        return {
            "id": self.name,
            "block": self.block,
            "srs": self.srs,
            "origin_x": self.origin[0],
            "origin_y": self.origin[1],
            "origin_z": self.origin[2],
            "X": self.center_x,
            "Y": self.center_y,
            "Z_base": self.z_base,
            "Z_ground": self.z_ground,
            "radius_m": self.radius,
            "wall_height_m": self.wall_height,
            "total_height_m": self.total_height,
            "roof_type": self.roof_type,
            "roof_rise_m": self.roof_rise,
            "rms_m": self.rms,
            "inlier_ratio": self.inlier_ratio,
            "coverage_deg": self.coverage_deg,
            "top_rim": self.top_rim_width is not None,
            "top_rim_width_m": self.top_rim_width if self.top_rim_width is not None else "",
            "wind_girder_count": len(self.wind_girders),
            "wind_girder_z": ";".join(f"{item['z']:.3f}" for item in self.wind_girders),
            "base_ring": bool(self.base_ring),
            "base_ring_protrusion_m": ring.get("protrusion", ""),
            "spiral_stair": bool(self.spiral_stair),
            "stair_outer_radius_m": stair.get("outer_radius", ""),
            "stair_width_m": stair.get("width", ""),
            "stair_turns": stair.get("turns", ""),
            "handrail_height_m": self.handrail_height if self.handrail_height is not None else "",
            "sphere_center_z": (self.sphere or {}).get("center_z", ""),
            "sphere_leg_count": len((self.sphere or {}).get("legs", [])) if self.sphere else "",
        }
