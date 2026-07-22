"""Mobile base guard extension (SPEC 210 MOB-3, extends SPEC 080). Pure
clamp cores — no dora, no sim. Wired into the budget-guard node for the
`mobile` embodiment. Every limit is config from env/limits.toml (BG-2)."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

_LIMITS = Path(__file__).resolve().parents[3] / "env" / "limits.toml"


@dataclass(frozen=True)
class BaseLimits:
    v_max: float
    omega_max: float
    v_creep: float
    omega_creep: float
    base_cmd_dt_s: float
    min_shelf_dist_m: float
    # how long a commanded arm-target change keeps the base clamped to creep
    # (the mutex hold); refreshed by each change, expires on silence (MOB-3)
    arm_motion_hold_s: float


def load_base_limits(embodiment: str) -> BaseLimits:
    with open(_LIMITS, "rb") as f:
        table = tomllib.load(f)["embodiment"]
    if embodiment not in table or "v_max" not in table[embodiment]:
        raise ValueError(f"env/limits.toml has no base limits for embodiment {embodiment!r}")
    p = table[embodiment]
    return BaseLimits(
        v_max=float(p["v_max"]),
        omega_max=float(p["omega_max"]),
        v_creep=float(p["v_creep"]),
        omega_creep=float(p["omega_creep"]),
        base_cmd_dt_s=float(p["base_cmd_dt_s"]),
        min_shelf_dist_m=float(p["min_shelf_dist_m"]),
        arm_motion_hold_s=float(p["arm_motion_hold_s"]),
    )


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _json_safe(x) -> float | None:
    """Map a possibly-non-finite command element to a JSON-safe value: a
    finite float stays, NaN/Inf become None (json.dumps(nan) is invalid)."""
    f = float(x)
    return f if math.isfinite(f) else None


def _dist_to_aabb(px: float, py: float, cx: float, cy: float, hx: float, hy: float) -> float:
    """Planar distance from point (px,py) to the AABB centered (cx,cy) with
    half-extents (hx,hy); 0 inside."""
    return math.hypot(max(abs(px - cx) - hx, 0.0), max(abs(py - cy) - hy, 0.0))


def base_creep_deadline(
    prev_deadline: float, target_changed: bool, now: float, hold_s: float
) -> float:
    """MOB-3 mutex window. A commanded arm-target CHANGE refreshes the creep
    hold to now + hold_s; otherwise the prior deadline stands. So a repeated
    target keeps the window opened by the last real move (the arm is still
    travelling), while command silence lets the window expire and release the
    base. `arm_in_motion` is then simply `now < deadline`. Pure/deterministic
    (CON-5): the caller injects `now`."""
    return now + hold_s if target_changed else prev_deadline


def clamp_base_cmd(
    cmd,
    arm_in_motion: bool,
    limits: BaseLimits,
    *,
    base_pose=None,
    shelves=None,
    arm_extended: bool = False,
) -> tuple[list[float], list[dict]]:
    """MOB-3: clamp a base_cmd [v, omega] to legal, never drop (BG-3).

    Enforces the base velocity limits, then the arm/base mutual exclusion
    (arm in motion -> base clamped to creep), then the shelf keep-out: with
    the arm extended past the base footprint, forward motion TOWARD a shelf
    inside `min_shelf_dist_m` is clamped to 0 (rotation and backing away stay
    legal, so the base can still recover). `base_pose` is (x, y, yaw) and
    `shelves` is a list of (cx, cy, hx, hy) AABBs in the store frame. Returns
    (safe_cmd, violations); each violation is {reason, axis, requested,
    clamped}."""
    violations: list[dict] = []

    # BG-3 fail-safe FIRST: a short vector must not IndexError and a
    # non-finite value must not slip through _clip (clip(nan) returns the
    # upper bound = MAX velocity). Malformed -> hold [0, 0] + violation.
    if len(cmd) < 2 or not all(math.isfinite(float(cmd[i])) for i in range(2)):
        requested = [_json_safe(cmd[i]) for i in range(min(len(cmd), 2))]
        violations.append(
            {
                "reason": "base_malformed",
                "axis": "cmd",
                "requested": requested,
                "clamped": [0.0, 0.0],
            }
        )
        return [0.0, 0.0], violations

    v, omega = float(cmd[0]), float(cmd[1])

    cv = _clip(v, -limits.v_max, limits.v_max)
    if cv != v:
        violations.append({"reason": "base_velocity", "axis": "v", "requested": v, "clamped": cv})
    co = _clip(omega, -limits.omega_max, limits.omega_max)
    if co != omega:
        violations.append(
            {"reason": "base_velocity", "axis": "omega", "requested": omega, "clamped": co}
        )

    if arm_in_motion:
        if abs(cv) > limits.v_creep:
            new = _clip(cv, -limits.v_creep, limits.v_creep)
            violations.append(
                {"reason": "base_arm_exclusion", "axis": "v", "requested": cv, "clamped": new}
            )
            cv = new
        if abs(co) > limits.omega_creep:
            new = _clip(co, -limits.omega_creep, limits.omega_creep)
            violations.append(
                {"reason": "base_arm_exclusion", "axis": "omega", "requested": co, "clamped": new}
            )
            co = new

    # MOB-3 keep-out: with the arm extended past the footprint, do not let the
    # base translate INTO a shelf's keep-out radius. Only forward motion whose
    # heading points at the shelf is stopped; turning and backing away remain
    # legal so the base is never trapped.
    if arm_extended and base_pose is not None and shelves and cv > 0:
        px, py, yaw = float(base_pose[0]), float(base_pose[1]), float(base_pose[2])
        hx_dir, hy_dir = math.cos(yaw), math.sin(yaw)
        for cx, cy, hx, hy in shelves:
            if _dist_to_aabb(px, py, cx, cy, hx, hy) < limits.min_shelf_dist_m:
                if (cx - px) * hx_dir + (cy - py) * hy_dir > 0:  # heading toward shelf
                    violations.append(
                        {"reason": "base_keepout", "axis": "v", "requested": cv, "clamped": 0.0}
                    )
                    cv = 0.0
                    break

    return [cv, co], violations
