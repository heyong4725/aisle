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
