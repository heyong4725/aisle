"""Navigation nav_goal resolution (SPEC 210 MOB-2): a goal names a known
location (scenes/locations.toml) OR carries an explicit pose. Pure — no
dora, no sim."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

_LOCATIONS = Path(__file__).resolve().parents[1] / "scenes" / "locations.toml"
_LIMITS = Path(__file__).resolve().parents[3] / "env" / "limits.toml"


def load_locations() -> dict[str, list[float]]:
    """Named store-frame targets (MOB-2/MOB-5): name -> [x, y, yaw]."""
    with open(_LOCATIONS, "rb") as f:
        return {k: list(v) for k, v in tomllib.load(f)["locations"].items()}


def load_nav_params(embodiment: str) -> dict:
    """Nav-action lifecycle parameters (MOB-2) from env/limits.toml:
    arrival tolerance and the timeout/stall tick budgets."""
    with open(_LIMITS, "rb") as f:
        p = tomllib.load(f)["embodiment"][embodiment]
    return {
        "arrival_tol_m": float(p["nav_arrival_tol_m"]),
        "arrival_yaw_rad": float(p["nav_arrival_yaw_rad"]),
        "timeout_ticks": int(p["nav_timeout_ticks"]),
        "stall_ticks": int(p["nav_stall_ticks"]),
    }


def resolve_nav_goal(goal: dict, locations: dict[str, list[float]]) -> list[float]:
    """Resolve a nav_goal to a store-frame [x, y, yaw] (MOB-2).

    `{"location": name}` resolves via `locations`; `{"pose": [x, y, yaw]}`
    is used verbatim. An unknown name, or a goal with neither key, is a
    loud error — the nav action must never drive to a silent default."""
    if "pose" in goal:
        pose = [float(v) for v in goal["pose"]]
        if len(pose) != 3:
            raise ValueError(f"nav_goal pose must be [x, y, yaw], got {goal['pose']!r}")
        return pose
    if "location" in goal:
        name = goal["location"]
        if name not in locations:
            raise ValueError(f"unknown location {name!r}; known: {sorted(locations)}")
        return list(locations[name])
    raise ValueError(f"nav_goal needs a 'location' or 'pose' key, got {sorted(goal)}")


class NavStateMachine:
    """Pure nav-action lifecycle (SPEC 210 MOB-2), mirroring the episode
    action (TC-7). A goal opens a nav toward a store-frame target; each
    tick emits feedback {t, dist_remaining} (>= 2 Hz) or a terminal result
    {status, failure, t_end}. Ticks are deterministic (CON-5): a wall clock
    would make same-seed runs diverge. Handlers return
    [(topic, payload, goal_id), ...]."""

    def __init__(
        self,
        arrival_tol_m: float,
        timeout_ticks: int,
        stall_ticks: int,
        arrival_yaw_rad: float = math.pi,
    ) -> None:
        self.arrival_tol_m = arrival_tol_m
        self.arrival_yaw_rad = arrival_yaw_rad
        self.timeout_ticks = timeout_ticks
        self.stall_ticks = stall_ticks
        self.target: list[float] | None = None
        self.goal_id: str | None = None
        self.pose: list[float] | None = None
        self.ticks = 0
        self._best_dist = math.inf
        self._since_progress = 0

    def on_goal(self, target_pose: list[float], goal_id: str) -> list:
        if self.target is not None:  # TC-7: nav actions do not overlap
            return []
        self.target = [float(v) for v in target_pose]
        self.goal_id = goal_id
        self.pose = None
        self.ticks = 0
        self._best_dist = math.inf
        self._since_progress = 0
        return []

    def on_base_pose(self, pose: list[float]) -> list:
        """Latest base pose (MOB-1 base_pose); consumed by the next tick."""
        if self.target is not None:
            self.pose = [float(v) for v in pose]
        return []

    def _finish(self, status: str, failure: str | None) -> list:
        result = {"status": status, "failure": failure, "t_end": self.ticks}
        goal_id = self.goal_id
        self.target = None
        self.goal_id = None
        return [("nav_result", result, goal_id)]

    def on_tick(self) -> list:
        if self.target is None or self.pose is None:
            return []
        self.ticks += 1
        dist = math.hypot(self.target[0] - self.pose[0], self.target[1] - self.pose[1])
        yaw_err = abs(_wrap(self.target[2] - self.pose[2]))
        # arrival requires BOTH translation AND orientation to converge (MOB-2)
        if dist <= self.arrival_tol_m and yaw_err <= self.arrival_yaw_rad:
            return self._finish("success", None)
        # progress tracking (MOB-2 blocked): the combined remaining (position +
        # orientation) must keep shrinking, so the rotation phase counts as
        # progress and does not read as blocked
        remaining = dist + yaw_err
        if remaining < self._best_dist - 1e-6:
            self._best_dist = remaining
            self._since_progress = 0
        else:
            self._since_progress += 1
            if self._since_progress >= self.stall_ticks:
                return self._finish("fail", "blocked")
        if self.ticks >= self.timeout_ticks:
            return self._finish("fail", "timeout")
        # MOB-2 contract feedback is {t, dist_remaining}; orientation progress
        # is tracked internally (above) and verified via base_pose, not
        # exposed as an unapproved contract field
        return [("nav_feedback", {"t": self.ticks, "dist_remaining": dist}, self.goal_id)]


# proportional gains for the diff-drive controller (MOB-2); dimensionless
# scaling of distance->v and heading-error->omega, clamped to the base
# limits (MOB-3). Turn-in-place when badly misaligned: v is scaled down by
# the heading alignment so the base does not arc wide through keep-out.
_K_V = 1.0
_K_OMEGA = 2.0


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def base_cmd_toward(pose, target, limits, arrival_tol_m: float = 0.05) -> tuple[float, float]:
    """Diff-drive base_cmd [v, omega] driving `pose` toward `target`
    (store frame), clamped to the base velocity limits (MOB-2/MOB-3).

    Two phases: while farther than `arrival_tol_m`, steer toward the target
    POSITION and drive forward; once in position, hold v=0 and rotate in
    place to the target YAW. So a goal that only changes orientation still
    rotates rather than reporting instant arrival."""
    dx = float(target[0]) - float(pose[0])
    dy = float(target[1]) - float(pose[1])
    dist = math.hypot(dx, dy)
    if dist > arrival_tol_m:
        heading_err = _wrap(math.atan2(dy, dx) - float(pose[2]))
        omega = max(-limits.omega_max, min(limits.omega_max, _K_OMEGA * heading_err))
        # only drive forward while roughly aligned; turn in place otherwise
        align = max(0.0, math.cos(heading_err))
        v = max(0.0, min(limits.v_max, _K_V * dist * align))
        return v, omega
    # in position: rotate to the target orientation
    yaw_err = _wrap(float(target[2]) - float(pose[2]))
    omega = max(-limits.omega_max, min(limits.omega_max, _K_OMEGA * yaw_err))
    return 0.0, omega
