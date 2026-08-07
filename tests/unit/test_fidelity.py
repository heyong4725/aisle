"""Verifier-agreement report (SPEC 040 VER-6 comparator half).

Covers the explicit denominators, null-not-zero empties, strict
evidence validation, identical-set pairing, D5 disagreement records,
manifest persistence, and the CON-8 CLI contract. Pure — no models, no
sim (CON-12).
"""

import json
import subprocess
import sys

import pytest

from aisle.harness.fidelity import (
    EvidenceError,
    compare,
    disagreement_records,
    fidelity_report,
    load_oracle_results,
    load_sidecar,
    rate,
    stage_attribution,
    validate_sidecar_record,
)
from aisle.verifier.realistic import STAGES

pytestmark = pytest.mark.unit


def _record(goal_id, success, votes=None, measurement=True):
    votes = votes or dict.fromkeys(STAGES, "pass")
    stages = {}
    for stage in STAGES:
        entry = {"vote": votes[stage]}
        if stage.startswith("identity_"):
            entry["frames"] = [{"sim_time_ns": 0, "per_class_scores": {}}]
        elif measurement:
            entry["measurement"] = {"probe": 1.0}
        stages[stage] = entry
    return {"goal_id": goal_id, "verifier": "realistic", "success": success, "stages": stages}


def _write_run(tmp_path, episodes, sidecar, manifest=None):
    (tmp_path / "episodes.jsonl").write_text("\n".join(json.dumps(e) for e in episodes) + "\n")
    (tmp_path / SIDE).write_text("\n".join(json.dumps(r) for r in sidecar) + "\n")
    if manifest is not None:
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))


SIDE = "verifier_stages.jsonl"


def test_rate_is_null_on_an_empty_denominator():
    """VER-6: 'no oracle failures' is NOT 'zero false successes'."""
    assert rate(0, 0) is None
    assert rate(3, 4) == 0.75


def test_denominators_are_the_specified_classes():
    oracle = {"ep-1": True, "ep-2": True, "ep-3": False, "ep-4": False}
    realistic = {"ep-1": True, "ep-2": False, "ep-3": True, "ep-4": False}
    report = compare(oracle, realistic)
    assert report["counts"] == {
        "agree": 2,
        "oracle_pass": 2,
        "oracle_fail": 2,
        "false_success": 1,
        "false_fail": 1,
    }
    assert report["agreement"] == 0.5
    assert report["false_success_rate"] == 0.5  # 1 of 2 oracle failures
    assert report["false_fail_rate"] == 0.5  # 1 of 2 oracle successes


def test_all_oracle_successes_gives_a_null_false_success_rate():
    report = compare({"ep-1": True, "ep-2": True}, {"ep-1": True, "ep-2": False})
    assert report["false_success_rate"] is None
    assert report["false_fail_rate"] == 0.5


def test_partial_pairing_refuses_instead_of_publishing_biased_rates():
    """PR #102 review: scoring the intersection let a realistic crash on
    the HARD episodes vanish from every denominator while the CLI still
    returned ok. Identical non-empty sets are required."""
    with pytest.raises(EvidenceError, match="verdict sets differ"):
        compare({"easy": True, "hard": False}, {"easy": True})
    with pytest.raises(EvidenceError, match="verdict sets differ"):
        compare({"easy": True}, {"easy": True, "extra": False})
    with pytest.raises(EvidenceError, match="refuses N=0"):
        compare({}, {})


def test_invalid_evidence_refuses_rather_than_being_coerced():
    """PR #102 review: a missing stage defaulted to `pass` in attribution
    and `bool("false")` silently became True."""
    assert validate_sidecar_record(_record("ep-1", True)) is True

    missing_stage = _record("ep-2", True)
    del missing_stage["stages"]["upright"]
    with pytest.raises(EvidenceError, match="missing from the record"):
        validate_sidecar_record(missing_stage)

    string_success = _record("ep-3", True)
    string_success["success"] = "false"
    with pytest.raises(EvidenceError, match="not a JSON boolean"):
        validate_sidecar_record(string_success)

    bad_vote = _record("ep-4", True)
    bad_vote["stages"]["home"]["vote"] = "ok"
    with pytest.raises(EvidenceError, match="invalid vote"):
        validate_sidecar_record(bad_vote)

    no_timeline = _record("ep-5", True)
    del no_timeline["stages"]["identity_overhead"]["frames"]
    with pytest.raises(EvidenceError, match="frame timeline"):
        validate_sidecar_record(no_timeline)

    no_measurement = _record("ep-6", True, measurement=False)
    with pytest.raises(EvidenceError, match="no measurement"):
        validate_sidecar_record(no_measurement)


def test_success_bit_must_agree_with_its_own_stages():
    """Self-inconsistent evidence is refused: a record claiming success
    while a stage failed cannot enter any denominator."""
    votes = dict.fromkeys(STAGES, "pass")
    votes["containment"] = "fail"
    inconsistent = _record("ep-7", True, votes=votes)
    with pytest.raises(EvidenceError, match="disagrees with fusing"):
        validate_sidecar_record(inconsistent)
    assert validate_sidecar_record(_record("ep-8", False, votes=votes)) is False


