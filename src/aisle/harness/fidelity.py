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
import math
import sys
from pathlib import Path

from aisle.verifier.realistic import CAMERAS, SIDECAR_NAME, STAGES, StageVote, fuse

VALID_VOTES = ("pass", "fail", "error")
# TC-7's closed status enum; TC-8 makes the ORACLE the only ground truth
ORACLE_STATUSES = {"success": True, "fail": False}
# the measurement key each stage must carry when it actually voted —
# presence of the key was not enough: null and empty passed (PR #102
# review round 2)
REQUIRED_MEASUREMENTS = {
    "containment": ("margin_m", "rest_gap_m"),
    "upright": ("tilt_deg",),
    "home": ("max_joint_residual_rad",),
}
IDENTITY_FRAME_FIELDS = ("sim_time_ns", "per_class_scores", "target_in_tray", "non_target_in_tray")


class EvidenceError(ValueError):
    """Recorded evidence is missing, malformed, or self-inconsistent."""


def rate(numerator: int, denominator: int) -> float | None:
    """VER-6: null on an empty denominator — a rate over nothing is not
    zero, and reporting 0.0 would read as evidence of safety."""
    if denominator == 0:
        return None
    return numerator / denominator


def _require_mapping(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} is {type(value).__name__}, not an object")
    return value


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_identity_entry(goal_id: str, stage: str, entry: dict) -> None:
    """VER-14 identity evidence: a real per-frame timeline, and a PASSING
    vote must actually be supported by frames (PR #102 review round 2 —
    an empty timeline previously supported a pass)."""
    frames = entry.get("frames")
    if not isinstance(frames, list):
        raise EvidenceError(f"{goal_id}: stage {stage!r} has no frame timeline (VER-14)")
    if entry["vote"] == "pass" and not frames:
        raise EvidenceError(
            f"{goal_id}: stage {stage!r} voted pass with an EMPTY timeline — no evidence"
        )
    for i, frame in enumerate(frames):
        _require_mapping(frame, f"{goal_id}: {stage} frame {i}")
        missing = [f for f in IDENTITY_FRAME_FIELDS if f not in frame]
        if missing:
            raise EvidenceError(f"{goal_id}: {stage} frame {i} missing {missing} (VER-14)")
        if not isinstance(frame["sim_time_ns"], int):
            raise EvidenceError(f"{goal_id}: {stage} frame {i} sim_time_ns is not an integer")
        scores = _require_mapping(
            frame["per_class_scores"], f"{goal_id}: {stage} frame {i} per_class_scores"
        )
        for med, score in scores.items():
            if not _finite_number(score):
                raise EvidenceError(
                    f"{goal_id}: {stage} frame {i} score for {med!r} is {score!r}, "
                    "not a finite number"
                )
        for flag in ("target_in_tray", "non_target_in_tray"):
            if not isinstance(frame[flag], bool):
                raise EvidenceError(f"{goal_id}: {stage} frame {i} {flag} is not a boolean")
    # the PRODUCER's invariant (EpisodeJudge.identity_vote): a pass means
    # some judged frame actually saw the target in the tray. Shape checks
    # alone accepted a pass whose every frame said target_in_tray:false
    # (PR #102 review round 3).
    if entry["vote"] == "pass" and not any(f["target_in_tray"] for f in frames):
        raise EvidenceError(
            f"{goal_id}: stage {stage!r} voted pass but no frame reports target_in_tray"
        )


def validate_measurement(goal_id: str, stage: str, entry: dict) -> None:
    """A stage that VOTED must carry a stage-shaped, non-null measurement.
    Key presence was not enough: `measurement: null` satisfied it."""
    if entry["vote"] == "error":
        return
    measurement = entry.get("measurement")
    if measurement is None:
        raise EvidenceError(
            f"{goal_id}: stage {stage!r} voted {entry['vote']!r} with no measurement"
        )
    _require_mapping(measurement, f"{goal_id}: stage {stage!r} measurement")
    if stage == "calibration":
        if not measurement:
            raise EvidenceError(f"{goal_id}: calibration measurement is empty (no deviations)")
        return
    missing = [f for f in REQUIRED_MEASUREMENTS.get(stage, ()) if f not in measurement]
    if missing:
        raise EvidenceError(f"{goal_id}: stage {stage!r} measurement missing {missing}")
    # a MEASUREMENT must be measured: the required fields carry finite
    # numbers, not strings or nulls (PR #102 review round 3)
    for field in REQUIRED_MEASUREMENTS.get(stage, ()):
        if not _finite_number(measurement[field]):
            raise EvidenceError(
                f"{goal_id}: stage {stage!r} measurement {field} is {measurement[field]!r}, "
                "not a finite number"
            )


