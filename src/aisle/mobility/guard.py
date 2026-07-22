"""Mobile base guard extension (SPEC 210 MOB-3, extends SPEC 080). Pure
clamp cores — no dora, no sim. Wired into the budget-guard node for the
`mobile` embodiment. Every limit is config from env/limits.toml (BG-2)."""

from __future__ import annotations

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
    )


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def clamp_base_cmd(cmd, arm_in_motion: bool, limits: BaseLimits) -> tuple[list[float], list[dict]]:
    """MOB-3: clamp a base_cmd [v, omega] to legal, never drop (BG-3).

    Enforces the base velocity limits, then the arm/base mutual exclusion:
    while the arm is in motion the base is clamped to creep speed. Returns
    (safe_cmd, violations); each violation is {reason, axis, requested,
    clamped}."""
    v, omega = float(cmd[0]), float(cmd[1])
    violations: list[dict] = []

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

    return [cv, co], violations
