"""Ground contact: every object that stands on the ground must sit on the measured ground
(or on the measured top of its concrete foundation), neither floating nor sunk.

All measurements use the face array F (N, 7: centroid xyz, normal xyz, area) of the block's
finest LOD.  The ground is the floor of the upward-facing faces in a ring just outside an
object's footprint; a tank's foundation is an upward-facing ledge right at the shell's base
edge, higher than that ground, seen round most of the circumference."""

from __future__ import annotations

import math

import numpy as np

TOL = 0.05  # residual allowed between an object's bottom and what it stands on (m)


def _wpct(z, w, q):
    o = np.argsort(z)
    c = np.cumsum(w[o])
    return float(z[o][min(int(np.searchsorted(c, q / 100.0 * c[-1])), len(z) - 1)])


def floor_z(z: np.ndarray, w: np.ndarray | None = None) -> float | None:
    """Robust ground level of a set of upward-face heights: the dense cluster at the low end
    (ignores kerbs, stairs, equipment, pipes standing on the ground).  Area-weighted when the
    face areas are given, so many small faces on a stair do not outvote the ground."""
    z = np.asarray(z, float)
    if len(z) < 3:
        return None
    w = np.ones(len(z)) if w is None else np.asarray(w, float)
    if len(z) < 8 and w.sum() < 2.0:
        return None
    q = _wpct(z, w, 25)
    m = np.abs(z - q) < 0.12
    if m.sum() < 2:
        return q
    return _wpct(z[m], w[m], 50)


def ring_ground(F, x, y, r_in, r_out, z_ref, below=4.0, above=1.2):
    d = np.hypot(F[:, 0] - x, F[:, 1] - y)
    m = (d > r_in) & (d < r_out) & (F[:, 5] > 0.85) & (F[:, 2] > z_ref - below) & (F[:, 2] < z_ref + above)
    return floor_z(F[m, 2], F[m, 6]), int(m.sum())


def rect_ground(F, x, y, length, width, axis_deg, z_ref, pad0=0.4, pad1=2.0, below=4.0, above=1.2):
    """Ground in a frame round a rectangle (stair tower, support), outside its own footprint."""
    a = math.radians(axis_deg)
    u = np.array([math.cos(a), math.sin(a)])
    v = np.array([-u[1], u[0]])
    rel = F[:, :2] - np.array([x, y])
    su, sv = np.abs(rel @ u), np.abs(rel @ v)
    inside0 = (su < length / 2 + pad0) & (sv < width / 2 + pad0)
    inside1 = (su < length / 2 + pad1) & (sv < width / 2 + pad1)
    m = inside1 & ~inside0 & (F[:, 5] > 0.85) & (F[:, 2] > z_ref - below) & (F[:, 2] < z_ref + above)
    return floor_z(F[m, 2], F[m, 6]), int(m.sum())


