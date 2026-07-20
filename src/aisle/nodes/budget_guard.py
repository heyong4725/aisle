"""Budget-guard node (SPEC 080 BG-1..5).

Interposes on all motion command edges (BG-1; topology enforced by the
validator, VAL-5). The clamping core is pure and unit-tested without dora
or sim (CON-12): limits come exclusively from env/limits.toml (BG-2), and
on violation the command is clamped — never dropped — to the nearest
legal value while a violation JSON is published (BG-3). The guard must
never crash the dataflow (BG-3) and adds <2 ms p99 per command (BG-4,
measured in tests/unit/test_guard_latency.py).
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import numpy as np

from aisle.topics import stamp

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Panda modified-DH rows (a_{i-1}, d_i, cos(alpha_{i-1}), sin(alpha_{i-1}))
# for joints 1..7 (official Franka kinematics; alphas are 0 or +-pi/2 so
# the trig is exact and precomputed off the per-command path, BG-4)
_FRANKA_DH = (
    (0.0, 0.333, 1.0, 0.0),
    (0.0, 0.0, 0.0, -1.0),
    (0.0, 0.316, 0.0, 1.0),
    (0.0825, 0.0, 0.0, 1.0),
    (-0.0825, 0.384, 0.0, -1.0),
    (0.0, 0.0, 0.0, 1.0),
    (0.088, 0.0, 0.0, 1.0),
)
_FRANKA_FLANGE_D = 0.107

_AXES = ("x", "y", "z")


@dataclass(frozen=True)
class GuardLimits:
    """BG-2: every limit the guard enforces, loaded from env/limits.toml."""

    q_min: tuple[float, ...]
    q_max: tuple[float, ...]
    qdot_max: tuple[float, ...]
    cmd_dt_s: float
    workspace_min: tuple[float, float, float]
    workspace_max: tuple[float, float, float]
    fallback_qpos: tuple[float, ...]
    gripper_min: float
    gripper_max: float
    wall_timeout_s: float
    idle_reset_s: float

    # precomputed off the per-command path (BG-4); cached_property writes
    # the instance __dict__ directly, so frozen is preserved
    @cached_property
    def q_min_arr(self) -> np.ndarray:
        return np.asarray(self.q_min, dtype=np.float32)

    @cached_property
    def q_max_arr(self) -> np.ndarray:
        return np.asarray(self.q_max, dtype=np.float32)

    @cached_property
    def max_step_arr(self) -> np.ndarray:
        return np.asarray(self.qdot_max, dtype=np.float32) * self.cmd_dt_s


def load_limits(embodiment: str) -> GuardLimits:
    with open(_REPO_ROOT / "env" / "limits.toml", "rb") as f:
        raw = tomllib.load(f)
    if embodiment not in raw["embodiment"]:
        raise ValueError(
            f"env/limits.toml has no limits section for embodiment {embodiment!r};"
            " the guard refuses to guess (BG-2)"
        )
    emb = raw["embodiment"][embodiment]
    return GuardLimits(
        q_min=tuple(emb["q_min"]),
        q_max=tuple(emb["q_max"]),
        qdot_max=tuple(emb["qdot_max"]),
        cmd_dt_s=emb["cmd_dt_s"],
        workspace_min=tuple(emb["workspace_min"]),
        workspace_max=tuple(emb["workspace_max"]),
        fallback_qpos=tuple(emb["fallback_qpos"]),
        gripper_min=emb["gripper_min"],
        gripper_max=emb["gripper_max"],
        wall_timeout_s=raw["episode"]["wall_timeout_s"],
        idle_reset_s=raw["episode"]["idle_reset_s"],
    )


def fk_ee_pos(q_arm: np.ndarray) -> np.ndarray:
    """Flange position (base frame) via modified-DH forward kinematics on
    the commanded arm pose (BG-2)."""
    T = np.eye(4)
    for (a, d, ca, sa), theta in zip(_FRANKA_DH, q_arm, strict=True):
        ct, st = math.cos(float(theta)), math.sin(float(theta))
        T = T @ np.array(
            [
                [ct, -st, 0.0, a],
                [st * ca, ct * ca, -sa, -sa * d],
                [st * sa, ct * sa, ca, ca * d],
                [0.0, 0.0, 0.0, 1.0],
            ]
        )
    return T[:3, 3] + T[:3, 2] * _FRANKA_FLANGE_D


def _inside(ee: np.ndarray, limits: GuardLimits) -> bool:
    return all(limits.workspace_min[i] <= ee[i] <= limits.workspace_max[i] for i in range(3))


def _viol(
    reason: str, requested, clamped, joint: int | None = None, axis: str | None = None
) -> dict:
    v = {"reason": reason, "requested": requested, "clamped": clamped}
    if axis is not None:
        v["axis"] = axis
    else:
        v["joint"] = joint
    return v


def clamp_joint_cmd(
    cmd: np.ndarray, last_safe: np.ndarray, limits: GuardLimits, timed_out: bool
) -> tuple[np.ndarray, list[dict]]:
    """BG-3: pure clamp — always returns a legal command, never raises.

    Order: wall timeout (hold) -> malformed screen (hold bad entries) ->
    position -> velocity (vs last safe + contract dt) -> workspace (FK on
    the result, pulled back along the segment from last safe)."""
    last = np.asarray(last_safe, dtype=np.float32)
    n = len(limits.q_min)
    violations: list[dict] = []

    if timed_out:
        return last.copy(), [_viol("wall_timeout", None, None)]

    cmd = np.asarray(cmd, dtype=np.float32).reshape(-1)
    if cmd.shape != (n,):
        return last.copy(), [_viol("malformed", None, None)]
    safe = cmd.copy()
    for i in np.flatnonzero(~np.isfinite(safe)):
        violations.append(_viol("malformed", None, float(last[i]), joint=int(i)))
        safe[i] = last[i]

    clipped = np.clip(safe, limits.q_min_arr, limits.q_max_arr)
    for i in np.flatnonzero(clipped != safe):
        violations.append(_viol("position", float(safe[i]), float(clipped[i]), joint=int(i)))
    safe = clipped
    # BG-2: the workspace check applies to the COMMANDED pose — judged here,
    # before the velocity clamp shortens the step, so an out-of-workspace
    # intent is reported even when velocity limiting already contains it;
    # commanded_ee is None iff the commanded pose was inside
    commanded_ee = fk_ee_pos(safe[:7])
    if _inside(commanded_ee, limits):
        commanded_ee = None

    stepped = np.clip(safe, last - limits.max_step_arr, last + limits.max_step_arr)
    for i in np.flatnonzero(stepped != safe):
        violations.append(_viol("velocity", float(safe[i]), float(stepped[i]), joint=int(i)))
    velocity_clamped = stepped is not safe and bool(np.any(stepped != safe))
    safe = stepped

    # containment is an invariant of the OUTPUT regardless of what was
    # reported: FK is nonlinear, so even a velocity-shortened step must be
    # verified and pulled back if needed. When velocity left the command
    # untouched, its FK is the commanded one already computed.
    final_ee = (
        fk_ee_pos(safe[:7])
        if velocity_clamped
        else (commanded_ee if commanded_ee is not None else None)
    )
    if final_ee is not None and not _inside(final_ee, limits):
        if commanded_ee is None:  # velocity-clamped pose strayed on its own
            commanded_ee = final_ee
        if _inside(fk_ee_pos(last[:7]), limits):
            # largest t in [0, 1] along last -> safe whose FK stays inside
            good, bad = 0.0, 1.0
            for _ in range(12):  # sub-millimeter resolution on any step
                mid = (good + bad) / 2
                if _inside(fk_ee_pos((last + mid * (safe - last))[:7]), limits):
                    good = mid
                else:
                    bad = mid
            safe = (last + good * (safe - last)).astype(np.float32)
        else:  # last safe itself is outside (should not happen): hold home
            safe = np.asarray(limits.fallback_qpos, dtype=np.float32)
        final_ee = fk_ee_pos(safe[:7])
    if commanded_ee is not None:
        axis = next(
            (
                i
                for i in range(3)
                if not limits.workspace_min[i] <= commanded_ee[i] <= limits.workspace_max[i]
            ),
            2,
        )
        violations.append(
            {
                "reason": "workspace",
                "axis": _AXES[axis],
                "requested": float(commanded_ee[axis]),
                "clamped": float(final_ee[axis]),
            }
        )

    return safe, violations


def clamp_gripper_cmd(value: float, limits: GuardLimits) -> tuple[float, list[dict]]:
    """BG-1/BG-3: scalar gripper command clamped to its range; NaN falls to
    the open position (gripper_min)."""
    if not math.isfinite(value):
        return limits.gripper_min, [_viol("malformed", None, limits.gripper_min, axis="gripper")]
    clamped = min(max(value, limits.gripper_min), limits.gripper_max)
    if clamped != value:
        return clamped, [_viol("position", value, clamped, axis="gripper")]
    return clamped, []


def episode_elapsed(
    first_t: float, last_t: float, now: float, idle_reset_s: float
) -> tuple[float, float]:
    """BG-2 wall timer: a command gap of at least idle_reset_s is an episode
    boundary (reset/idle) and restarts the timer. Returns (episode start,
    elapsed seconds)."""
    if now - last_t >= idle_reset_s:
        return now, 0.0
    return first_t, now - first_t


def violation_payload(violation: dict, seq: int) -> dict:
    """BG-3: the published violation JSON is {reason, joint|axis, requested,
    clamped, seq}."""
    return {**violation, "seq": seq}


def main(clock=None) -> None:
    """Guard node: the clock is injected (CON-5)."""
    import json
    import os
    import sys
    import time

    import pyarrow as pa
    from dora import Node

    clock = clock or time.monotonic
    embodiment = os.environ.get("AISLE_EMBODIMENT", "franka")
    limits = load_limits(embodiment)
    fallback = np.asarray(limits.fallback_qpos, dtype=np.float32)

    node = Node()
    envs: dict[int, dict] = {}
    seq: dict[str, int] = {}
    counts: dict[str, int] = {}
    stats_t = clock()

    def next_seq(topic: str) -> int:
        seq[topic] = seq.get(topic, 0) + 1
        return seq[topic]

    def send(topic: str, value, metadata: dict, s: int | None = None) -> None:
        node.send_output(topic, value, stamp(metadata, s if s is not None else next_seq(topic)))

    def publish_violations(violations: list[dict], metadata: dict) -> None:
        for v in violations:
            counts[v["reason"]] = counts.get(v["reason"], 0) + 1
            s = next_seq("violation")
            payload = violation_payload(v, s)
            send("violation", pa.array([json.dumps(payload)]), metadata, s=s)
            print(f"guard violation: {payload}", file=sys.stderr)

    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        now = clock()
        if event["id"] == "joint_cmd":
            env_id = int(metadata.get("env_id", 0))
            state = envs.setdefault(env_id, {"last_safe": fallback, "first_t": now, "last_t": now})
            state["first_t"], elapsed = episode_elapsed(
                state["first_t"], state["last_t"], now, limits.idle_reset_s
            )
            state["last_t"] = now
            safe, violations = clamp_joint_cmd(
                event["value"].to_numpy(zero_copy_only=False),
                state["last_safe"],
                limits,
                timed_out=elapsed > limits.wall_timeout_s,
            )
            state["last_safe"] = safe
            send("joint_cmd_safe", pa.array(safe), metadata)
            publish_violations(violations, metadata)
        elif event["id"] == "gripper_cmd":
            raw = event["value"].to_numpy(zero_copy_only=False)
            value = float(raw[0]) if len(raw) else float("nan")
            safe_g, violations = clamp_gripper_cmd(value, limits)
            send("gripper_cmd_safe", pa.array(np.array([safe_g], dtype=np.float32)), metadata)
            publish_violations(violations, metadata)
        # BG-5: cumulative violation counts every 5 s (on command cadence)
        if now - stats_t >= 5.0:
            stats_t = now
            send("guard_stats", pa.array([json.dumps({"violations": counts})]), {})


if __name__ == "__main__":
    main()
