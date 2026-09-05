"""Fixed-proposal guard ablation on a fake driver (SFE-9, SFE-10, SFE-11,
SFE-12; issue #351).

Each pair replays byte-identical proposals with identical contract
timestamps through two arms that differ only in gateway enforcement mode:

- `guard_on` applies the production clamp (`budget_guard.clamp_joint_cmd`,
  `clamp_gripper_cmd`) and forwards the safe command.
- `guard_observe_only` computes the identical would-have decision, logs it,
  and forwards each well-formed proposal unchanged. It exists only inside
  this evaluator with a fake driver (SFE-10): no participant process, no
  hardware.

The fake driver records what it receives; a frozen violation instrument
(the same limits) scores per-trace driver-received kinematic violations,
the primary endpoint (SFE-11). An emergency containment envelope, identical
across arms, invalidates a pair it touches. The trace is the experimental
unit (SFE-12). Pure and seeded (CON-5, CON-12).
"""

from __future__ import annotations

import hashlib
import json
import random
from typing import Any

import numpy as np

from aisle.harness.benchmark_statistics import clopper_pearson_interval
from aisle.nodes.budget_guard import clamp_gripper_cmd, clamp_joint_cmd, fk_ee_pos

CORPUS_SCHEMA = "aisle.held-command.corpus.v1"
RESULT_SCHEMA = "aisle.held-command.result.v1"
FAMILIES = (
    "legal_negative_control",
    "joint_position_limit",
    "joint_velocity_limit",
    "workspace_envelope",
    "gripper_limit",
    "held_motion_watchdog_silence",
)
ARMS = ("guard_on", "guard_observe_only")
CONTAINMENT_MARGIN_M = 0.15  # workspace AABB grown by this much, identical across arms;
# reachable by the Panda (a 0.5 m margin would make containment vacuous)


class HeldCommandError(Exception):
    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.details = details or []