def tank_footing(F, cx, cy, R, z_base):
    """Measured ground round a vertical tank and its foundation ledge, if any.

    Returns {"ground", "ground_faces", "foundation": {"top", "protrusion", "coverage"} | None}."""
    d = np.hypot(F[:, 0] - cx, F[:, 1] - cy)
    az = np.degrees(np.arctan2(F[:, 1] - cy, F[:, 0] - cx)) % 360
    near = (F[:, 5] > 0.85) & (F[:, 2] > z_base - 4.0) & (F[:, 2] < z_base + 1.2)
    m0 = near & (d > R + 0.6) & (d < R + 3.0)
    g0 = floor_z(F[m0, 2], F[m0, 6])
    if g0 is None:
        m0 = near & (d > R + 0.6) & (d < R + 8.0)
        g0 = floor_z(F[m0, 2], F[m0, 6])
    found = None
    vert = np.abs(F[:, 5]) < 0.3
    if g0 is not None:
        ring = near & (d > R - 0.25) & (d < R + 0.35) & (F[:, 2] > g0 + 0.1)
        if ring.sum() >= 10:
            edges = np.arange(g0 + 0.1, max(z_base + 1.2, g0 + 0.3), 0.04)
            h, e = np.histogram(F[ring, 2], bins=edges, weights=F[ring, 6])
            hs = np.convolve(h, [1, 1, 1], "same")
            # candidate ledges, lowest first: a foundation is the lowest ledge whose side
            # reaches the ground (a steel base ring on the shell does not)
            cands = [i for i in range(len(hs)) if hs[i] > 0.25 * hs.max()
                     and hs[i] >= hs[max(i - 1, 0)] and hs[i] >= hs[min(i + 1, len(hs) - 1)]]
            for i in cands:
                zl = e[i] + 0.02
                sel = ring & (np.abs(F[:, 2] - zl) < 0.08)
                if sel.sum() < 5:
                    continue
                zl = float(np.median(F[sel, 2]))
                cov = len(np.unique((az[sel] // 10).astype(int))) / 36.0
                if cov < 0.4 or zl - g0 < 0.12:
                    continue
                prot = 0.0
                for a in np.arange(0.0, 1.5, 0.1):
                    b = near & (d >= R + a) & (d < R + a + 0.1)
                    if b.sum() < 5 or np.mean(np.abs(F[b, 2] - zl) < 0.1) < 0.5:
                        break
                    prot = float(a + 0.1)
                prot = max(prot, 0.1)
                side = vert & (d > R - 0.15) & (d < R + prot + 0.2) & (F[:, 2] > g0) & (F[:, 2] < zl)
                nb = max(1, int(round((zl - g0) / 0.1)))
                filled = len(np.unique(np.clip(((F[side, 2] - g0) / 0.1).astype(int), 0, nb - 1))) / nb
                if filled < 0.6:
                    continue  # nothing under the ledge down to the ground: not a foundation
                found = {"top": zl, "protrusion": prot, "coverage": float(cov), "side_fill": float(filled)}
                break
    r_in = R + (found["protrusion"] if found else 0.0) + 0.4
    g, n = ring_ground(F, cx, cy, r_in, r_in + 2.5, z_base)
    if g is None:
        g, n = ring_ground(F, cx, cy, r_in, r_in + 8.0, z_base)
    if g is None:
        g = g0
    if found and (g is None or found["top"] - g < 0.12):
        found = None  # "ledge" level with the ground: no foundation
    return {"ground": g, "ground_faces": n, "foundation": found}


def row(obj, kind, parent, bottom_before, ground, support, support_top, bottom_after, note=""):
    gb = None if support_top is None or bottom_before is None else bottom_before - support_top
    gg = None if ground is None or bottom_before is None else bottom_before - ground
    ga = None if support_top is None or bottom_after is None else bottom_after - support_top
    return {"object": obj, "kind": kind, "parent": parent, "bottom_before_z": bottom_before, "ground_z": ground,
            "stands_on": support, "support_top_z": support_top, "gap_before_m": gb, "gap_to_ground_before_m": gg, "bottom_after_z": bottom_after,
            "gap_after_m": ga, "note": note}


def ground_tank(F, tank, rows: list) -> None:
    """Snap a vertical tank's base to its foundation top (and add the foundation) or to the
    ground; the eave and roof stay where they were measured."""
    if tank.sphere:
        return
    info = tank_footing(F, tank.center_x, tank.center_y, tank.radius, tank.z_base)
    g = info["ground"]
    before = float(tank.z_base)
    tank.z_base_measured = before
    if g is None:
        rows.append(row(f"{tank.name}_body", "tank_body", tank.name, before, None, "unknown", None, before,
                        "no ground faces round the tank; left as measured"))
        tank.ground_gap = None
        return
    fnd = info["foundation"]
    if fnd and abs(fnd["top"] - before) <= 1.0:
        target, support, note = fnd["top"], "foundation", ""
    else:
        target, support, note = g, "ground", ""
        if fnd:
            note = f"ledge at {fnd['top']:.2f} m ignored (more than 1 m from the shell base)"
            fnd = None
    delta = target - before
    if abs(delta) > TOL:
        note = (note + "; " if note else "") + ("base raised" if delta > 0 else "wall extended down") + f" {abs(delta):.2f} m"
    tank.z_base = target
    tank.wall_height = max(tank.wall_height - delta, 0.1)
    tank.total_height = tank.total_height - delta
    tank.z_ground = g
    tank.ground_gap = 0.0
    tank.ground_gap_before = before - target
    rows.append(row(f"{tank.name}_body", "tank_body", tank.name, before, g, support, target, target, note))
    if fnd:
        tank.foundation = {"z0": g, "z1": target, "height": target - g, "radius": tank.radius + fnd["protrusion"],
                           "protrusion": fnd["protrusion"], "coverage": fnd["coverage"]}
        rows.append(row(f"{tank.name}_foundation", "foundation", tank.name, None, g, "ground", g, g,
                        f"concrete foundation {target - g:.2f} m high, {fnd['protrusion']:.1f} m beyond the shell"))
    else:
        tank.foundation = None


def leg_grounds(F, tank) -> list[float | None]:
    """Ground right round each sphere leg's foot (outside its pedestal)."""
    s = tank.sphere
    rho = float(s.get("leg_rho") or tank.radius * 0.97)
    out = []
    for a in s.get("legs") or []:
        ar = math.radians(a)
        px, py = tank.center_x + rho * math.cos(ar), tank.center_y + rho * math.sin(ar)
        g, _n = ring_ground(F, px, py, 1.1, 2.6, float(tank.z_base), below=2.0, above=0.6)
        out.append(g)
    return out


def ground_only(tanks, connectors, F, rows: list) -> None:
    """Grounding without the structure detectors (``--no-structures``): tank bases /
    foundations, sphere bases and connector supports."""
    import statistics

    for tank in tanks:
        reach = max(tank.radius, float((tank.sphere or {}).get("leg_rho", 0.0))) + 6.0
        sub = F[(np.abs(F[:, 0] - tank.center_x) < reach) & (np.abs(F[:, 1] - tank.center_y) < reach)]
        if tank.sphere:
            gs = [g for g in leg_grounds(sub, tank) if g is not None]
            if gs:
                before = float(tank.z_base)
                tank.z_base_measured = before
                tank.z_base = tank.z_ground = float(statistics.median(gs))
                tank.sphere["leg_bottom"] = tank.z_base
                tank.ground_gap, tank.ground_gap_before = 0.0, before - tank.z_base
                rows.append(row(f"{tank.name}_legs", "sphere_legs", tank.name, before, tank.z_base, "ground", tank.z_base, tank.z_base))
        else:
            ground_tank(sub, tank, rows)
            fd = getattr(tank, "foundation", None)
            if fd:
                tank.extra_parts = [{"name": f"{tank.name}_foundation", "type": "cylinder", "center": [tank.center_x, tank.center_y],
                                     "z0": fd["z0"], "z1": fd["z1"], "radius": fd["radius"], "segments": 96}] + list(tank.extra_parts or [])
                tank.accessories = {**(tank.accessories or {}), "foundation": fd}
    for conn in connectors:
        for j, sp in enumerate(conn.supports, start=1):
            g, _n = rect_ground(F, sp["x"], sp["y"], sp.get("length", 1.0), sp.get("width", 1.0), sp.get("axis_deg", 0.0), sp["z_bottom"])
            before = float(sp["z_bottom"])
            if g is not None:
                sp["z_bottom"] = g
                sp["ground_gap_before_m"], sp["ground_gap_m"] = before - g, 0.0
            rows.append(row(f"{conn.name}_support_{j:02d}", "support", conn.name, before, g, "ground", g, sp["z_bottom"]))
