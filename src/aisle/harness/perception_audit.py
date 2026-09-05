"""Independent perception audit over a frozen frame corpus with hidden
truth (BND-5, BND-6, BND-7; SPEC 490, issue #346).

The perception process (OWLv2 identity plus depth back-projection, the
same code the L2 node runs) never sees the truth; only the audit scorer
does. The corpus is built from a recorded run: captured overhead frames,
the oracle_state trace at the same stamps (verifier-only, so audit-only
here), the goal assignments, and the bridge calibration. The envelope
(BND-6) freezes the identity vocabulary, localization tolerance,
confidence and margin thresholds, camera assumptions, strata ranges,
synchronization rule, and missing-data behaviour; thresholds may be tuned
on the calibration split only. Eligibility (BND-7) is decided per stratum
with exact lower bounds against pre-registered floors; an aggregate never
masks a failed stratum.

Pure over decoded rows with an injected detector (CON-12); the Arrow and
npz adapters are thin.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from aisle.harness.benchmark_statistics import clopper_pearson_interval

ENVELOPE_SCHEMA = "aisle.perception-audit.envelope.v1"
CORPUS_SCHEMA = "aisle.perception-audit.corpus.v1"
REPORT_SCHEMA = "aisle.perception-audit.report.v1"
FAILURE_TAXONOMY = (
    "correct",
    "wrong_identity",
    "refused",
    "no_detection",
    "localization_error",
    "missing_data",
)


class PerceptionAuditError(Exception):
    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.details = details or []


def content_hash(value: Any) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
    )


def default_envelope(med_names: list[str]) -> dict:
    """BND-6 frozen envelope for the pharmacy L2 rung."""
    return {
        "schema_version": ENVELOPE_SCHEMA,
        "identity_vocabulary": list(med_names),
        "margin_floor": 0.01,
        "confidence_floor": 0.0,
        "localization_tolerance_m": 0.03,
        "latency_ceiling_s": 5.0,
        "refusal_availability_limit": 0.5,
        "accuracy_floor": 0.90,
        "camera": {"role": "overhead", "resolution": [480, 640], "depth_required": True},
        "strata": ["target_class", "seed_parity", "sensor"],
        "synchronization": (
            "rgb and depth share one sim_time_ns; truth is the oracle sample at the same stamp"
        ),
        "missing_data": (
            "a frame without depth, truth, or calibration is a missing_data row and fails "
            "the audit if any stratum is empty"
        ),
        "tuning_rule": (
            "thresholds may change only from calibration-split records; evaluation records "
            "never tune"
        ),
        "multiplicity": "every required stratum must pass its floor; no pooling rescue",
    }


def validate_envelope(envelope: dict) -> None:
    required = {
        "schema_version",
        "identity_vocabulary",
        "margin_floor",
        "confidence_floor",
        "localization_tolerance_m",
        "latency_ceiling_s",
        "refusal_availability_limit",
        "accuracy_floor",
        "camera",
        "strata",
        "synchronization",
        "missing_data",
        "tuning_rule",
        "multiplicity",
    }
    missing = sorted(required - set(envelope))
    if missing or envelope.get("schema_version") != ENVELOPE_SCHEMA:
        raise PerceptionAuditError("perception envelope incomplete", missing)


# ------------------------------------------------------------- corpus


def build_corpus(
    *,
    run_id: str,
    frames: dict[str, dict[int, dict[str, np.ndarray]]],
    oracle_rows: list[dict],
    goals: list[dict],
    calibration: dict,
    med_names: list[str],
    split_rule: str = "even seeds calibrate, odd seeds evaluate",
) -> dict:
    """BND-5: one record per captured frame with the hidden truth attached
    for the scorer only. Frames and truth are hashed; the split is by seed
    content, disjoint by construction."""
    if not frames:
        raise PerceptionAuditError("no captured frames", [run_id])
    by_stamp = {int(r["sim_time_ns"]): np.asarray(r["data"], dtype=np.float32) for r in oracle_rows}
    windows = []
    for index, goal in enumerate(goals):
        g = goal if isinstance(goal.get("target_med"), str) else json.loads(goal["text"])
        start = int(g.get("reset_sim_ns", 0))
        end = (
            int(json.loads(goals[index + 1]["text"]).get("reset_sim_ns", 0))
            if index + 1 < len(goals) and "text" in goals[index + 1]
            else None
        )
        windows.append((start, end, g))
    records = []
    for camera, per_stamp in frames.items():
        for stamp, arrays in sorted(per_stamp.items()):
            goal = next((g for s, e, g in windows if stamp >= s and (e is None or stamp < e)), None)
            truth_stamps = [s for s in by_stamp if abs(s - stamp) <= 20_000_000]
            truth = (
                by_stamp[min(truth_stamps, key=lambda s: abs(s - stamp))] if truth_stamps else None
            )
            record = {
                "record_id": f"{run_id}-{camera}-{stamp}",
                "camera": camera,
                "sim_time_ns": stamp,
                "seed": int(goal.get("seed", -1)) if goal else None,
                "target": goal.get("target_med") if goal else None,
                "frame_hash": content_hash(
                    {
                        k: hashlib.sha256(np.ascontiguousarray(v).tobytes()).hexdigest()
                        for k, v in arrays.items()
                    }
                ),
                "has_depth": "depth" in arrays,
                "truth": None
                if truth is None or goal is None
                else {
                    "positions": {
                        name: truth[i * 7 : i * 7 + 3].tolist() for i, name in enumerate(med_names)
                    },
                    "target": goal.get("target_med"),
                },
                "strata": {
                    "target_class": goal.get("target_med") if goal else "none",
                    "seed_parity": "even" if goal and int(goal.get("seed", 0)) % 2 == 0 else "odd",
                    "sensor": camera,
                },
            }
            record["split"] = (
                "calibration" if record["strata"]["seed_parity"] == "even" else "evaluation"
            )
            records.append(record)
    return {
        "schema_version": CORPUS_SCHEMA,
        "run_id": run_id,
        "calibration": calibration,
        "split_rule": split_rule,
        "records": records,
        "corpus_hash": content_hash([r["frame_hash"] for r in records]),
    }


# ------------------------------------------------------------- scoring


def score_record(
    record: dict,
    arrays: dict[str, np.ndarray],
    *,
    envelope: dict,
    detector: Callable[[np.ndarray], list[dict]],
    localizer: Callable[[dict, np.ndarray, dict], list[float] | None],
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """The perception path sees rgb, depth, calibration; the truth is used
    only after the prediction is fixed."""
    from aisle.nodes.l2_pose import pick_target_detection
    from aisle.nodes.segmented_pose import PoseRefused

    truth = record.get("truth")
    if not record.get("has_depth") or truth is None or record.get("target") is None:
        return {
            "record_id": record["record_id"],
            "outcome": "missing_data",
            "prediction": None,
            "latency_s": None,
        }
    started = clock()
    detections = detector(arrays["rgb"])
    prediction: dict[str, Any] = {"detections": len(detections)}
    try:
        best = pick_target_detection(detections, record["target"], envelope["margin_floor"])
        prediction.update(
            {
                "identity": best["label"],
                "score": float(best["score"]),
                "margin": float(best["margin"]),
                "box": [float(v) for v in best["box"]],
            }
        )
        outcome = "correct"
    except PoseRefused as refused:
        prediction.update({"identity": None, "refusal": str(refused)[:80]})
        outcome = (
            "refused" if any(d["label"] == record["target"] for d in detections) else "no_detection"
        )
    position = None
    if outcome == "correct":
        position = localizer(best, arrays["depth"], record)
        prediction["position"] = position
    latency = clock() - started
    # truth is opened only now
    if outcome == "correct":
        rivals = [
            d
            for d in detections
            if d["label"] != record["target"] and d["score"] > prediction["score"]
        ]
        if rivals:
            outcome = "wrong_identity"
        elif position is not None:
            error = float(
                np.linalg.norm(
                    np.asarray(position) - np.asarray(truth["positions"][record["target"]])
                )
            )
            prediction["localization_error_m"] = error
            if error > envelope["localization_tolerance_m"]:
                outcome = "localization_error"
    return {
        "record_id": record["record_id"],
        "outcome": outcome,
        "prediction": prediction,
        "latency_s": latency,
    }


def audit(
    corpus: dict,
    envelope: dict,
    *,
    scored: list[dict],
    model_hashes: dict,
    confidence: float = 0.95,
) -> dict:
    """BND-5 / BND-7: per-stratum accuracy with exact lower bounds against
    the frozen floor, refusal availability, latency, taxonomy, split
    disjointness; missing strata fail; raw predictions retained."""
    validate_envelope(envelope)
    by_id = {r["record_id"]: r for r in corpus["records"]}
    if {s["record_id"] for s in scored} != set(by_id):
        raise PerceptionAuditError("scored rows do not match the corpus")
    splits = {"calibration": set(), "evaluation": set()}
    for r in corpus["records"]:
        splits[r["split"]].add(r["frame_hash"])
    if splits["calibration"] & splits["evaluation"]:
        raise PerceptionAuditError("calibration and evaluation frames overlap by content")
    evaluation = [s for s in scored if by_id[s["record_id"]]["split"] == "evaluation"]
    strata: dict[str, dict[str, dict]] = {}
    for axis in envelope["strata"]:
        strata[axis] = {}
        for s in evaluation:
            key = str(by_id[s["record_id"]]["strata"][axis])
            cell = strata[axis].setdefault(
                key, {"n": 0, "correct": 0, "refused": 0, "missing": 0, "taxonomy": {}}
            )
            cell["n"] += 1
            cell["correct"] += s["outcome"] == "correct"
            cell["refused"] += s["outcome"] in ("refused", "no_detection")
            cell["missing"] += s["outcome"] == "missing_data"
            cell["taxonomy"][s["outcome"]] = cell["taxonomy"].get(s["outcome"], 0) + 1
    failures = []
    for axis, cells in strata.items():
        if not cells:
            failures.append(f"stratum axis {axis} has no evaluation records")
        for key, cell in cells.items():
            usable = cell["n"] - cell["missing"]
            if usable == 0:
                failures.append(f"{axis}={key}: no usable records")
                continue
            interval = clopper_pearson_interval(
                cell["correct"], usable, confidence_level=confidence, sidedness="lower"
            )
            cell["accuracy"] = cell["correct"] / usable
            cell["accuracy_lower_bound"] = interval["lower"]
            cell["refusal_rate"] = cell["refused"] / usable
            cell["passes_floor"] = interval["lower"] >= envelope["accuracy_floor"]
            cell["refusal_within_limit"] = (
                cell["refusal_rate"] <= envelope["refusal_availability_limit"]
            )
            if not cell["passes_floor"] or not cell["refusal_within_limit"]:
                failures.append(
                    f"{axis}={key}: accuracy lower bound {interval['lower']:.3f} or refusal "
                    f"{cell['refusal_rate']:.2f} outside the envelope"
                )
    latencies = [s["latency_s"] for s in evaluation if s["latency_s"] is not None]
    report = {
        "ok": not failures,
        "schema_version": REPORT_SCHEMA,
        "run_id": corpus["run_id"],
        "corpus_hash": corpus["corpus_hash"],
        "envelope_hash": content_hash(envelope),
        "model_hashes": model_hashes,
        "split_sizes": {k: len(v) for k, v in splits.items()},
        "evaluation_records": len(evaluation),
        "strata": strata,
        "latency_s": {
            "median": float(np.median(latencies)) if latencies else None,
            "max": max(latencies) if latencies else None,
            "ceiling": envelope["latency_ceiling_s"],
            "descriptive": True,
        },
        "taxonomy": {k: sum(1 for s in evaluation if s["outcome"] == k) for k in FAILURE_TAXONOMY},
        "eligibility": "perception_eligible" if not failures else "not_eligible",
        "failures": failures,
        "raw_predictions": scored,
        "wording": (
            "an oracle-scored audit of the perception path; it does not validate perception "
            "portability beyond this rendering and envelope (BND-3)"
        ),
    }
    report["report_hash"] = content_hash({k: v for k, v in report.items() if k != "report_hash"})
    return report


# ------------------------------------------------------------- adapters


def corpus_from_run(
    run_dir: Path, *, med_names: list[str]
) -> tuple[dict, dict[str, dict[int, dict[str, np.ndarray]]]]:
    from aisle.harness.trace_recorder import load_frames
    from aisle.harness.traces import _load

    frames = load_frames(run_dir / "traces")
    oracle = _load(run_dir, "oracle_state", "dora-genesis").to_pylist()
    goals = _load(run_dir, "episode_goal", "rollout-client").to_pylist()
    info = _load(run_dir, "bridge_info", "dora-genesis").to_pylist()
    calibration = json.loads(info[0]["text"])["calibration"] if info else {}
    manifest = (
        json.loads((run_dir / "manifest.json").read_text())
        if (run_dir / "manifest.json").exists()
        else {}
    )
    corpus = build_corpus(
        run_id=manifest.get("run_id", run_dir.name),
        frames=frames,
        oracle_rows=oracle,
        goals=goals,
        calibration=calibration,
        med_names=med_names,
    )
    return corpus, frames


def real_localizer(calibration: dict, meds: dict) -> Callable:
    from aisle.nodes.l2_pose import _bbox_mask
    from aisle.nodes.segmented_pose import PoseRefused, estimate_pose
    from aisle.verifier.stages import backproject_overhead

    def localize(best: dict, depth: np.ndarray, record: dict) -> list[float] | None:
        size = meds[record["target"]]["size"]
        try:
            est = estimate_pose(
                _bbox_mask(depth.shape, best["box"]),
                depth,
                [1],
                float(size[2]),
                lambda d, px: backproject_overhead(d, calibration, px),
                footprint_m=tuple(size[:2]),
            )
        except PoseRefused:
            return None
        return [float(v) for v in est["pos"]]

    return localize
