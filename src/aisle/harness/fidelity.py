"""Verifier-agreement report over recorded verdicts (SPEC 040 VER-6 input side).

SCOPE — read this before citing VER-6. This module is the COMPARATOR
half of the fidelity job: given a run that already holds both verdicts,
it computes the agreement statistics, the per-episode disagreement
records (D5), and persists them into the run manifest.

It is NOT the replay driver, and it deliberately does not claim VER-6.
Replaying recorded episodes through the realistic verifier is currently
IMPOSSIBLE from run artifacts, which is a recorder limitation rather
than a scoping preference: `dora-genesis__rgb_overhead.arrow` and
`__depth_overhead.arrow` carry timestamps with NULL payloads (measured:
0 of 32070 and 0 of 16022 rows non-null), so the only surviving image
evidence is a lossy `overhead.mp4` and no depth at all. A faithful
replay therefore needs either (a) the recorder to persist lossless RGB
+ depth, or (b) the increment-1b dora node judging online and writing
the VER-14 sidecar as the episode runs. Either way this comparator is
what consumes the result.

Denominators are explicit (VER-6):
    agreement          = matching success bits / N
    false_success_rate = |realistic pass AND oracle fail| / |oracle fail|
    false_fail_rate    = |realistic fail AND oracle pass| / |oracle pass|

false-success is the dangerous direction for A7. An empty denominator
yields null (never 0.0 — "no oracle failures" is not "no false
successes"). Evidence is validated strictly and refuses rather than
being coerced into favourable numbers.

CON-8: single JSON object on stdout, logs to stderr, exit 0 iff ok.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aisle.verifier.realistic import SIDECAR_NAME, STAGES, StageVote, fuse

VALID_VOTES = ("pass", "fail", "error")
# stages whose records must carry a measurement when they voted at all
MEASURED_STAGES = ("calibration", "containment", "upright", "home")


class EvidenceError(ValueError):
    """Recorded evidence is missing, malformed, or self-inconsistent."""


def rate(numerator: int, denominator: int) -> float | None:
    """VER-6: null on an empty denominator — a rate over nothing is not
    zero, and reporting 0.0 would read as evidence of safety."""
    if denominator == 0:
        return None
    return numerator / denominator


def validate_sidecar_record(record: dict) -> bool:
    """Strictly validate one VER-14 record and return its success bit.

    Invalid evidence REFUSES (PR #102 review): a missing stage vote used
    to default to `pass` in attribution, and `bool("false")` silently
    turned the JSON string into True. Every stage must be present with a
    valid vote, the identity stages must carry their frame timeline, the
    measured stages must carry a measurement when they voted, and
    `success` must be a real JSON Boolean that AGREES with fusing the
    recorded votes."""
    goal_id = record.get("goal_id")
    if not isinstance(goal_id, str) or not goal_id:
        raise EvidenceError(f"sidecar record has no goal_id: {record!r:.120}")
    stages = record.get("stages")
    if not isinstance(stages, dict):
        raise EvidenceError(f"{goal_id}: stages is {type(stages).__name__}, not an object")
    votes = {}
    for stage in STAGES:
        entry = stages.get(stage)
        if not isinstance(entry, dict):
            raise EvidenceError(f"{goal_id}: stage {stage!r} missing from the record")
        vote = entry.get("vote")
        if vote not in VALID_VOTES:
            raise EvidenceError(f"{goal_id}: stage {stage!r} has invalid vote {vote!r}")
        if stage.startswith("identity_") and not isinstance(entry.get("frames"), list):
            raise EvidenceError(f"{goal_id}: stage {stage!r} has no frame timeline (VER-14)")
        if stage in MEASURED_STAGES and vote != "error" and "measurement" not in entry:
            raise EvidenceError(f"{goal_id}: stage {stage!r} voted {vote!r} with no measurement")
        votes[stage] = StageVote(vote)
    success = record.get("success")
    if not isinstance(success, bool):
        raise EvidenceError(
            f"{goal_id}: success is {type(success).__name__} ({success!r}), not a JSON boolean"
        )
    fused = fuse(votes)
    if fused != success:
        raise EvidenceError(
            f"{goal_id}: recorded success {success} disagrees with fusing its own stages ({fused})"
        )
    return success


def compare(oracle: dict[str, bool], realistic: dict[str, bool]) -> dict:
    """The three VER-6 scalars plus the four underlying counts.

    Requires IDENTICAL non-empty goal-id sets (PR #102 review): scoring
    the intersection let a realistic crash on the hard episodes vanish
    from every denominator while the CLI still returned ok."""
    if not oracle or not realistic:
        raise EvidenceError("no episodes to compare (VER-6 refuses N=0)")
    only_oracle = sorted(set(oracle) - set(realistic))
    only_realistic = sorted(set(realistic) - set(oracle))
    if only_oracle or only_realistic:
        raise EvidenceError(
            "verdict sets differ — every episode must be judged by BOTH verifiers "
            f"(oracle-only: {only_oracle}; realistic-only: {only_realistic})"
        )
    goal_ids = sorted(oracle)
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
    }


def disagreement_records(
    records: dict[str, dict],
    oracle: dict[str, bool],
    realistic: dict[str, bool],
    disagreement_ids: list[str],
) -> list[dict]:
    """D5: the per-episode disagreement log — each stage's vote AND its
    measurement, so a disagreement is attributable to a stage (the
    evidence for the `depth_wrist` decision)."""
    log = []
    for goal_id in disagreement_ids:
        stages = (records.get(goal_id) or {}).get("stages") or {}
        log.append(
            {
                "goal_id": goal_id,
                "oracle_success": oracle[goal_id],
                "realistic_success": realistic[goal_id],
                "direction": "false_success" if realistic[goal_id] else "false_fail",
                "latch": (records.get(goal_id) or {}).get("latch"),
                "stages": {
                    stage: {
                        "vote": (stages.get(stage) or {}).get("vote"),
                        "measurement": (stages.get(stage) or {}).get("measurement"),
                        "detail": (stages.get(stage) or {}).get("detail"),
                    }
                    for stage in STAGES
                },
            }
        )
    return log


def stage_attribution(records: dict[str, dict], disagreement_ids: list[str]) -> dict[str, int]:
    """Counts of non-passing stages across the disagreeing episodes.
    Records are validated before this runs, so a missing stage can no
    longer be counted as a pass."""
    counts = dict.fromkeys(STAGES, 0)
    for goal_id in disagreement_ids:
        stages = (records.get(goal_id) or {}).get("stages") or {}
        for stage in STAGES:
            if (stages.get(stage) or {}).get("vote") != "pass":
                counts[stage] += 1
    return counts


def load_sidecar(run_dir: Path) -> tuple[dict[str, dict], dict[str, bool]]:
    """VER-14 records keyed by goal_id, strictly validated. Duplicate
    goal ids REFUSE — goal_id is the correlation key, and last-write-wins
    let a later record silently replace contrary evidence (PR #102
    review)."""
    path = Path(run_dir) / SIDECAR_NAME
    if not path.exists():
        raise EvidenceError(f"no realistic verdicts: {path} is missing")
    records: dict[str, dict] = {}
    verdicts: dict[str, bool] = {}
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"{path}:{lineno}: malformed JSON ({exc})") from exc
        success = validate_sidecar_record(record)
        goal_id = record["goal_id"]
        if goal_id in records:
            raise EvidenceError(f"{path}:{lineno}: duplicate goal_id {goal_id!r}")
        records[goal_id] = record
        verdicts[goal_id] = success
    return records, verdicts


def load_oracle_results(run_dir: Path) -> dict[str, bool]:
    """Oracle success bits from the rollout's episodes.jsonl. An explicit
    unique `goal_id` is REQUIRED: the old `ep-{seed}` fallback did not
    match the rollout client's `ep-{episode index}` convention, so absent
    ids paired under the wrong key (PR #102 review)."""
    path = Path(run_dir) / "episodes.jsonl"
    if not path.exists():
        raise EvidenceError(f"no oracle verdicts: {path} is missing")
    results: dict[str, bool] = {}
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        episode = json.loads(line)
        goal_id = episode.get("goal_id")
        if not isinstance(goal_id, str) or not goal_id:
            raise EvidenceError(f"{path}:{lineno}: episode has no goal_id (cannot correlate)")
        if goal_id in results:
            raise EvidenceError(f"{path}:{lineno}: duplicate goal_id {goal_id!r}")
        results[goal_id] = episode.get("status") == "success"
    return results


def write_manifest_metrics(run_dir: Path, report: dict) -> bool:
    """VER-6: the per-run manifest carries the four counts plus the three
    rates. Returns whether a manifest was updated."""
    path = Path(run_dir) / "manifest.json"
    if not path.exists():
        return False
    manifest = json.loads(path.read_text())
    manifest["verifier_fidelity"] = {
        "n": report["n"],
        "counts": report["counts"],
        "agreement": report["agreement"],
        "false_success_rate": report["false_success_rate"],
        "false_fail_rate": report["false_fail_rate"],
    }
    path.write_text(json.dumps(manifest, indent=1) + "\n")
    return True


def fidelity_report(run_dir: Path, write_manifest: bool = True) -> dict:
    """The agreement report for one run directory holding both verdicts."""
    records, realistic = load_sidecar(run_dir)
    oracle = load_oracle_results(run_dir)
    report = compare(oracle, realistic)
    disagreements = report["false_success_ids"] + report["false_fail_ids"]
    report["stage_attribution"] = stage_attribution(records, disagreements)
    report["disagreements"] = disagreement_records(records, oracle, realistic, disagreements)
    report["run_dir"] = str(run_dir)
    report["manifest_updated"] = write_manifest and write_manifest_metrics(run_dir, report)
    return report


class _JsonArgumentParser(argparse.ArgumentParser):
    """CON-8: argument errors are JSON refusals on stdout, not argparse
    usage text on stderr (PR #102 review)."""

    def error(self, message: str):  # noqa: D102
        print(json.dumps({"ok": False, "error": f"argument error: {message}"}))
        raise SystemExit(1)


def main() -> int:
    parser = _JsonArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--no-manifest", action="store_true", help="do not update manifest.json")
    args = parser.parse_args()
    try:
        report = fidelity_report(args.run_dir, write_manifest=not args.no_manifest)
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"ok": True, **report}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
