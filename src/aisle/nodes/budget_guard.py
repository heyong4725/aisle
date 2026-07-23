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

    n_arm_dof: int
    q_min: tuple[float, ...]
    q_max: tuple[float, ...]
    qdot_max: tuple[float, ...]
    cmd_dt_s: float
    workspace_min: tuple[float, float, float]
    workspace_max: tuple[float, float, float]
    fallback_qpos: tuple[float, ...]
    gripper_min: float
    gripper_max: float
    gripper_rate_max: float
    gripper_dt_s: float
    wall_timeout_s: float

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


# mobile reuses the franka arm's limits (ADR-14); its own [embodiment.mobile]
# section carries only the base limits (load_base_limits). Mirrors the
# validator's EMBODIMENT_ARM resolution.
_ARM_EMBODIMENT = {"mobile": "franka"}


def load_limits(embodiment: str) -> GuardLimits:
    with open(_REPO_ROOT / "env" / "limits.toml", "rb") as f:
        raw = tomllib.load(f)
    arm_kind = _ARM_EMBODIMENT.get(embodiment, embodiment)
    if arm_kind not in raw["embodiment"]:
        raise ValueError(
            f"env/limits.toml has no limits section for embodiment {embodiment!r};"
            " the guard refuses to guess (BG-2)"
        )
    emb = raw["embodiment"][arm_kind]
    return GuardLimits(
        n_arm_dof=emb["n_arm_dof"],
        q_min=tuple(emb["q_min"]),
        q_max=tuple(emb["q_max"]),
        qdot_max=tuple(emb["qdot_max"]),
        cmd_dt_s=emb["cmd_dt_s"],
        workspace_min=tuple(emb["workspace_min"]),
        workspace_max=tuple(emb["workspace_max"]),
        fallback_qpos=tuple(emb["fallback_qpos"]),
        gripper_min=emb["gripper_min"],
        gripper_max=emb["gripper_max"],
        gripper_rate_max=emb["gripper_rate_max"],
        gripper_dt_s=emb["gripper_dt_s"],
        wall_timeout_s=raw["episode"]["wall_timeout_s"],
    )


