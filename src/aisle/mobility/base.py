"""Kinematic base model (SPEC 210, ADR-13): a differential-drive base is
integrated as a unicycle — no wheel or contact dynamics. Pure and
deterministic (CON-5): same (pose, cmd, dt) -> same next pose."""

from __future__ import annotations

import math


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def integrate_base_pose(pose, cmd, dt: float) -> list[float]:
    """Advance a store-frame base pose [x, y, yaw] by one tick under
    base_cmd [v, omega] (ADR-13): yaw turns first, then the base advances
    along the new heading. Yaw is wrapped to (-pi, pi]."""
    x, y, yaw = float(pose[0]), float(pose[1]), float(pose[2])
    v, omega = float(cmd[0]), float(cmd[1])
    yaw = _wrap(yaw + omega * dt)
    x += v * math.cos(yaw) * dt
    y += v * math.sin(yaw) * dt
    return [x, y, yaw]


def _ray_aabb(ox, oy, dx, dy, cx, cy, hx, hy, range_max):
    """Distance from (ox,oy) along unit (dx,dy) to an axis-aligned box
    centred (cx,cy) half-extents (hx,hy); inf if no forward hit (slab
    method)."""
    tmin, tmax = 0.0, range_max
    for o, d, c, h in ((ox, dx, cx, hx), (oy, dy, cy, hy)):
        lo, hi = c - h, c + h
        if abs(d) < 1e-12:
            if o < lo or o > hi:
                return math.inf
        else:
            t1, t2 = (lo - o) / d, (hi - o) / d
            if t1 > t2:
                t1, t2 = t2, t1
            tmin, tmax = max(tmin, t1), min(tmax, t2)
            if tmin > tmax:
                return math.inf
    return tmin


def base_scan_ranges(pose, obstacles, n, angle_min, angle_max, range_max) -> list[float]:
    """MOB-1 base_scan: n planar ranges (m) from the base origin, evenly
    spaced over [angle_min, angle_max] relative to the base heading, each
    the nearest AABB hit capped at range_max. obstacles: (cx, cy, hx, hy)
    in the store frame (ADR-13: flat 2-D raycast, no noise)."""
    x, y, yaw = float(pose[0]), float(pose[1]), float(pose[2])
    step = 0.0 if n <= 1 else (angle_max - angle_min) / (n - 1)
    ranges = []
    for i in range(n):
        a = yaw + angle_min + i * step
        dx, dy = math.cos(a), math.sin(a)
        best = range_max
        for cx, cy, hx, hy in obstacles:
            d = _ray_aabb(x, y, dx, dy, cx, cy, hx, hy, range_max)
            if d < best:
                best = d
        ranges.append(best)
    return ranges
