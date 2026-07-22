"""Navigation nav_goal resolution (SPEC 210 MOB-2): a goal names a known
location (scenes/locations.toml) OR carries an explicit pose. Pure — no
dora, no sim."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

_LOCATIONS = Path(__file__).resolve().parents[1] / "scenes" / "locations.toml"


def load_locations() -> dict[str, list[float]]:
    """Named store-frame targets (MOB-2/MOB-5): name -> [x, y, yaw]."""
    with open(_LOCATIONS, "rb") as f:
        return {k: list(v) for k, v in tomllib.load(f)["locations"].items()}


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

    def __init__(self, arrival_tol_m: float, timeout_ticks: int, stall_ticks: int) -> None:
        self.arrival_tol_m = arrival_tol_m
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
        if dist <= self.arrival_tol_m:
            return self._finish("success", None)
        # progress tracking (MOB-2 blocked): distance must keep shrinking
        if dist < self._best_dist - 1e-6:
            self._best_dist = dist
            self._since_progress = 0
        else:
            self._since_progress += 1
            if self._since_progress >= self.stall_ticks:
                return self._finish("fail", "blocked")
        if self.ticks >= self.timeout_ticks:
            return self._finish("fail", "timeout")
        return [("nav_feedback", {"t": self.ticks, "dist_remaining": dist}, self.goal_id)]