def fk_flange(q_arm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Flange position and rotation matrix (base frame) via modified-DH
    forward kinematics on the commanded arm pose (BG-2; also the shared
    kinematics for ik-trajectory)."""
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
    return T[:3, 3] + T[:3, 2] * _FRANKA_FLANGE_D, T[:3, :3]


def fk_ee_pos(q_arm: np.ndarray) -> np.ndarray:
    """Flange position only (the guard's workspace check)."""
    return fk_flange(q_arm)[0]


def gripper_to_fingers(g: float, limits: GuardLimits) -> np.ndarray:
    """Normalized gripper (0 open .. 1 closed) -> finger joint positions
    (fingers are open at q_max, closed at 0 — franka; ADR-9)."""
    return limits.q_max_arr[limits.n_arm_dof :] * (1.0 - g)


def fingers_to_gripper(q: np.ndarray, limits: GuardLimits) -> float:
    """Inverse of gripper_to_fingers on the finger slice of a command."""
    open_pos = limits.q_max_arr[limits.n_arm_dof :]
    return float(1.0 - np.mean(np.asarray(q, np.float32)[limits.n_arm_dof :] / open_pos))


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
    commanded_ee = fk_ee_pos(safe[: limits.n_arm_dof])
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
        fk_ee_pos(safe[: limits.n_arm_dof])
        if velocity_clamped
        else (commanded_ee if commanded_ee is not None else None)
    )
    if final_ee is not None and not _inside(final_ee, limits):
        if commanded_ee is None:  # velocity-clamped pose strayed on its own
            commanded_ee = final_ee
        if _inside(fk_ee_pos(last[: limits.n_arm_dof]), limits):
            # largest t in [0, 1] along last -> safe whose FK stays inside
            good, bad = 0.0, 1.0
            for _ in range(12):  # sub-millimeter resolution on any step
                mid = (good + bad) / 2
                if _inside(fk_ee_pos((last + mid * (safe - last))[: limits.n_arm_dof]), limits):
                    good = mid
                else:
                    bad = mid
            safe = (last + good * (safe - last)).astype(np.float32)
        else:  # last safe itself is outside (should not happen): hold home
            safe = np.asarray(limits.fallback_qpos, dtype=np.float32)
        final_ee = fk_ee_pos(safe[: limits.n_arm_dof])
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


def clamp_gripper_cmd(
    value: float, last_safe: float, limits: GuardLimits, timed_out: bool
) -> tuple[float, list[dict]]:
    """BG-1/BG-3: scalar gripper command under the SAME regime as joints —
    wall timeout holds it, NaN holds it at the last safe value, then range
    and rate (vs last safe + contract dt) clamps (PR review: the gripper
    must not bypass timeout or velocity enforcement)."""
    if timed_out:
        return last_safe, [_viol("wall_timeout", None, None, axis="gripper")]
    if not math.isfinite(value):
        return last_safe, [_viol("malformed", None, last_safe, axis="gripper")]
    violations = []
    clamped = min(max(value, limits.gripper_min), limits.gripper_max)
    if clamped != value:
        violations.append(_viol("position", value, clamped, axis="gripper"))
    max_step = limits.gripper_rate_max * limits.gripper_dt_s
    stepped = min(max(clamped, last_safe - max_step), last_safe + max_step)
    if stepped != clamped:
        violations.append(_viol("velocity", clamped, stepped, axis="gripper"))
    return stepped, violations


class EpisodeTimer:
    """BG-2 wall timer, anchored at the RESET that starts the episode —
    not the first command, or a policy could delay its first command to
    stretch the budget (PR review round 2). Idle pauses do not restart it
    either. Before any reset is seen (bare startup) the first command
    anchors, the only signal available."""

    def __init__(self) -> None:
        self._start: float | None = None

    def on_command(self, now: float) -> float:
        if self._start is None:
            self._start = now
        return now - self._start

    def on_reset(self, now: float) -> None:
        self._start = now


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

    from aisle.mobility.guard import (
        base_creep_deadline,
        clamp_base_cmd,
        load_base_limits,
        valid_base_pose,
    )

    clock = clock or time.monotonic
    embodiment = os.environ.get("AISLE_EMBODIMENT", "franka")
    limits = load_limits(embodiment)
    fallback = np.asarray(limits.fallback_qpos, dtype=np.float32)
    is_mobile = embodiment == "mobile"
    base_limits = load_base_limits(embodiment) if is_mobile else None
    # MOB-3 keep-out geometry: the shelf AABBs and the base footprint radius
    # the base must not drive into with the arm extended
    shelves: list = []
    footprint_r = 0.0
    if is_mobile:
        from aisle.nodes.dora_genesis import _scan_obstacles
        from aisle.scenes.pharmacy import load_physics

        physics = load_physics()
        shelves = _scan_obstacles(physics, embodiment)
        footprint_r = float(physics["embodiment"][embodiment]["base_footprint_radius_m"])

    node = Node()
    envs: dict[int, dict] = {}
    seq: dict[str, int] = {}
    counts: dict[str, int] = {}

    def new_state() -> dict:
        return {
            "last_safe": fallback,
            "last_gripper": 0.0,
            # MOB-3 mutex: the base is held at creep until this deadline; a
            # commanded arm-target change pushes it out, silence lets it pass
            "arm_motion_deadline": float("-inf"),
            "base_pose": None,  # latest base_pose feedback (MOB-3 keep-out)
            "last_base_cmd_t": None,  # wall time of the last base_cmd (watchdog)
            "last_base_safe": [0.0, 0.0],  # last emitted safe base cmd
            "timer": EpisodeTimer(),
        }

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
            state = envs.setdefault(env_id, new_state())
            timed_out = state["timer"].on_command(now) > limits.wall_timeout_s
            prev_arm = np.asarray(state["last_safe"], dtype=np.float32)[: limits.n_arm_dof]
            safe, violations = clamp_joint_cmd(
                event["value"].to_numpy(zero_copy_only=False),
                state["last_safe"],
                limits,
                timed_out=timed_out,
            )
            # MOB-3 mutex: a commanded arm-target CHANGE (re)opens a hold
            # window of base_limits.arm_motion_hold_s. The window PERSISTS
            # while the arm travels even if the same target repeats, and
            # EXPIRES on command silence — so a settled arm releases the base
            # and a still-moving arm keeps it clamped. Deterministic (CON-5).
            if is_mobile:
                changed = bool(np.any(safe[: limits.n_arm_dof] != prev_arm))
                state["arm_motion_deadline"] = base_creep_deadline(
                    state["arm_motion_deadline"], changed, now, base_limits.arm_motion_hold_s
                )
            state["last_safe"] = safe
            # the fingers ARE the gripper: keep the gripper channel's rate
            # reference in sync so alternating channels cannot double the
            # effective finger rate (PR review round 2)
            state["last_gripper"] = fingers_to_gripper(safe, limits)
            send("joint_cmd_safe", pa.array(safe), metadata)
            publish_violations(violations, metadata)
        elif event["id"] == "base_pose" and is_mobile:
            # MOB-3 keep-out feedback: cache the base pose. VALIDATE it first
            # (BG-3): a malformed pose must not crash clamp_base_cmd or bypass
            # keep-out — a bad pose caches None so keep-out fails closed.
            env_id = int(metadata.get("env_id", 0))
            state = envs.setdefault(env_id, new_state())
            pose = event["value"].to_numpy(zero_copy_only=False).tolist()
            if valid_base_pose(pose):
                state["base_pose"] = [float(p) for p in pose]
            else:
                state["base_pose"] = None
                publish_violations(
                    [
                        {
                            "reason": "base_pose_malformed",
                            "axis": "pose",
                            "requested": None,
                            "clamped": None,
                        }
                    ],
                    metadata,
                )
        elif event["id"] == "base_cmd" and is_mobile:
            # MOB-3: base velocity limits, arm/base mutual exclusion (base
            # clamped to creep while the arm moves), the shelf keep-out (no
            # entry into a shelf zone with the arm reaching), and the BG-2
            # episode wall timeout. Never dropped (BG-3).
            env_id = int(metadata.get("env_id", 0))
            state = envs.setdefault(env_id, new_state())
            state["last_base_cmd_t"] = now
            timed_out = state["timer"].on_command(now) > limits.wall_timeout_s
            arm_in_motion = now < state["arm_motion_deadline"]
            arm = np.asarray(state["last_safe"], dtype=np.float32)[: limits.n_arm_dof]
            ee = fk_ee_pos(arm)
            arm_extended = float(np.hypot(ee[0], ee[1])) > base_limits.arm_extended_reach_m
            safe_b, violations = clamp_base_cmd(
                event["value"].to_numpy(zero_copy_only=False),
                arm_in_motion,
                base_limits,
                base_pose=state["base_pose"],
                shelves=shelves,
                arm_extended=arm_extended,
                footprint_radius=footprint_r,
            )
            if timed_out and safe_b != [0.0, 0.0]:
                violations.append(
                    {
                        "reason": "base_timeout",
                        "axis": "cmd",
                        "requested": safe_b,
                        "clamped": [0.0, 0.0],
                    }
                )
                safe_b = [0.0, 0.0]
            state["last_base_safe"] = safe_b
            send("base_cmd_safe", pa.array(np.asarray(safe_b, dtype=np.float32)), metadata)
            publish_violations(violations, metadata)
        elif event["id"] == "gripper_cmd":
            env_id = int(metadata.get("env_id", 0))
            state = envs.setdefault(env_id, new_state())
            timed_out = state["timer"].on_command(now) > limits.wall_timeout_s
            raw = event["value"].to_numpy(zero_copy_only=False)
            value = float(raw[0]) if len(raw) else float("nan")
            safe_g, violations = clamp_gripper_cmd(
                value, state["last_gripper"], limits, timed_out=timed_out
            )
            state["last_gripper"] = safe_g
            updated = np.array(state["last_safe"], dtype=np.float32)
            updated[limits.n_arm_dof :] = gripper_to_fingers(safe_g, limits)
            state["last_safe"] = updated
            send("gripper_cmd_safe", pa.array(np.array([safe_g], dtype=np.float32)), metadata)
            publish_violations(violations, metadata)
        elif event["id"] == "reset_done":
            # the authoritative episode boundary: the wall timer anchors
            # HERE (not at the first command), and velocity/hold state is
            # re-referenced to home — the robot IS at home after a
            # teleport reset
            for state in envs.values():
                state["timer"].on_reset(now)
                state["last_safe"] = fallback
                state["last_gripper"] = 0.0
                state["arm_motion_deadline"] = float("-inf")
                # MOB-3: clear the cached pose (keep-out fails closed until a
                # fresh pose arrives) and the watchdog/latched-base state
                state["base_pose"] = None
                state["last_base_cmd_t"] = None
                state["last_base_safe"] = [0.0, 0.0]
        elif event["id"] == "base_watchdog" and is_mobile:
            # MOB-3 watchdog on a DEDICATED fast input (separate from the 0.2 Hz
            # BG-5 stats `tick`): the bridge latches the last base_cmd_safe and
            # integrates it every tick, so a stale command (producer died) or a
            # wall-timed-out episode would drive forever. Stop any latched
            # moving base by emitting [0, 0] once.
            for env_id, state in envs.items():
                last_t = state["last_base_cmd_t"]
                if last_t is None or state["last_base_safe"] == [0.0, 0.0]:
                    continue
                stale = now - last_t > base_limits.base_staleness_s
                timed_out = state["timer"].on_command(now) > limits.wall_timeout_s
                if stale or timed_out:
                    reason = "base_timeout" if timed_out else "base_stale"
                    meta = {"env_id": env_id}
                    send("base_cmd_safe", pa.array(np.zeros(2, dtype=np.float32)), meta)
                    publish_violations(
                        [
                            {
                                "reason": reason,
                                "axis": "cmd",
                                "requested": state["last_base_safe"],
                                "clamped": [0.0, 0.0],
                            }
                        ],
                        meta,
                    )
                    state["last_base_safe"] = [0.0, 0.0]
        elif event["id"] == "tick":
            # BG-5: cumulative violation counts every 5 s, timer-driven —
            # emitted even when no commands flow
            send("guard_stats", pa.array([json.dumps({"violations": counts})]), {})


if __name__ == "__main__":
    main()