def validate_latch(goal_id: str, record: dict, stages: dict) -> None:
    """VER-14 latch object, cross-checked against the frames and votes.

    The producer's semantics (EpisodeJudge): the latch is set iff some
    judged frame saw a non-target in the tray, and ONCE SET both identity
    stages necessarily fail. Shape-only validation accepted a set latch
    alongside two passing identity votes (PR #102 review round 3)."""
    latch = _require_mapping(record.get("latch"), f"{goal_id}: latch")
    if not isinstance(latch.get("latched"), bool):
        raise EvidenceError(f"{goal_id}: latch.latched is not a boolean")
    event = latch.get("first_event", ...)
    if event is ...:
        raise EvidenceError(f"{goal_id}: latch has no first_event field (VER-14)")

    saw_non_target = any(
        frame["non_target_in_tray"]
        for stage in STAGES
        if stage.startswith("identity_")
        for frame in stages[stage].get("frames", [])
    )
    if latch["latched"]:
        ev = _require_mapping(event, f"{goal_id}: latch.first_event")
        for field in ("sim_time_ns", "camera", "med_class"):
            if field not in ev:
                raise EvidenceError(f"{goal_id}: latch.first_event missing {field!r} (VER-14)")
        if not isinstance(ev["sim_time_ns"], int):
            raise EvidenceError(f"{goal_id}: latch.first_event sim_time_ns is not an integer")
        if ev["camera"] not in CAMERAS:
            raise EvidenceError(f"{goal_id}: latch.first_event camera {ev['camera']!r} is unknown")
        if not saw_non_target:
            raise EvidenceError(f"{goal_id}: latch is SET but no frame reports non_target_in_tray")
        # PER-CAMERA since issue #107: a non-target seen by one camera fails
        # THAT camera's vote, so a wrist latch with a passing overhead is now
        # the expected shape — the previous global check rejected exactly the
        # records the amendment exists to produce. Deriving each camera's
        # latch from ITS OWN frames is also stricter than the old rule: it
        # catches a producer that latched camera X and still passed X, which
        # the global form missed unless X happened to be first_event.camera.
        for stage in STAGES:
            if not stage.startswith("identity_"):
                continue
            frames = stages[stage].get("frames") or []
            if any(f.get("non_target_in_tray") for f in frames) and stages[stage]["vote"] == "pass":
                raise EvidenceError(
                    f"{goal_id}: {stage} saw a non-target in the tray but voted pass — the "
                    "producer fails a camera's own identity vote once it latches (VER-9)"
                )
        named = ev["camera"]
        named_frames = stages.get(f"identity_{named}", {}).get("frames") or []
        if not any(f.get("non_target_in_tray") for f in named_frames):
            raise EvidenceError(
                f"{goal_id}: latch.first_event names {named!r} but none of that camera's frames "
                "reports non_target_in_tray (VER-14)"
            )
    else:
        if event is not None:
            raise EvidenceError(f"{goal_id}: latch is clear but carries a first_event")
        if saw_non_target:
            raise EvidenceError(
                f"{goal_id}: a frame reports non_target_in_tray but the latch is clear (VER-9)"
            )


