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
    # flange horizontal reach past which the arm counts as "reaching" and the
    # shelf keep-out engages (home ~0.31 m is not reaching)
    arm_extended_reach_m: float
    # a base_cmd older than this is stale -> the guard stops the base (MOB-3)
    base_staleness_s: float


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
        arm_extended_reach_m=float(p["arm_extended_reach_m"]),
        base_staleness_s=float(p["base_staleness_s"]),
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
    footprint_radius: float = 0.0,
) -> tuple[list[float], list[dict]]:
    """MOB-3: clamp a base_cmd [v, omega] to legal, never drop (BG-3).

    Enforces the base velocity limits, then the arm/base mutual exclusion
    (arm in motion -> base clamped to creep), then the shelf keep-out: with
    the arm reaching, forward velocity is capped so the base (its footprint
    included) cannot enter a shelf's `min_shelf_dist_m` zone within one
    base_cmd step -- preventing ENTRY, not just motion once already inside.
    It FAILS CLOSED: with the arm reaching but no `base_pose` feedback the
    base is held at 0 (the keep-out cannot be verified). Rotation and backing
    away stay legal. `base_pose` is (x, y, yaw), `shelves` a list of
    (cx, cy, hx, hy) AABBs in the store frame. Returns (safe_cmd, violations);
    each violation is {reason, axis, requested, clamped}."""
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

    # MOB-3 keep-out (only forward motion; turning / backing away stay legal).
    if arm_extended and shelves and cv > 0:
        if base_pose is None:
            # FAIL CLOSED: the keep-out cannot be checked without a pose
            violations.append(
                {"reason": "base_keepout", "axis": "v", "requested": cv, "clamped": 0.0}
            )
            cv = 0.0
        else:
            px, py, yaw = float(base_pose[0]), float(base_pose[1]), float(base_pose[2])
            hx_dir, hy_dir = math.cos(yaw), math.sin(yaw)
            for cx, cy, hx, hy in shelves:
                if (cx - px) * hx_dir + (cy - py) * hy_dir <= 0:
                    continue  # not heading toward this shelf
                # remaining clearance before the footprint enters the zone;
                # cap v so ONE base_cmd step cannot cross the boundary
                clearance = _dist_to_aabb(px, py, cx, cy, hx, hy) - footprint_radius
                max_v = max(0.0, clearance - limits.min_shelf_dist_m) / limits.base_cmd_dt_s
                if cv > max_v:
                    violations.append(
                        {"reason": "base_keepout", "axis": "v", "requested": cv, "clamped": max_v}
                    )
                    cv = max_v

    return [cv, co], violations