def _hash(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _legal_walk(rng: random.Random, home: np.ndarray, limits, steps: int) -> list[list[float]]:
    """A random walk that never exceeds half the per-step velocity budget
    and stays well inside the joint range: legal by construction."""
    q = home.copy()
    arm = limits.n_arm_dof  # finger dofs stay at home: their range is 0.04 m
    lo = limits.q_min_arr[:arm] + 0.2
    hi = limits.q_max_arr[:arm] - 0.2
    out = []
    for _ in range(steps):
        delta = np.array([rng.uniform(-0.5, 0.5) for _ in range(arm)], dtype=np.float32)
        q = q.copy()
        q[:arm] = np.clip(q[:arm] + delta * limits.max_step_arr[:arm], lo, hi)
        out.append([float(v) for v in q])
    return out


def _trace(family: str, index: int, home: np.ndarray, limits, rng: random.Random) -> dict:
    dt = limits.cmd_dt_s
    steps = 20
    joints = _legal_walk(rng, home, limits, steps)
    gripper = [0.0] * steps
    stamps = [round(dt * (i + 1), 6) for i in range(steps)]
    expected = "legal"
    if family == "joint_position_limit":
        j = rng.randrange(limits.n_arm_dof)
        joints[10][j] = float(limits.q_max[j] + 0.3)  # beyond the range
        expected = "position"
    elif family == "joint_velocity_limit":
        j = rng.randrange(limits.n_arm_dof)
        joints[10][j] = float(joints[9][j] + 6.0 * limits.max_step_arr[j])
        joints[10][j] = float(min(joints[10][j], limits.q_max[j] - 0.1))
        expected = "velocity"
    elif family == "workspace_envelope":
        # stretch joint 1 (shoulder) forward until the flange leaves the AABB
        for k in range(10, steps):
            joints[k] = list(joints[9])
        q = np.asarray(joints[9], dtype=np.float32)
        for _ in range(200):
            q = q.copy()
            q[1] = min(q[1] + 0.02, limits.q_max[1] - 0.01)
            q[3] = max(q[3] - 0.02, limits.q_min[3] + 0.01)
            ee = fk_ee_pos(q[: limits.n_arm_dof], limits)
            if any(
                not limits.workspace_min[i] <= ee[i] <= limits.workspace_max[i] for i in range(3)
            ):
                break
        joints[10] = [float(v) for v in q]
        expected = "workspace"
    elif family == "gripper_limit":
        gripper[10] = 1.6  # beyond gripper_max
        expected = "gripper"
    elif family == "held_motion_watchdog_silence":
        # silence beyond the wall watchdog, then a motion proposal
        stamps[10:] = [round(limits.wall_timeout_s + 1.0 + dt * i, 6) for i in range(steps - 10)]
        joints[10][0] = float(joints[9][0] + 0.5 * limits.max_step_arr[0])
        expected = "wall_timeout"
    body = {
        "family": family,
        "initial_state": [float(v) for v in home],
        "stamps_s": stamps,
        "joint_proposals": joints,
        "gripper_proposals": gripper,
        "expected_class": expected,
        "declared_at_risk": expected != "legal",
    }
    return {"trace_id": f"{family}-{index:02d}", "trace_hash": _hash(body), **body}


def build_corpus(limits, *, embodiment: str, seed: int, per_family: int = 8) -> dict:
    """SFE-9 / SFE-11: a deterministic corpus covering every family, with
    legal negative controls and a held-motion watchdog trace."""
    rng = random.Random(seed)
    home = np.asarray(limits.fallback_qpos, dtype=np.float32)
    traces = [_trace(f, i, home, limits, rng) for f in FAMILIES for i in range(per_family)]
    corpus = {
        "schema_version": CORPUS_SCHEMA,
        "embodiment": embodiment,
        "seed": seed,
        "per_family": per_family,
        "families": list(FAMILIES),
        "containment_margin_m": CONTAINMENT_MARGIN_M,
        "traces": traces,
    }
    corpus["corpus_hash"] = _hash({k: v for k, v in corpus.items() if k != "corpus_hash"})
    return corpus


def _receipt_violations(received: list[np.ndarray], gripper: list[float], limits) -> dict:
    """The frozen violation instrument over what the driver received."""
    counts = {"position": 0, "velocity": 0, "workspace": 0, "gripper": 0}
    excursion, out_steps = 0.0, 0
    prev = np.asarray(limits.fallback_qpos, dtype=np.float32)
    prev_g = 0.0
    for q, g in zip(received, gripper, strict=True):
        if np.any(q < limits.q_min_arr - 1e-6) or np.any(q > limits.q_max_arr + 1e-6):
            counts["position"] += 1
        if np.any(np.abs(q - prev) > limits.max_step_arr + 1e-6):
            counts["velocity"] += 1
        ee = fk_ee_pos(q[: limits.n_arm_dof], limits)
        over = max(
            max(limits.workspace_min[i] - ee[i], ee[i] - limits.workspace_max[i], 0.0)
            for i in range(3)
        )
        if over > 0:
            counts["workspace"] += 1
            out_steps += 1
            excursion = max(excursion, float(over))
        if not limits.gripper_min <= g <= limits.gripper_max or abs(g - prev_g) > (
            limits.gripper_rate_max * limits.gripper_dt_s + 1e-6
        ):
            counts["gripper"] += 1
        prev, prev_g = q, g
    return {
        "by_class": counts,
        "total": sum(counts.values()),
        "out_of_envelope_steps": out_steps,
        "out_of_envelope_duration_s": out_steps * limits.cmd_dt_s,
        "max_excursion_m": excursion,
    }


def _containment_hit(q: np.ndarray, limits) -> bool:
    ee = fk_ee_pos(q[: limits.n_arm_dof], limits)
    return any(
        ee[i] < limits.workspace_min[i] - CONTAINMENT_MARGIN_M
        or ee[i] > limits.workspace_max[i] + CONTAINMENT_MARGIN_M
        for i in range(3)
    )


def replay_trace(trace: dict, arm: str, limits) -> dict:
    """One arm over one trace: decisions logged identically; the driver
    receives the safe command (guard_on) or the raw proposal (observe-only)."""
    if arm not in ARMS:
        raise HeldCommandError("unknown arm", [arm])
    last_safe = np.asarray(trace["initial_state"], dtype=np.float32)
    last_g = 0.0
    received, received_g, decisions, containment = [], [], [], False
    t0 = 0.0
    for stamp, joints, g in zip(
        trace["stamps_s"], trace["joint_proposals"], trace["gripper_proposals"], strict=True
    ):
        timed_out = (stamp - t0) > limits.wall_timeout_s
        cmd = np.asarray(joints, dtype=np.float32)
        safe, violations = clamp_joint_cmd(cmd, last_safe, limits, timed_out)
        safe_g, g_violations = clamp_gripper_cmd(float(g), last_g, limits, timed_out)
        decisions.append(
            {
                "stamp_s": stamp,
                "reasons": sorted({v["reason"] for v in violations + g_violations}),
                "intervened": bool(violations or g_violations),
            }
        )
        forward = safe if arm == "guard_on" else (cmd if np.all(np.isfinite(cmd)) else safe)
        forward_g = safe_g if arm == "guard_on" else float(g)
        if _containment_hit(forward, limits):
            containment = True
            forward = last_safe  # containment holds; the pair is invalid either way
        received.append(forward)
        received_g.append(forward_g)
        last_safe, last_g = safe, safe_g
    return {
        "arm": arm,
        "trace_id": trace["trace_id"],
        "decisions": decisions,
        "interventions": sum(1 for d in decisions if d["intervened"]),
        "driver_received": _receipt_violations(received, received_g, limits),
        "containment_activated": containment,
        "received_hash": _hash([[float(v) for v in q] for q in received]),
    }


def _bootstrap_mean(values: list[float], seed: int, replicates: int = 2000) -> dict:
    rng = random.Random(seed)
    if not values:
        return {"mean": None, "ci95": None}
    means = []
    for _ in range(replicates):
        sample = [rng.choice(values) for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    return {
        "mean": sum(values) / len(values),
        "ci95": [means[int(0.025 * replicates)], means[int(0.975 * replicates) - 1]],
        "replicates": replicates,
        "unit": "trace",
    }


def run_ablation(corpus: dict, limits, *, analysis_seed: int) -> dict:
    """SFE-12: every pair retained; paired effect per family and pooled over
    at-risk traces; legal traces changed by guard_on block the study."""
    if corpus.get("schema_version") != CORPUS_SCHEMA:
        raise HeldCommandError("unsupported corpus schema", [str(corpus.get("schema_version"))])
    body = {k: v for k, v in corpus.items() if k not in ("corpus_hash", "ok")}
    if corpus["corpus_hash"] != _hash(body):
        raise HeldCommandError("corpus hash does not match its content", [corpus["corpus_hash"]])
    pairs, blockers = [], []
    for trace in corpus["traces"]:
        if trace["trace_hash"] != _hash(
            {k: v for k, v in trace.items() if k not in ("trace_id", "trace_hash")}
        ):
            raise HeldCommandError("trace hash drift", [trace["trace_id"]])
        on = replay_trace(trace, "guard_on", limits)
        off = replay_trace(trace, "guard_observe_only", limits)
        if on["decisions"] != off["decisions"]:
            raise HeldCommandError("observe-only decision log diverged", [trace["trace_id"]])
        excluded = on["containment_activated"] or off["containment_activated"]
        legal_changed = trace["expected_class"] == "legal" and on["interventions"] > 0
        unsafe_receipt = on["driver_received"]["total"] > 0
        if legal_changed:
            blockers.append(f"legal trace altered by guard_on: {trace['trace_id']}")
        if unsafe_receipt:
            blockers.append(f"unsafe guard_on receipt: {trace['trace_id']}")
        pairs.append(
            {
                "trace_id": trace["trace_id"],
                "family": trace["family"],
                "expected_class": trace["expected_class"],
                "at_risk": trace["declared_at_risk"],
                "excluded": excluded,
                "exclusion_reason": "emergency containment activated" if excluded else None,
                "guard_on": on,
                "guard_observe_only": off,
                "paired_difference": on["driver_received"]["total"]
                - off["driver_received"]["total"],
                "any_violation": {
                    "guard_on": on["driver_received"]["total"] > 0,
                    "guard_observe_only": off["driver_received"]["total"] > 0,
                },
            }
        )
    included = [p for p in pairs if not p["excluded"]]
    at_risk = [p for p in included if p["at_risk"]]
    legal = [p for p in included if not p["at_risk"]]
    strata = {}
    for family in FAMILIES:
        rows = [p for p in included if p["family"] == family]
        strata[family] = {
            "pairs": len(rows),
            "guard_on_any_violation": sum(1 for p in rows if p["any_violation"]["guard_on"]),
            "observe_only_any_violation": sum(
                1 for p in rows if p["any_violation"]["guard_observe_only"]
            ),
            "paired_difference": _bootstrap_mean(
                [float(p["paired_difference"]) for p in rows],
                analysis_seed + FAMILIES.index(family),
            ),
        }
    n = len(at_risk)
    on_any = sum(1 for p in at_risk if p["any_violation"]["guard_on"])
    off_any = sum(1 for p in at_risk if p["any_violation"]["guard_observe_only"])
    result = {
        "ok": not blockers,
        "schema_version": RESULT_SCHEMA,
        "corpus_hash": corpus["corpus_hash"],
        "embodiment": corpus["embodiment"],
        "analysis_seed": analysis_seed,
        "evidence_kind": "simulation_fake_driver",
        "unit": "trace",
        "pairs": pairs,
        "flow": {
            "pairs": len(pairs),
            "included": len(included),
            "excluded": len(pairs) - len(included),
            "at_risk": n,
            "legal_controls": len(legal),
        },
        "primary": {
            "endpoint": "per-trace driver-received kinematic violations (at-risk traces)",
            "guard_on_any_violation": clopper_pearson_interval(on_any, n) if n else None,
            "observe_only_any_violation": clopper_pearson_interval(off_any, n) if n else None,
            "risk_difference_any_violation": (on_any - off_any) / n if n else None,
            "paired_difference_count": _bootstrap_mean(
                [float(p["paired_difference"]) for p in at_risk], analysis_seed
            ),
        },
        "secondary": {
            "legal_controls_altered_by_guard_on": sum(
                1 for p in legal if p["guard_on"]["interventions"]
            ),
            "intervention_false_positives": sum(p["guard_on"]["interventions"] for p in legal),
            "observe_only_out_of_envelope_duration_s": sum(
                p["guard_observe_only"]["driver_received"]["out_of_envelope_duration_s"]
                for p in at_risk
            ),
            "guard_on_out_of_envelope_duration_s": sum(
                p["guard_on"]["driver_received"]["out_of_envelope_duration_s"] for p in at_risk
            ),
            "watchdog_hold_pairs": sum(
                1
                for p in at_risk
                if any("wall_timeout" in d["reasons"] for d in p["guard_on"]["decisions"])
            ),
            "collisions": "unmeasured (fake driver, no contact instrument)",
        },
        "strata": strata,
        "blockers": blockers,
    }
    result["result_hash"] = _hash({k: v for k, v in result.items() if k != "result_hash"})
    return result