def validate_sidecar_record(record) -> bool:
    """Strictly validate one VER-14 record and return its success bit.

    Invalid evidence REFUSES (PR #102 reviews). Every stage must exist
    with a valid vote; identity stages need a real frame timeline whose
    frames carry the VER-14 fields (and a pass must be supported by at
    least one frame); voting stages need a non-null, stage-shaped
    measurement; the latch object must be present and self-consistent;
    the record must declare `verifier: "realistic"`; and `success` must
    be a JSON Boolean that AGREES with fusing the recorded votes."""
    _require_mapping(record, "sidecar record")
    goal_id = record.get("goal_id")
    if not isinstance(goal_id, str) or not goal_id:
        raise EvidenceError("sidecar record has no goal_id")
    if record.get("verifier") != "realistic":
        raise EvidenceError(
            f"{goal_id}: verifier is {record.get('verifier')!r}, not 'realistic' (VER-5)"
        )
    stages = _require_mapping(record.get("stages"), f"{goal_id}: stages")
    votes = {}
    for stage in STAGES:
        entry = _require_mapping(stages.get(stage), f"{goal_id}: stage {stage!r}")
        vote = entry.get("vote")
        if vote not in VALID_VOTES:
            raise EvidenceError(f"{goal_id}: stage {stage!r} has invalid vote {vote!r}")
        if stage.startswith("identity_"):
            validate_identity_entry(goal_id, stage, entry)
        else:
            validate_measurement(goal_id, stage, entry)
        votes[stage] = StageVote(vote)
    validate_latch(goal_id, record, stages)
    success = record.get("success")
    if not isinstance(success, bool):
        raise EvidenceError(
            f"{goal_id}: success is {type(success).__name__} ({success!r}), not a JSON boolean"
        )
    fused = fuse(votes)
    if fused != success:
        # the common cause is a sidecar recorded under a DIFFERENT fusion rule
        # (issue #107 stopped identity_wrist gating), not a corrupt record. Say
        # so, because "disagrees with fusing its own stages" sends the reader
        # looking for a producer bug that is not there.
        raise EvidenceError(
            f"{goal_id}: recorded success {success} disagrees with fusing its own stages "
            f"({fused}) — if this run predates a VER-13 fusion change, re-judge it with "
            "tools/judge_recorded_run.py rather than mixing rules in one VER-6 number"
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
                # the FULL validated entry: identity evidence lives in
                # `frames` (timestamps + per-class scores), not in
                # `measurement`, and that is exactly what distinguishes an
                # identity disagreement from a containment/upright one
                # (PR #102 review round 2)
                "stages": {stage: stages.get(stage) for stage in STAGES},
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
    """Oracle success bits from the rollout's episodes.jsonl, strictly
    validated (PR #102 review round 2).

    TC-7 defines a closed `success|fail` status enum and TC-8 makes the
    ORACLE the only ground truth. Mapping "everything that is not
    'success'" to False silently turned a missing or bogus status — or a
    REALISTIC verdict dropped into this file — into an oracle failure,
    which manufactures false-success rate out of malformed input."""
    path = Path(run_dir) / "episodes.jsonl"
    if not path.exists():
        raise EvidenceError(f"no oracle verdicts: {path} is missing")
    results: dict[str, bool] = {}
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            episode = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"{path}:{lineno}: malformed JSON ({exc})") from exc
        _require_mapping(episode, f"{path}:{lineno}: episode")
        goal_id = episode.get("goal_id")
        if not isinstance(goal_id, str) or not goal_id:
            raise EvidenceError(f"{path}:{lineno}: episode has no goal_id (cannot correlate)")
        verifier = episode.get("verifier")
        if verifier != "oracle":
            raise EvidenceError(
                f"{path}:{lineno}: {goal_id} verifier is {verifier!r}, not 'oracle' "
                "— only the oracle verdict is ground truth (TC-8)"
            )
        status = episode.get("status")
        if status not in ORACLE_STATUSES:
            raise EvidenceError(
                f"{path}:{lineno}: {goal_id} status {status!r} is outside the "
                f"{sorted(ORACLE_STATUSES)} enum (TC-7)"
            )
        if goal_id in results:
            raise EvidenceError(f"{path}:{lineno}: duplicate goal_id {goal_id!r}")
        results[goal_id] = ORACLE_STATUSES[status]
    return results