def test_duplicate_goal_ids_refuse(tmp_path):
    """goal_id is the correlation key; last-write-wins let a later record
    silently replace contrary evidence (PR #102 review)."""
    _write_run(
        tmp_path,
        [
            {"goal_id": "ep-0001", "status": "success"},
            {"goal_id": "ep-0002", "status": "fail"},
        ],
        # both consistent records; the DUPLICATE key is what must refuse
        [_record("ep-0001", True), _record("ep-0001", True)],
    )
    with pytest.raises(EvidenceError, match="duplicate goal_id"):
        load_sidecar(tmp_path)

    _write_run(
        tmp_path,
        [{"goal_id": "ep-0001", "status": "success"}, {"goal_id": "ep-0001", "status": "fail"}],
        [_record("ep-0001", True)],
    )
    with pytest.raises(EvidenceError, match="duplicate goal_id"):
        load_oracle_results(tmp_path)


def test_missing_goal_id_refuses_instead_of_guessing_a_key(tmp_path):
    """The old fallback built `ep-{seed}` while the rollout client builds
    `ep-{episode index}` — absent ids paired under the wrong key."""
    _write_run(tmp_path, [{"seed": 7, "status": "success"}], [_record("ep-0007", True)])
    with pytest.raises(EvidenceError, match="no goal_id"):
        load_oracle_results(tmp_path)


def test_disagreement_records_carry_stage_votes_and_measurements():
    """D5: the per-episode log, not just aggregate counts."""
    votes = dict.fromkeys(STAGES, "pass")
    votes["containment"] = "fail"
    records = {"ep-1": _record("ep-1", False, votes=votes)}
    log = disagreement_records(records, {"ep-1": True}, {"ep-1": False}, ["ep-1"])
    assert len(log) == 1
    entry = log[0]
    assert entry["direction"] == "false_fail"
    assert entry["stages"]["containment"]["vote"] == "fail"
    assert entry["stages"]["containment"]["measurement"] == {"probe": 1.0}
    assert set(entry["stages"]) == set(STAGES)


def test_stage_attribution_counts_non_passing_stages():
    votes = dict.fromkeys(STAGES, "pass")
    votes["containment"] = "fail"
    votes["upright"] = "error"
    counts = stage_attribution({"ep-1": _record("ep-1", False, votes=votes)}, ["ep-1"])
    assert counts["containment"] == 1 and counts["upright"] == 1
    assert counts["home"] == 0


def test_report_pairs_verdicts_and_persists_manifest_metrics(tmp_path):
    """VER-6: the four counts plus three rates land in the run manifest."""
    votes = dict.fromkeys(STAGES, "pass")
    _write_run(
        tmp_path,
        [
            {"goal_id": "ep-0001", "status": "success"},
            {"goal_id": "ep-0002", "status": "fail", "failure": "dropped"},
        ],
        [_record("ep-0001", True, votes=votes), _record("ep-0002", True, votes=votes)],
        manifest={"run_id": "r1"},
    )
    report = fidelity_report(tmp_path)
    assert report["n"] == 2
    assert report["counts"]["false_success"] == 1
    assert report["false_success_rate"] == 1.0
    assert report["manifest_updated"] is True
    assert [d["goal_id"] for d in report["disagreements"]] == ["ep-0002"]

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    fidelity = manifest["verifier_fidelity"]
    assert fidelity["counts"]["false_success"] == 1
    assert fidelity["false_success_rate"] == 1.0
    assert fidelity["agreement"] == 0.5


def test_missing_evidence_files_refuse(tmp_path):
    with pytest.raises(EvidenceError, match="no realistic verdicts"):
        fidelity_report(tmp_path)
    (tmp_path / "verifier_stages.jsonl").write_text(json.dumps(_record("ep-1", True)) + "\n")
    with pytest.raises(EvidenceError, match="no oracle verdicts"):
        fidelity_report(tmp_path)


def test_cli_contract_including_argument_errors(tmp_path):
    """CON-8: JSON on stdout, exit 0 iff ok — INCLUDING argument errors,
    which argparse previously reported as usage text on stderr."""
    _write_run(
        tmp_path,
        [{"goal_id": "ep-0001", "status": "success"}],
        [_record("ep-0001", True)],
    )
    ok = subprocess.run(
        [sys.executable, "-m", "aisle.harness.fidelity", "--run-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0 and json.loads(ok.stdout)["n"] == 1

    missing_arg = subprocess.run(
        [sys.executable, "-m", "aisle.harness.fidelity"], capture_output=True, text=True
    )
    assert missing_arg.returncode == 1
    payload = json.loads(missing_arg.stdout)
    assert payload["ok"] is False and "argument error" in payload["error"]

    bad = subprocess.run(
        [sys.executable, "-m", "aisle.harness.fidelity", "--run-dir", str(tmp_path / "nope")],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1 and json.loads(bad.stdout)["ok"] is False
