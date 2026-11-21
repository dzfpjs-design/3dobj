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
    # Measured ancillary structures attached to this tank (wall stair, wall pipes, roof
    # platform / walkways, sphere pedestals + bracing) and their primitive parts.
    accessories: dict = field(default_factory=dict)
    extra_parts: list = field(default_factory=list)
    # Tree-crown check (treefilter): geometry cues measured in pass 2, texture colour,
    # and the verdict {"tree": bool, "reason": str}.
    tree_check: dict = field(default_factory=dict)

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
            "export_name": getattr(self, "export_name", "") or "",
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
            "sphere_leg_diameter_m": (self.sphere or {}).get("leg_diameter", "") if self.sphere else "",
            "sphere_leg_rho_m": (self.sphere or {}).get("leg_rho", "") if self.sphere else "",
            "sphere_leg_height_m": (
                self.sphere["leg_top"] - self.sphere["leg_bottom"]
                if self.sphere and "leg_top" in self.sphere and "leg_bottom" in self.sphere
                else ""
            ),
            "sphere_leg_azimuth_deg": (
                ";".join(f"{a:.1f}" for a in self.sphere.get("legs", [])) if self.sphere else ""
            ),
            "sphere_bracing": (self.sphere or {}).get("bracing", "") if self.sphere else "",
            **_accessory_columns(self),
        }


def _fmt(v, nd=3):
    return "" if v is None else round(float(v), nd)


def _accessory_columns(tank: "Tank") -> dict:
    import json

    acc = tank.accessories or {}
    sup = acc.get("sphere_supports", {})
    ped = sup.get("pedestal") or {}
    roof = acc.get("roof") or {}
    pf = roof.get("platform") or {}
    st = acc.get("wall_stair") or {}
    pipes = acc.get("wall_pipes") or []
    fd = getattr(tank, "foundation", None) or {}
    return {
        "Z_base_measured": _fmt(getattr(tank, "z_base_measured", None)),
        "ground_gap_m": _fmt(getattr(tank, "ground_gap", None)),
        "ground_gap_before_m": _fmt(getattr(tank, "ground_gap_before", None)),
        "foundation": bool(fd),
        "foundation_top_z": _fmt(fd.get("z1")),
        "foundation_height_m": _fmt(fd.get("height")),
        "foundation_radius_m": _fmt(fd.get("radius")),
        "sphere_leg_diameter_v5_m": _fmt((tank.sphere or {}).get("leg_diameter_v5")) if tank.sphere else "",
        "sphere_pedestal_height_m": _fmt(ped.get("height")),
        "sphere_pedestal_size_m": f"{ped['size_radial']:.2f}x{ped['size_tangential']:.2f}" if ped else "",
        "sphere_brace_z0": _fmt(sup.get("brace_z0")),
        "sphere_brace_z1": _fmt(sup.get("brace_z1")),
        "roof_platform_z": _fmt(pf.get("z")),
        "roof_platform_radius_m": _fmt(pf.get("radius")),
        "roof_platform_rail_m": _fmt(pf.get("rail_height")),
        "roof_walkways": json.dumps([{k: round(v, 3) if isinstance(v, float) else v for k, v in w.items()} for w in roof.get("walkways", [])]) if roof.get("walkways") else "",
        "wall_stair": bool(st),
        "wall_stair_az_bottom_deg": _fmt(st.get("az_bottom_deg"), 1),
        "wall_stair_az_top_deg": _fmt(st.get("az_top_deg"), 1),
        "wall_stair_z_bottom": _fmt(st.get("z_bottom")),
        "wall_stair_z_top": _fmt(st.get("z_top")),
        "wall_stair_width_m": _fmt(st.get("width")),
        "wall_stair_slope_deg": _fmt(st.get("slope_deg"), 1),
        "wall_stair_sweep_deg": _fmt(st.get("sweep_deg"), 1),
        "wall_stair_extended": ";".join(st.get("extended", [])) if st else "",
        "wall_pipe_count": len(pipes),
        "wall_pipes": json.dumps([{k: round(v, 3) if isinstance(v, float) else v for k, v in q.items()} for q in pipes]) if pipes else "",
    }
