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


def _record(goal_id, success, votes=None, measurement=True, frames=True):
    """A VALID VER-14 record — the fixture has to satisfy the same strict
    schema the comparator enforces."""
    votes = votes or dict.fromkeys(STAGES, "pass")
    shapes = {
        "calibration": {"overhead_pos_max_dev_m": 0.001},
        "containment": {"margin_m": 0.01, "rest_gap_m": 0.0},
        "upright": {"tilt_deg": 1.0},
        "home": {"max_joint_residual_rad": 0.01},
    }
    stages = {}
    for stage in STAGES:
        entry = {"vote": votes[stage]}
        if stage.startswith("identity_"):
            entry["frames"] = (
                [
                    {
                        "sim_time_ns": 0,
                        "per_class_scores": {"omeprazole": 0.9},
                        "target_in_tray": True,
                        "non_target_in_tray": False,
                    }
                ]
                if frames
                else []
            )
        elif measurement:
            entry["measurement"] = shapes[stage]
        stages[stage] = entry
    return {
        "goal_id": goal_id,
        "verifier": "realistic",
        "success": success,
        "latch": {"latched": False, "first_event": None},
        "stages": stages,
    }


def _oracle(goal_id, success):
    """A VALID oracle episode record (TC-7 status enum, TC-8 verifier)."""
    return {
        "goal_id": goal_id,
        "status": "success" if success else "fail",
        "verifier": "oracle",
    }


_DEFAULT_MANIFEST = {"run_id": "r1"}


def _write_run(tmp_path, episodes, sidecar, manifest=_DEFAULT_MANIFEST):
    (tmp_path / "episodes.jsonl").write_text("\n".join(json.dumps(e) for e in episodes) + "\n")
    (tmp_path / SIDE).write_text("\n".join(json.dumps(r) for r in sidecar) + "\n")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.unlink(missing_ok=True)
    if manifest is not None:
        manifest_path.write_text(json.dumps(manifest))


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
    with pytest.raises(EvidenceError, match="not an object"):
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

    empty_timeline = _record("ep-9", True, frames=False)
    with pytest.raises(EvidenceError, match="EMPTY timeline"):
        validate_sidecar_record(empty_timeline)


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
        [_oracle("ep-0001", True), _oracle("ep-0002", False)],
        # both consistent records; the DUPLICATE key is what must refuse
        [_record("ep-0001", True), _record("ep-0001", True)],
    )
    with pytest.raises(EvidenceError, match="duplicate goal_id"):
        load_sidecar(tmp_path)

    _write_run(
        tmp_path,
        [_oracle("ep-0001", True), _oracle("ep-0001", False)],
        [_record("ep-0001", True)],
    )
    with pytest.raises(EvidenceError, match="duplicate goal_id"):
        load_oracle_results(tmp_path)


def test_missing_goal_id_refuses_instead_of_guessing_a_key(tmp_path):
    """The old fallback built `ep-{seed}` while the rollout client builds
    `ep-{episode index}` — absent ids paired under the wrong key."""
    _write_run(
        tmp_path,
        [{"seed": 7, "status": "success", "verifier": "oracle"}],
        [_record("ep-0007", True)],
    )
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
    assert entry["stages"]["containment"]["measurement"] == {"margin_m": 0.01, "rest_gap_m": 0.0}
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
        [_oracle("ep-0001", True), _oracle("ep-0002", False)],
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
        [_oracle("ep-0001", True)],
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


