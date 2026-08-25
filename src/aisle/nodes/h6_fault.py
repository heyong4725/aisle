"""H6 fault hooks (ADR-h6-operation-protocol).

Env-triggered degradations for the H6 (Operation) campaign: the
injector arms exactly ONE via `AISLE_H6_FAULT` on the node's graph
entry (HAR-10 swap), so the injection is validated, logged, and
mechanically identical to the repair the agent is expected to make.
Absent env = behavior unchanged (pinned by tests/unit/test_h6_fault.py);
an unrecognized or wrong-node value refuses loudly at startup — an
injector bug must never run silently unfaulted. Every fault degrades
task success; none can produce a wrong-medicine delivery (the pharmacy
asymmetry binds the experimenter too)."""

from __future__ import annotations

import os
from collections.abc import Mapping

import numpy as np

POSE_BIAS_M = 0.018
GRASP_HIGH_M = 0.025
TRAJ_SHORT_FRACTION = 0.7

NODE_FAULTS: dict[str, frozenset[str]] = {
    "segmented-pose": frozenset({"pose_bias"}),
    "grasp-planner-topdown": frozenset({"grasp_high"}),
    "ik-trajectory": frozenset({"traj_short"}),
}


def armed_fault(node_id: str, env: Mapping[str, str] | None = None) -> str | None:
    """The fault this node runs under, or None. Raises on a value the
    node does not recognize (including another node's fault)."""
    mapping = os.environ if env is None else env
    value = (mapping.get("AISLE_H6_FAULT") or "").strip()
    if not value:
        return None
    recognized = NODE_FAULTS.get(node_id, frozenset())
    if value not in recognized:
        raise RuntimeError(
            f"AISLE_H6_FAULT={value!r} is not a fault of {node_id} "
            f"(recognized: {sorted(recognized)}; ADR-h6-operation-protocol)"
        )
    return value


def bias_pose(estimate: dict, bias_m: float = POSE_BIAS_M) -> dict:
    """F1: the target estimate shifted +x by bias_m; a new dict."""
    pos = estimate["pos"]
    return {**estimate, "pos": [pos[0] + bias_m, pos[1], pos[2]]}


def raise_grasp(grasp_pose7: np.ndarray, lift_m: float = GRASP_HIGH_M) -> np.ndarray:
    """F2: the grasp TCP lifted +z by lift_m; a new array."""
    pose = np.asarray(grasp_pose7, dtype=np.float32).reshape(7).copy()
    pose[2] += lift_m
    return pose


def plan_waypoint_cap(stages, fraction: float = TRAJ_SHORT_FRACTION) -> int:
    """F3: the waypoint count after which the executor stalls."""
    total = sum(len(stage.path) for stage in stages)
    return max(1, int(total * fraction))