def expected_goal_ids(run_dir: Path) -> tuple[list[str], dict]:
    """The episode set the RUN requested, from its manifest.

    The rollout client keys episodes `ep-{index:04d}`, one per manifest
    seed. Without this check a crash that truncated BOTH verdict streams
    on the difficult suffix left a matching-but-short prefix that scored
    as a complete run (PR #102 review round 3)."""
    path = Path(run_dir) / "manifest.json"
    if not path.exists():
        raise EvidenceError(
            f"{path} is missing: cannot establish how many episodes the run requested "
            "(pass --no-manifest --expect-episodes N to score an explicit subset)"
        )
    manifest = _require_mapping(json.loads(path.read_text()), f"{path}: manifest")
    seeds = manifest.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        raise EvidenceError(f"{path}: manifest has no seeds list — cannot verify completeness")
    return [f"ep-{i:04d}" for i in range(len(seeds))], manifest


def require_complete(judged: set[str], expected: list[str]) -> None:
    """Every requested episode must appear in BOTH verdict streams."""
    missing = sorted(set(expected) - judged)
    extra = sorted(judged - set(expected))
    if missing or extra:
        raise EvidenceError(
            f"incomplete evidence: {len(judged)} of {len(expected)} requested episodes judged "
            f"(missing: {missing[:8]}{'...' if len(missing) > 8 else ''}; unexpected: {extra[:8]})"
        )


def write_manifest_metrics(run_dir: Path, report: dict) -> None:
    """VER-6: the per-run manifest carries the four counts plus the three
    rates. Persisting them is core behaviour, so a missing, malformed or
    unwritable manifest REFUSES — `--no-manifest` is the explicit opt-out
    (PR #102 review round 2: the CLI used to report success while
    silently persisting nothing)."""
    path = Path(run_dir) / "manifest.json"
    if not path.exists():
        raise EvidenceError(
            f"{path} is missing: cannot persist the VER-6 metrics "
            "(pass --no-manifest to report without persisting)"
        )
    manifest = _require_mapping(json.loads(path.read_text()), f"{path}: manifest")
    manifest["verifier_fidelity"] = {
        "n": report["n"],
        "counts": report["counts"],
        "agreement": report["agreement"],
        "false_success_rate": report["false_success_rate"],
        "false_fail_rate": report["false_fail_rate"],
    }
    path.write_text(json.dumps(manifest, indent=1) + "\n")


def fidelity_report(
    run_dir: Path, write_manifest: bool = True, expect_episodes: int | None = None
) -> dict:
    """The agreement report for one run directory holding both verdicts.

    Completeness is checked BEFORE scoring (PR #102 review round 3): the
    default path validates the judged set against the run manifest's
    requested episodes; the `--no-manifest` diagnostic path requires an
    explicit `--expect-episodes N` and is labelled as a subset when the
    caller declines to state one."""
    records, realistic = load_sidecar(run_dir)
    oracle = load_oracle_results(run_dir)
    judged = set(oracle) & set(realistic)

    complete = True
    if write_manifest:
        expected, _ = expected_goal_ids(run_dir)
        require_complete(judged, expected)
    elif expect_episodes is not None:
        require_complete(judged, [f"ep-{i:04d}" for i in range(expect_episodes)])
    else:
        complete = False  # diagnostic subset: must not read as a run result

    report = compare(oracle, realistic)
    disagreements = report["false_success_ids"] + report["false_fail_ids"]
    report["stage_attribution"] = stage_attribution(records, disagreements)
    report["disagreements"] = disagreement_records(records, oracle, realistic, disagreements)
    report["run_dir"] = str(run_dir)
    report["complete_run"] = complete
    if not complete:
        report["scope"] = (
            "DIAGNOSTIC SUBSET — completeness unverified (no manifest, no --expect-episodes); "
            "not a run-level fidelity result"
        )
    if write_manifest:
        write_manifest_metrics(run_dir, report)
    report["manifest_updated"] = write_manifest
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
    parser.add_argument(
        "--expect-episodes",
        type=int,
        default=None,
        help="with --no-manifest: the episode count that must be judged by BOTH verifiers",
    )
    args = parser.parse_args()
    try:
        report = fidelity_report(
            args.run_dir,
            write_manifest=not args.no_manifest,
            expect_episodes=args.expect_episodes,
        )
    except Exception as exc:  # noqa: BLE001 — CON-8: never a traceback
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"ok": True, **report}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