def test_evidence_free_all_pass_record_is_refused():
    """PR #102 review round 2's sharpest reproduction: an all-pass record
    with EMPTY identity timelines, null measurements, and no verifier or
    latch fields used to validate and enter the denominators."""
    bare = {
        "goal_id": "ep-hollow",
        "success": True,
        "stages": {
            s: (
                {"vote": "pass", "frames": []}
                if s.startswith("identity_")
                else {"vote": "pass", "measurement": None}
            )
            for s in STAGES
        },
    }
    with pytest.raises(EvidenceError, match="verifier"):
        validate_sidecar_record(bare)

    bare["verifier"] = "realistic"
    bare["latch"] = {"latched": False, "first_event": None}
    # null measurements are caught first; with those repaired, the empty
    # identity timelines are still refused — a pass needs evidence
    with pytest.raises(EvidenceError, match="no measurement"):
        validate_sidecar_record(bare)
    for stage, shape in (
        ("calibration", {"overhead_pos_max_dev_m": 0.0}),
        ("containment", {"margin_m": 0.01, "rest_gap_m": 0.0}),
        ("upright", {"tilt_deg": 1.0}),
        ("home", {"max_joint_residual_rad": 0.01}),
    ):
        bare["stages"][stage]["measurement"] = shape
    with pytest.raises(EvidenceError, match="EMPTY timeline"):
        validate_sidecar_record(bare)

    null_measure = _record("ep-null", True, measurement=False)
    with pytest.raises(EvidenceError, match="no measurement"):
        validate_sidecar_record(null_measure)

    wrong_shape = _record("ep-shape", True)
    wrong_shape["stages"]["containment"]["measurement"] = {"unrelated": 1}
    with pytest.raises(EvidenceError, match="measurement missing"):
        validate_sidecar_record(wrong_shape)

    bad_frame = _record("ep-frame", True)
    bad_frame["stages"]["identity_overhead"]["frames"][0]["target_in_tray"] = "yes"
    with pytest.raises(EvidenceError, match="not a boolean"):
        validate_sidecar_record(bad_frame)

    bad_latch = _record("ep-latch", True)
    del bad_latch["latch"]["first_event"]
    with pytest.raises(EvidenceError, match="first_event"):
        validate_sidecar_record(bad_latch)


def test_non_oracle_or_bogus_status_cannot_become_ground_truth(tmp_path):
    """TC-7/TC-8 (PR #102 review round 2): a bogus status, or a REALISTIC
    verdict dropped into episodes.jsonl, previously counted as an oracle
    failure and manufactured a false-success rate."""
    _write_run(
        tmp_path,
        [{"goal_id": "ep-1", "status": "bogus", "verifier": "oracle"}],
        [_record("ep-1", True)],
    )
    with pytest.raises(EvidenceError, match="outside the"):
        load_oracle_results(tmp_path)

    _write_run(
        tmp_path,
        [{"goal_id": "ep-1", "status": "fail", "verifier": "realistic"}],
        [_record("ep-1", True)],
    )
    with pytest.raises(EvidenceError, match="not 'oracle'"):
        load_oracle_results(tmp_path)

    _write_run(tmp_path, [{"goal_id": "ep-1", "verifier": "oracle"}], [_record("ep-1", True)])
    with pytest.raises(EvidenceError, match="outside the"):
        load_oracle_results(tmp_path)


def test_disagreement_records_carry_identity_frames():
    """D5 (PR #102 review round 2): identity evidence lives in `frames`,
    so copying only `measurement` emitted null for both identity stages —
    losing exactly what distinguishes an identity disagreement."""
    votes = dict.fromkeys(STAGES, "pass")
    votes["identity_wrist"] = "fail"
    records = {"ep-1": _record("ep-1", False, votes=votes)}
    log = disagreement_records(records, {"ep-1": True}, {"ep-1": False}, ["ep-1"])
    wrist = log[0]["stages"]["identity_wrist"]
    assert wrist["vote"] == "fail"
    assert wrist["frames"], "identity timeline dropped from the disagreement record"
    assert wrist["frames"][0]["per_class_scores"] == {"omeprazole": 0.9}


def test_missing_manifest_refuses_unless_opted_out(tmp_path):
    """VER-6 (PR #102 review round 2): persisting the metrics is core
    behaviour; reporting ok while persisting nothing hid the failure."""
    _write_run(tmp_path, [_oracle("ep-0001", True)], [_record("ep-0001", True)], manifest=None)
    with pytest.raises(EvidenceError, match="cannot persist"):
        fidelity_report(tmp_path)
    report = fidelity_report(tmp_path, write_manifest=False)
    assert report["manifest_updated"] is False and report["n"] == 1


def test_malformed_json_shapes_still_produce_con8_refusals(tmp_path):
    """CON-8 (PR #102 review round 2): a top-level `[]` in either file
    raised AttributeError/TypeError with a traceback and empty stdout."""
    _write_run(tmp_path, [_oracle("ep-0001", True)], [_record("ep-0001", True)])
    (tmp_path / SIDE).write_text("[]\n")
    bad_sidecar = subprocess.run(
        [sys.executable, "-m", "aisle.harness.fidelity", "--run-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert bad_sidecar.returncode == 1 and json.loads(bad_sidecar.stdout)["ok"] is False

    _write_run(tmp_path, [_oracle("ep-0001", True)], [_record("ep-0001", True)])
    (tmp_path / "manifest.json").write_text("[]")
    bad_manifest = subprocess.run(
        [sys.executable, "-m", "aisle.harness.fidelity", "--run-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert bad_manifest.returncode == 1 and json.loads(bad_manifest.stdout)["ok"] is False
