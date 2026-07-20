"""Oracle verifier (SPEC 040 VER-1..4).

`judge` is a pure function of its arguments — importable and unit-testable
without dora or sim (VER-1). The node wrapper subscribes oracle_state and
episode_goal (plus joint_state solely for VER-2's robot-home condition,
snapshotted into cfg — ADR-8) and publishes episode_result per TC-7/8.
All thresholds come from thresholds.toml (VER-2); the failure taxonomy is
exactly VER-3's five classes, with wrong_object fired the moment ANY
non-target box enters the tray.
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

_VERIFIER_DIR = Path(__file__).parent

FAILURE_CLASSES = ("wrong_object", "dropped", "timeout", "never_grasped", "collision")


def load_thresholds() -> dict:
    with open(_VERIFIER_DIR / "thresholds.toml", "rb") as f:
        return tomllib.load(f)


@dataclass(frozen=True)
class JudgeCfg:
    """Per-episode configuration assembled by the node: scene geometry,
    initial poses, the episode timeout, and the latest robot state."""

    tray_min: tuple[float, float, float]
    tray_max: tuple[float, float, float]
    box_half_extents: tuple[tuple[float, float, float], ...]
    initial_positions: tuple[tuple[float, float, float], ...]
    timeout_s: float
    upright_max_deg: float
    tray_margin_m: float
    dropped_z_m: float
    move_epsilon_m: float
    knock_epsilon_m: float
    resting_tolerance_m: float
    robot_home_tolerance_rad: float
    # VER-2 robot-home condition: max |qpos - home| snapshot (ADR-8);
    # None means "not yet reported" and blocks success, never failure
    robot_home_error_rad: float | None = None
    home_check_enabled: bool = True
    extra: dict = field(default_factory=dict, compare=False)


def _box_pose(oracle_state: np.ndarray, idx: int) -> tuple[np.ndarray, np.ndarray]:
    block = np.asarray(oracle_state, dtype=np.float32).reshape(-1)[idx * 7 : idx * 7 + 7]
    return block[:3], block[3:7]  # pos, quat (x, y, z, w) per TC-1


def _rot_matrix(quat_xyzw: np.ndarray) -> np.ndarray:
    x, y, z, w = (float(v) for v in quat_xyzw)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def _tilt_deg(quat_xyzw: np.ndarray) -> float:
    """Angle between the box's local z-axis and world z."""
    zz = _rot_matrix(quat_xyzw)[2, 2]
    return math.degrees(math.acos(max(-1.0, min(1.0, zz))))


def _world_half_extents(half: tuple[float, float, float], quat_xyzw: np.ndarray) -> np.ndarray:
    """World-frame AABB half-extents of a rotated box: |R| @ half."""
    return np.abs(_rot_matrix(quat_xyzw)) @ np.asarray(half, dtype=np.float64)


def _aabb_inside_tray(
    pos: np.ndarray, half: tuple[float, float, float], quat_xyzw: np.ndarray, cfg: JudgeCfg
) -> bool:
    """Open-topped tray volume: the box's WORLD-frame AABB (rotation-aware —
    yaw and tilt change the footprint) must sit within the tray footprint
    (x, y) and REST on the tray floor — bottom within
    [floor - margin, floor + resting_tolerance]. Medicine boxes are taller
    than the tray walls, so a closed-top test could never pass; the resting
    bound keeps an airborne box passing over the tray from scoring."""
    margin = cfg.tray_margin_m
    extents = _world_half_extents(half, quat_xyzw)
    for i in range(2):  # x, y footprint
        if pos[i] - extents[i] < cfg.tray_min[i] - margin:
            return False
        if pos[i] + extents[i] > cfg.tray_max[i] + margin:
            return False
    bottom = pos[2] - extents[2]
    return cfg.tray_min[2] - margin <= bottom <= cfg.tray_min[2] + cfg.resting_tolerance_m


def _center_inside_tray(pos: np.ndarray, cfg: JudgeCfg) -> bool:
    """wrong_object entry region: footprint containment BOUNDED in z —
    tray_max[2] is the entry ceiling (tray floor + entry height, set by
    build_judge_cfg). An unbounded ceiling would classify shelf boxes
    standing over the footprint (or boxes arcing above it) as having
    entered the tray."""
    return all(cfg.tray_min[i] <= pos[i] <= cfg.tray_max[i] for i in range(3))


