"""VER-6 verifier-fidelity job (SPEC 040; design doc §11.2, ablation A7).

Replays N episodes through BOTH verifiers and reports, with EXPLICIT
denominators (VER-6):

    agreement          = matching success bits / N
    false_success_rate = |realistic pass AND oracle fail| / |oracle fail|
    false_fail_rate    = |realistic fail AND oracle pass| / |oracle pass|

false-success is the dangerous direction for A7: the loop would be
driven by a verifier that calls failures successes. An empty
denominator yields null (never 0.0 — "no oracle failures" is not "no
false successes"), and N=0 refuses (CON-8).

Per-episode disagreements carry the VER-14 stage record, so every
disagreement is attributable to a stage — the decision evidence for the
D4 `depth_wrist` follow-up.

CON-8: single JSON object on stdout, logs to stderr, exit 0 iff ok.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aisle.verifier.realistic import SIDECAR_NAME, STAGES


def rate(numerator: int, denominator: int) -> float | None:
    """VER-6: null on an empty denominator — a rate over nothing is not
    zero, and reporting 0.0 would read as evidence of safety."""
    if denominator == 0:
        return None
    return numerator / denominator


def compare(oracle: dict[str, bool], realistic: dict[str, bool]) -> dict:
    """The three VER-6 scalars plus the four underlying counts."""
    goal_ids = sorted(set(oracle) & set(realistic))
    if not goal_ids:
        raise ValueError("no episodes to compare (VER-6 refuses N=0)")
    missing = sorted(set(oracle) ^ set(realistic))
    agree = sum(1 for g in goal_ids if oracle[g] == realistic[g])
    oracle_fail = [g for g in goal_ids if not oracle[g]]
    oracle_pass = [g for g in goal_ids if oracle[g]]
    false_success = [g for g in oracle_fail if realistic[g]]
    false_fail = [g for g in oracle_pass if not realistic[g]]
    return {
        "n": len(goal_ids),
        "counts": {
            "agree": agree,
            "oracle_pass": len(oracle_pass),
            "oracle_fail": len(oracle_fail),
            "false_success": len(false_success),
            "false_fail": len(false_fail),
        },
        "agreement": rate(agree, len(goal_ids)),
        "false_success_rate": rate(len(false_success), len(oracle_fail)),
        "false_fail_rate": rate(len(false_fail), len(oracle_pass)),
        "false_success_ids": false_success,
        "false_fail_ids": false_fail,
        "unpaired_goal_ids": missing,
    }


def stage_attribution(records: dict[str, dict], disagreement_ids: list[str]) -> dict[str, int]:
    """Which stage produced each disagreement (VER-14 -> the D4
    trigger): counts of non-passing stages across the disagreeing
    episodes. A single episode can implicate several stages."""
    counts = dict.fromkeys(STAGES, 0)
    for goal_id in disagreement_ids:
        stages = (records.get(goal_id) or {}).get("stages") or {}
        for stage in STAGES:
            if (stages.get(stage) or {}).get("vote", "pass") != "pass":
                counts[stage] += 1
    return counts


def load_sidecar(run_dir: Path) -> dict[str, dict]:
    """VER-14 records keyed by goal_id (last write per episode wins)."""
    path = Path(run_dir) / SIDECAR_NAME
    if not path.exists():
        return {}
    records = {}
    for line in path.read_text().splitlines():
        if line.strip():
            record = json.loads(line)
            records[record["goal_id"]] = record
    return records


def load_oracle_results(run_dir: Path) -> dict[str, bool]:
    """Oracle success bits from the rollout's episodes.jsonl."""
    path = Path(run_dir) / "episodes.jsonl"
    results = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        episode = json.loads(line)
        goal_id = episode.get("goal_id") or f"ep-{episode['seed']:04d}"
        results[goal_id] = episode.get("status") == "success"
    return results


def fidelity_report(run_dir: Path) -> dict:
    """The VER-6 report for one run directory holding both verdicts."""
    records = load_sidecar(run_dir)
    realistic = {g: bool(r.get("success")) for g, r in records.items()}
    oracle = load_oracle_results(run_dir)
    report = compare(oracle, realistic)
    report["stage_attribution"] = stage_attribution(
        records, report["false_success_ids"] + report["false_fail_ids"]
    )
    report["run_dir"] = str(run_dir)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = fidelity_report(args.run_dir)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"ok": True, **report}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
