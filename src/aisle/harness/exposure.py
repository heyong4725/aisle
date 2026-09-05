"""Safety exposure ledger derived from retained run traces (SFE-1..SFE-5,
SFE-7, SFE-13, SFE-15; issue #351).

The guard reasons about commands and limits; the verifier observes semantic
outcomes after they happen. This module joins the two into one versioned,
append-only ledger with frozen exposure rules, so a zero can carry its
denominator and a clamp can never be described as prevention of a
wrong-object event. Every row names its evidence layer (SFE-1) and evidence
kind; simulation rows stay simulation rows (SFE-15).

Pure derivation over decoded trace rows (CON-12): `derive_ledger` reads no
files and no clock; `ledger_for_run` is the thin Arrow adapter.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

LEDGER_SCHEMA = "aisle.safety-exposure.ledger.v1"
LAYERS = (
    "declared_topology",
    "gateway_kinematic_enforcement",
    "observed_kinematic_outcome",
    "verifier_semantic_detection",
    "semantic_authorization",
)
EVIDENCE_KINDS = ("unit", "synthetic", "simulation", "hardware")
DECISIONS = ("pass", "clamp", "refuse", "hold")
HOLD_REASONS = {"wall_timeout", "malformed"}
GRIPPER_CLOSE_THRESHOLD = 0.5  # gripper_cmd is 0 open .. 1 closed (limits.toml)
SOURCE_CLASSES = ("classical", "learned", "hybrid", "unknown")


class ExposureError(Exception):
    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.details = details or []


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _decision(cmd: np.ndarray, safe: np.ndarray, reasons: list[str]) -> str:
    if reasons and set(reasons) & HOLD_REASONS:
        return "hold"
    return "pass" if np.allclose(cmd, safe, atol=1e-7) else "clamp"


def _episode_windows(goals: list[dict], results: list[dict]) -> list[dict]:
    """SFE-3: an episode begins at its retained goal assignment whether or
    not a result ever arrives."""
    by_goal = {json.loads(r["text"])["goal_id"]: json.loads(r["text"]) for r in results}
    episodes = []
    for index, row in enumerate(goals):
        goal = json.loads(row["text"])
        goal_id = f"ep-{index:04d}"
        start = int(goal.get("reset_sim_ns", row["sim_time_ns"]))
        end = (
            int(
                json.loads(goals[index + 1]["text"]).get(
                    "reset_sim_ns", goals[index + 1]["sim_time_ns"]
                )
            )
            if index + 1 < len(goals)
            else None
        )
        episodes.append(
            {
                "goal_id": goal_id,
                "seed": int(goal.get("seed", 0)),
                "target_med": goal["target_med"],
                "timeout_s": float(goal.get("timeout_s", 0.0)),
                "start_ns": start,
                "end_ns": end,
                "result": by_goal.get(goal_id),
            }
        )
    return episodes


def _in_window(stamp: int, episode: dict) -> bool:
    return stamp >= episode["start_ns"] and (episode["end_ns"] is None or stamp < episode["end_ns"])


def _proposals(
    channel: str, cmds: list[dict], safes: list[dict], violations: list[dict], limits, fk
) -> list[dict]:
    """SFE-4: one proposal per received request, exactly one decision, one
    receipt; a multi-joint clamp stays one proposal."""
    if len(cmds) != len(safes):
        raise ExposureError(
            "proposal/decision reconciliation failed",
            [f"{channel}: {len(cmds)} proposals vs {len(safes)} gateway outputs"],
        )
    by_stamp: dict[int, list[dict]] = {}
    for v in violations:
        payload = json.loads(v["text"]) if v.get("text") else {}
        by_stamp.setdefault(int(v["sim_time_ns"]), []).append(payload)
    rows = []
    for cmd, safe in zip(cmds, safes, strict=True):
        stamp = int(cmd["sim_time_ns"])
        c = np.asarray(cmd["data"], dtype=np.float32)
        s = np.asarray(safe["data"], dtype=np.float32)
        found = by_stamp.get(stamp, [])
        reasons = [p.get("reason", "unknown") for p in found]
        decision = _decision(c, s, reasons)
        malformed = "malformed" in reasons or not np.all(np.isfinite(c))
        row = {
            "proposal_id": f"{channel}-{int(cmd['seq'])}",
            "channel": channel,
            "sim_time_ns": stamp,
            "seq": int(cmd["seq"]),
            "request_hash": "sha256:" + hashlib.sha256(c.tobytes()).hexdigest(),
            "output_hash": "sha256:" + hashlib.sha256(s.tobytes()).hexdigest(),
            "valid": not malformed,
            "decision": decision,
            "reasons": sorted(set(reasons)),
            "max_correction": float(np.max(np.abs(s - c))) if len(c) == len(s) else None,
            "receipt_id": f"{channel}-safe-{int(safe['seq'])}",
            "workspace_proposed_out": "workspace" in reasons,
        }
        if channel == "joint_cmd" and fk is not None:
            ee = fk(s[: limits.n_arm_dof], limits)
            row["receipt_out_of_envelope"] = not all(
                limits.workspace_min[i] <= ee[i] <= limits.workspace_max[i] for i in range(3)
            )
        rows.append(row)
    return rows


def _attempts(episode: dict, joint_rows: list[dict], gripper_rows: list[dict]) -> list[dict]:
    """SFE-3 manipulation attempts: open on the first close proposal after an
    open state with at least one arm-motion proposal since the previous
    boundary; close on the next open, reset, terminal result, or timeout."""
    arm_stamps = sorted(
        r["sim_time_ns"] for r in joint_rows if _in_window(r["sim_time_ns"], episode)
    )
    grips = sorted(
        (r for r in gripper_rows if _in_window(r["sim_time_ns"], episode)),
        key=lambda r: r["sim_time_ns"],
    )
    attempts, boundary, open_state, current = [], episode["start_ns"], True, None
    for g in grips:
        value = float(np.asarray(g["data"]).reshape(-1)[0])
        closing = value >= GRIPPER_CLOSE_THRESHOLD
        if current is None and open_state and closing:
            moved = any(boundary <= s <= g["sim_time_ns"] for s in arm_stamps)
            if moved:
                current = {"open_ns": int(g["sim_time_ns"]), "close_ns": None, "complete": False}
            open_state = False
        elif current is not None and not closing:
            current.update(close_ns=int(g["sim_time_ns"]), complete=True, close_reason="open")
            attempts.append(current)
            boundary, current, open_state = int(g["sim_time_ns"]), None, True
        elif not closing:
            open_state = True
    if current is not None:
        result = episode["result"]
        current.update(
            close_ns=episode["end_ns"],
            complete=False,
            close_reason="terminal_result" if result else "episode_end",
        )
        attempts.append(current)
    for index, a in enumerate(attempts):
        a["attempt_id"] = f"{episode['goal_id']}-att-{index}"
    return attempts


def _deliveries(
    episode: dict, oracle_rows: list[dict], med_names, judge_cfg_factory, verifier
) -> dict:
    """SFE-3/SFE-5: deliveries are verifier-observed tray entries under the
    frozen judge geometry, deduplicated per object/episode/entry; the
    collision proxy is pose displacement, not a contact instrument."""
    samples = sorted(
        (r for r in oracle_rows if _in_window(r["sim_time_ns"], episode)),
        key=lambda r: r["sim_time_ns"],
    )
    barrier = verifier.initial_capture_barrier(0, episode["start_ns"])
    samples = [s for s in samples if s["sim_time_ns"] > barrier]
    if not samples:
        return {"deliveries": [], "collisions": [], "started": False}
    initial_state = np.asarray(samples[0]["data"], dtype=np.float32)
    initial = [initial_state[i * 7 : i * 7 + 3].tolist() for i in range(len(med_names))]
    cfg = judge_cfg_factory(episode["timeout_s"], initial)
    target = med_names.index(episode["target_med"])
    result = episode["result"] or {}
    verdict_ns = (
        samples[0]["sim_time_ns"] + int(float(result["t_end"]) * 1e9)
        if result.get("t_end") is not None
        else None
    )
    inside_prev = [False] * len(med_names)
    knocked = [False] * len(med_names)
    deliveries, collisions = [], []
    for s in samples:
        state = np.asarray(s["data"], dtype=np.float32)
        for idx in range(len(med_names)):
            pos, _ = verifier._box_pose(state, idx)
            inside = bool(verifier._center_inside_tray(pos, cfg))
            if inside and not inside_prev[idx]:
                deliveries.append(
                    {
                        "delivery_id": (
                            f"{episode['goal_id']}-box{idx}-entry"
                            f"{sum(1 for d in deliveries if d['box'] == idx)}"
                        ),
                        "box": idx,
                        "identity": med_names[idx],
                        "sim_time_ns": int(s["sim_time_ns"]),
                        "wrong_object": idx != target,
                        "after_terminal_result": verdict_ns is not None
                        and int(s["sim_time_ns"]) > verdict_ns,
                    }
                )
            inside_prev[idx] = inside
            if idx != target and not knocked[idx]:
                magnitude = float(np.linalg.norm(pos - np.asarray(initial[idx], dtype=np.float32)))
                if magnitude > cfg.knock_epsilon_m:
                    knocked[idx] = True
                    collisions.append(
                        {
                            "event_id": f"{episode['goal_id']}-knock-box{idx}",
                            "instrument": "oracle_pose_displacement_proxy",
                            "threshold_m": float(cfg.knock_epsilon_m),
                            "bodies": ["robot_or_target", med_names[idx]],
                            "magnitude_m": magnitude,
                            "sim_time_ns": int(s["sim_time_ns"]),
                            "contact_instrumentation": "unmeasured",
                            "after_terminal_result": verdict_ns is not None
                            and int(s["sim_time_ns"]) > verdict_ns,
                        }
                    )
    return {"deliveries": deliveries, "collisions": collisions, "started": True}


def _observed_envelope(joint_state: list[dict], limits, fk) -> list[dict]:
    """SFE-5 observed out-of-envelope state: FK of the robot's own joint
    state, run-length encoded into events with duration."""
    events, current = [], None
    for row in sorted(joint_state, key=lambda r: r["sim_time_ns"]):
        q = np.asarray(row["data"], dtype=np.float32)
        ee = fk(q[: limits.n_arm_dof], limits)
        out = not all(limits.workspace_min[i] <= ee[i] <= limits.workspace_max[i] for i in range(3))
        stamp = int(row["sim_time_ns"])
        if out and current is None:
            current = {"start_ns": stamp, "end_ns": stamp, "max_excursion_m": 0.0}
        if out:
            excursion = max(
                max(limits.workspace_min[i] - ee[i], ee[i] - limits.workspace_max[i], 0.0)
                for i in range(3)
            )
            current["end_ns"] = stamp
            current["max_excursion_m"] = max(current["max_excursion_m"], float(excursion))
        elif current is not None:
            events.append(current)
            current = None
    if current is not None:
        events.append(current)
    return events


def derive_ledger(
    run_id: str,
    rows: dict[str, list[dict]],
    *,
    limits,
    fk,
    med_names,
    judge_cfg_factory,
    verifier,
    source_map: dict[str, dict],
    producers: dict[str, str],
    instrument_hash: str,
    trace_hashes: dict[str, str],
    campaign_id: str,
    evidence_kind: str = "simulation",
) -> dict[str, Any]:
    """The versioned ledger for one run. `producers` maps channel -> content
    hash of the producing node; `source_map` classifies those hashes
    (SFE-7: by hash and role, never by filename)."""
    if evidence_kind not in EVIDENCE_KINDS:
        raise ExposureError("unknown evidence kind", [evidence_kind])
    episodes = _episode_windows(rows.get("episode_goal", []), rows.get("episode_result", []))
    if not episodes:
        raise ExposureError("no retained goal assignments", [run_id])
    joint = _proposals(
        "joint_cmd",
        rows.get("joint_cmd", []),
        rows.get("joint_cmd_safe", []),
        rows.get("violation", []),
        limits,
        fk,
    )
    gripper = _proposals(
        "gripper_cmd",
        rows.get("gripper_cmd", []),
        rows.get("gripper_cmd_safe", []),
        [],
        limits,
        None,
    )
    for channel, plist in (("joint_cmd", joint), ("gripper_cmd", gripper)):
        producer = producers.get(channel)
        source = source_map.get(producer or "", {})
        for p in plist:
            p["producer_hash"] = producer
            p["controller_class"] = source.get("class", "unknown")
    episode_rows = []
    for episode in episodes:
        semantic = _deliveries(
            episode, rows.get("oracle_state", []), med_names, judge_cfg_factory, verifier
        )
        proposals_in = [p for p in joint + gripper if _in_window(p["sim_time_ns"], episode)]
        episode_rows.append(
            {
                **{k: v for k, v in episode.items() if k != "result"},
                "randomized": True,
                "started": semantic["started"],
                "completed": episode["result"] is not None,
                "included": semantic["started"],
                "exclusion_reason": None
                if semantic["started"]
                else "no oracle sample after reset barrier",
                "result": episode["result"],
                "attempts": _attempts(
                    episode, rows.get("joint_cmd", []), rows.get("gripper_cmd", [])
                ),
                "deliveries": semantic["deliveries"],
                "wrong_object_events": [d for d in semantic["deliveries"] if d["wrong_object"]],
                "collisions": semantic["collisions"],
                "proposal_ids": [p["proposal_id"] for p in proposals_in],
            }
        )
    return {
        "schema_version": LEDGER_SCHEMA,
        "campaign_id": campaign_id,
        "session_id": run_id,
        "environment": "env-0",
        "evidence_kind": evidence_kind,
        "instrument_hash": instrument_hash,
        "trace_hashes": trace_hashes,
        "layers": {
            "gateway_kinematic_enforcement": "proposals",
            "observed_kinematic_outcome": "observed_envelope, collisions(proxy)",
            "verifier_semantic_detection": "deliveries, wrong_object_events",
        },
        "producers": producers,
        "source_map": source_map,
        "episodes": episode_rows,
        "proposals": joint + gripper,
        "observed_envelope": _observed_envelope(rows.get("joint_state", []), limits, fk),
        "contact_instrumentation": "unmeasured",
    }


def load_run_rows(run_dir: Path) -> tuple[dict[str, list[dict]], dict[str, str]]:
    """Arrow adapter: decode the topics the ledger joins; hash each trace."""
    from aisle.harness.traces import _load, resolve_endpoint

    wanted = {
        "episode_goal": "rollout-client",
        "episode_result": "verifier-oracle",
        "joint_cmd": None,
        "joint_cmd_safe": "budget-guard",
        "gripper_cmd": None,
        "gripper_cmd_safe": "budget-guard",
        "violation": "budget-guard",
        "oracle_state": "dora-genesis",
        "joint_state": "dora-genesis",
    }
    rows, hashes = {}, {}
    for topic, node in wanted.items():
        try:
            table = _load(run_dir, topic, node)
        except (FileNotFoundError, OSError):
            rows[topic] = []
            continue
        rows[topic] = table.to_pylist()
        hashes[topic] = sha256_file(resolve_endpoint(run_dir, topic, node))
    return rows, hashes


def ledger_for_run(
    run_dir: Path, *, campaign_id: str, source_map: dict[str, dict]
) -> dict[str, Any]:
    import yaml

    from aisle.nodes import budget_guard
    from aisle.scenes.pharmacy import MED_NAMES, load_meds, load_physics
    from aisle.verifier import oracle

    manifest = json.loads((run_dir / "manifest.json").read_text())
    embodiment = manifest.get("embodiment", "franka")
    limits = budget_guard.load_limits(embodiment)
    physics, meds = load_physics(), load_meds()

    def judge_cfg_factory(timeout_s: float, initial: list) -> Any:
        return oracle.build_judge_cfg(physics, meds, embodiment, timeout_s, initial, 0.0)

    graph = yaml.safe_load((run_dir / "graph.yaml").read_text())
    producers = {}
    for node in graph.get("nodes", []):
        outputs = node.get("outputs") or []
        for channel in ("joint_cmd", "gripper_cmd"):
            if channel in outputs and node["id"] != "budget-guard":
                path = (run_dir / "graph.yaml").parent / node["path"]
                producers[channel] = sha256_file(path.resolve()) if path.exists() else None
    rows, hashes = load_run_rows(run_dir)
    classes = source_map.get("classes", source_map)
    return derive_ledger(
        manifest.get("run_id", run_dir.name),
        rows,
        limits=limits,
        fk=budget_guard.fk_ee_pos,
        med_names=list(MED_NAMES),
        judge_cfg_factory=judge_cfg_factory,
        verifier=oracle,
        source_map=classes,
        producers=producers,
        instrument_hash=sha256_file(Path(__file__)),
        trace_hashes=hashes,
        campaign_id=campaign_id,
    )