def judge(
    oracle_state: np.ndarray, target_idx: int, t: float, cfg: JudgeCfg
) -> tuple[str, str | None]:
    """VER-1: pure verdict for one oracle sample.

    Returns ("success", None), ("fail", <VER-3 class>), or
    ("ongoing", None). Ordering encodes VER-3's safety asymmetry:
    wrong_object fires immediately, before any success or timeout
    consideration."""
    state = np.asarray(oracle_state, dtype=np.float32).reshape(-1)
    n_boxes = len(cfg.box_half_extents)

    # VER-3: the moment ANY non-target box enters the tray
    for idx in range(n_boxes):
        if idx == target_idx:
            continue
        pos, _ = _box_pose(state, idx)
        if _center_inside_tray(pos, cfg):
            return "fail", "wrong_object"

    target_pos, target_quat = _box_pose(state, target_idx)

    # dropped: the target is on the floor
    if float(target_pos[2]) < cfg.dropped_z_m:
        return "fail", "dropped"

    # collision: a non-target box was knocked away from its start pose
    # (oracle proxy — poses are the only contact evidence available, ADR-8)
    for idx in range(n_boxes):
        if idx == target_idx:
            continue
        pos, _ = _box_pose(state, idx)
        displacement = float(
            np.linalg.norm(pos - np.asarray(cfg.initial_positions[idx], dtype=np.float32))
        )
        if displacement > cfg.knock_epsilon_m:
            return "fail", "collision"

    # the deadline gates success: a placement completed after timeout_s is
    # a timeout, never a late success (TC-8 metrics must not inflate)
    if t >= cfg.timeout_s:
        moved = float(
            np.linalg.norm(
                target_pos - np.asarray(cfg.initial_positions[target_idx], dtype=np.float32)
            )
        )
        if moved <= cfg.move_epsilon_m:
            return "fail", "never_grasped"
        return "fail", "timeout"

    # VER-2 success: world AABB inside tray AND upright within threshold AND
    # robot home; "toppled but inside and within the upright band" is SUCCESS
    in_tray = _aabb_inside_tray(target_pos, cfg.box_half_extents[target_idx], target_quat, cfg)
    upright = _tilt_deg(target_quat) <= cfg.upright_max_deg
    robot_home = (not cfg.home_check_enabled) or (
        cfg.robot_home_error_rad is not None
        and cfg.robot_home_error_rad <= cfg.robot_home_tolerance_rad
    )
    if in_tray and upright and robot_home:
        return "success", None

    return "ongoing", None


def threshold_kwargs(thresholds: dict) -> dict:
    """The thresholds.toml -> JudgeCfg field mapping, in ONE place so tests
    build cfgs through the same wiring as production."""
    return dict(
        upright_max_deg=thresholds["success"]["upright_max_deg"],
        tray_margin_m=thresholds["success"]["tray_margin_m"],
        resting_tolerance_m=thresholds["success"]["resting_tolerance_m"],
        robot_home_tolerance_rad=thresholds["success"]["robot_home_tolerance_rad"],
        dropped_z_m=thresholds["failure"]["dropped_z_m"],
        move_epsilon_m=thresholds["failure"]["move_epsilon_m"],
        knock_epsilon_m=thresholds["failure"]["knock_epsilon_m"],
    )


def build_judge_cfg(
    physics: dict,
    meds: dict,
    embodiment: str,
    timeout_s: float,
    initial_positions,
    robot_home_error_rad: float | None,
) -> JudgeCfg:
    """Assemble the per-episode cfg from the canonical config files (VER-2:
    thresholds.toml; geometry from the scene layout profile)."""
    from aisle.scenes.pharmacy import resolve_layout

    thresholds = load_thresholds()
    layout = resolve_layout(physics, embodiment)
    tray_pos, tray_size = layout["tray"]["pos"], layout["tray"]["size"]
    tray_min = (
        tray_pos[0] - tray_size[0] / 2,
        tray_pos[1] - tray_size[1] / 2,
        tray_pos[2] + tray_size[2] / 2,  # open-topped: floor is the base top
    )
    tray_max = (
        tray_pos[0] + tray_size[0] / 2,
        tray_pos[1] + tray_size[1] / 2,
        # wrong_object entry ceiling: tray floor + entry height
        tray_pos[2] + tray_size[2] / 2 + thresholds["failure"]["wrong_object_entry_height_m"],
    )
    return JudgeCfg(
        tray_min=tray_min,
        tray_max=tray_max,
        box_half_extents=tuple(tuple(s / 2 for s in meds[name]["size"]) for name in meds),
        initial_positions=tuple(tuple(p) for p in initial_positions),
        timeout_s=timeout_s,
        robot_home_error_rad=robot_home_error_rad,
        # no home_qpos in the profile (so101 until its asset lands) means the
        # home condition is unobservable — disable rather than block success
        # forever (ADR-8)
        home_check_enabled="home_qpos" in physics["embodiment"][embodiment],
        **threshold_kwargs(thresholds),
    )


def main() -> None:
    """Verifier node (VER-1): oracle_state + episode_goal in (joint_state
    solely for the VER-2 home condition, ADR-8), episode_result out per
    TC-7/8. Exactly one result per goal."""
    import json
    import os
    import sys

    import pyarrow as pa
    from dora import Node

    from aisle.scenes.pharmacy import MED_NAMES, load_meds, load_physics

    embodiment = os.environ.get("AISLE_EMBODIMENT", "franka")
    physics = load_physics()
    meds = load_meds()
    home = physics["embodiment"][embodiment].get("home_qpos")

    node = Node()
    goal = None
    target_idx = -1
    goal_id = None
    goal_t0_ns = None
    goal_barrier_ns = -1
    latest_oracle_ns = -1
    result_seq = 0
    cfg = None
    home_error: float | None = None
    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if event["id"] == "episode_goal":
            if goal is not None:
                # actions must not overlap (TC-7): refuse, keep the active
                # episode; the harness sends goals sequentially
                print(f"goal {metadata.get('goal_id')} refused: episode active", file=sys.stderr)
                continue
            candidate = json.loads(event["value"][0].as_py())
            if candidate.get("target_med") not in MED_NAMES:
                print(
                    f"goal refused: unknown target_med {candidate.get('target_med')!r}",
                    file=sys.stderr,
                )
                continue
            goal = candidate
            target_idx = MED_NAMES.index(goal["target_med"])
            goal_id = metadata.get("goal_id", "")
            goal_t0_ns = None
            # freshness barrier: in-flight pre-goal oracle samples must not
            # seed the new episode's initial poses or clock
            goal_barrier_ns = latest_oracle_ns
            cfg = None
        elif event["id"] == "joint_state" and home is not None:
            qpos = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(-1)
            n = min(len(qpos), len(home))
            home_error = float(np.abs(qpos[:n] - np.asarray(home[:n], np.float32)).max())
        elif event["id"] == "oracle_state":
            sim_time_ns = int(metadata.get("sim_time_ns", 0))
            latest_oracle_ns = max(latest_oracle_ns, sim_time_ns)
            if goal is None or sim_time_ns <= goal_barrier_ns:
                continue
            state = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            ).reshape(-1)
            if goal_t0_ns is None:
                goal_t0_ns = sim_time_ns
                initial = [state[i * 7 : i * 7 + 3].tolist() for i in range(len(MED_NAMES))]
                cfg = build_judge_cfg(
                    physics, meds, embodiment, float(goal["timeout_s"]), initial, home_error
                )
            if home is not None and home_error != cfg.robot_home_error_rad:
                cfg = replace(cfg, robot_home_error_rad=home_error)
            t = (sim_time_ns - goal_t0_ns) / 1e9
            status, failure = judge(state, target_idx, t, cfg)
            if status in ("success", "fail"):
                result = {
                    "status": status,
                    "failure": failure,
                    "t_end": t,
                    "seed": int(goal.get("seed", 0)),
                    "goal_id": goal_id,
                    "verifier": "oracle",
                }
                result_seq += 1
                node.send_output(
                    "episode_result",
                    pa.array([json.dumps(result)]),
                    # TC-2: mandatory keys on every output
                    metadata={
                        "goal_id": goal_id,
                        "sim_time_ns": sim_time_ns,
                        "env_id": 0,
                        "seq": result_seq,
                    },
                )
                goal = None  # one result per goal (TC-7)


if __name__ == "__main__":
    main()
