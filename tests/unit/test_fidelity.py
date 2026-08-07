"""VER-6 fidelity job (SPEC 040): explicit denominators, null-not-zero
empties, N=0 refusal, and stage attribution from the VER-14 sidecar.
Pure — no models, no sim (CON-12)."""

import json
import subprocess
import sys

import pytest

from aisle.harness.fidelity import (
    compare,
    fidelity_report,
    load_sidecar,
    rate,
    stage_attribution,
)

pytestmark = pytest.mark.unit


def test_rate_is_null_on_an_empty_denominator():
    """VER-6: 'no oracle failures' is NOT 'zero false successes' — a
    rate over nothing must not read as evidence of safety."""
    assert rate(0, 0) is None
    assert rate(3, 4) == 0.75


def test_denominators_are_the_specified_classes():
    """false_success over ORACLE FAILURES, false_fail over ORACLE
    SUCCESSES — not over N (VER-6, the round-1 review's finding)."""
    oracle = {"ep-1": True, "ep-2": True, "ep-3": False, "ep-4": False}
    realistic = {"ep-1": True, "ep-2": False, "ep-3": True, "ep-4": False}
    report = compare(oracle, realistic)
    assert report["n"] == 4
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
    assert report["false_success_ids"] == ["ep-3"]
    assert report["false_fail_ids"] == ["ep-2"]


def test_all_oracle_successes_gives_a_null_false_success_rate():
    oracle = {"ep-1": True, "ep-2": True}
    realistic = {"ep-1": True, "ep-2": False}
    report = compare(oracle, realistic)
    assert report["false_success_rate"] is None  # no oracle failures to fake
    assert report["false_fail_rate"] == 0.5


def test_empty_comparison_refuses():
    with pytest.raises(ValueError, match="refuses N=0"):
        compare({}, {})
    with pytest.raises(ValueError, match="refuses N=0"):
        compare({"ep-1": True}, {"ep-9": True})  # no shared episodes


def test_unpaired_episodes_are_reported_not_silently_dropped():
    report = compare({"ep-1": True, "ep-2": False}, {"ep-1": True})
    assert report["n"] == 1
    assert report["unpaired_goal_ids"] == ["ep-2"]


def test_stage_attribution_counts_non_passing_stages():
    """VER-14 -> the D4 depth_wrist trigger: disagreements must be
    attributable to stages, so containment/upright dominance is
    measurable from overhead-only evidence."""
    records = {
        "ep-1": {
            "stages": {
                "calibration": {"vote": "pass"},
                "identity_overhead": {"vote": "pass"},
                "identity_wrist": {"vote": "pass"},
                "containment": {"vote": "fail"},
                "upright": {"vote": "fail"},
                "home": {"vote": "pass"},
            }
        },
        "ep-2": {
            "stages": {
                "calibration": {"vote": "pass"},
                "identity_overhead": {"vote": "fail"},
                "identity_wrist": {"vote": "pass"},
                "containment": {"vote": "pass"},
                "upright": {"vote": "pass"},
                "home": {"vote": "pass"},
            }
        },
    }
    counts = stage_attribution(records, ["ep-1", "ep-2"])
    assert counts["containment"] == 1 and counts["upright"] == 1
    assert counts["identity_overhead"] == 1
    assert counts["home"] == 0


def _write_run(tmp_path, episodes, sidecar):
    (tmp_path / "episodes.jsonl").write_text("\n".join(json.dumps(e) for e in episodes) + "\n")
    (tmp_path / "verifier_stages.jsonl").write_text(
        "\n".join(json.dumps(r) for r in sidecar) + "\n"
    )


def test_report_pairs_oracle_and_sidecar_by_goal_id(tmp_path):
    episodes = [
        {"seed": 1, "goal_id": "ep-0001", "status": "success"},
        {"seed": 2, "goal_id": "ep-0002", "status": "fail", "failure": "dropped"},
    ]
    sidecar = [
        {"goal_id": "ep-0001", "success": True, "stages": {}},
        {"goal_id": "ep-0002", "success": True, "stages": {"containment": {"vote": "pass"}}},
    ]
    _write_run(tmp_path, episodes, sidecar)
    report = fidelity_report(tmp_path)
    assert report["n"] == 2
    assert report["counts"]["false_success"] == 1  # ep-0002: realistic says yes, oracle no
    assert report["false_success_rate"] == 1.0
    assert report["false_fail_rate"] == 0.0


def test_sidecar_last_write_per_episode_wins(tmp_path):
    (tmp_path / "verifier_stages.jsonl").write_text(
        json.dumps({"goal_id": "ep-1", "success": False, "stages": {}})
        + "\n"
        + json.dumps({"goal_id": "ep-1", "success": True, "stages": {}})
        + "\n"
    )
    assert load_sidecar(tmp_path)["ep-1"]["success"] is True


def test_cli_emits_one_json_object_and_refuses_cleanly(tmp_path):
    """CON-8: JSON on stdout, exit 0 iff ok."""
    episodes = [{"seed": 1, "goal_id": "ep-0001", "status": "success"}]
    sidecar = [{"goal_id": "ep-0001", "success": True, "stages": {}}]
    _write_run(tmp_path, episodes, sidecar)
    ok = subprocess.run(
        [sys.executable, "-m", "aisle.harness.fidelity", "--run-dir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert ok.returncode == 0
    payload = json.loads(ok.stdout)
    assert payload["ok"] is True and payload["n"] == 1

    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "episodes.jsonl").write_text("")
    (empty / "verifier_stages.jsonl").write_text("")
    bad = subprocess.run(
        [sys.executable, "-m", "aisle.harness.fidelity", "--run-dir", str(empty)],
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 1
    assert json.loads(bad.stdout)["ok"] is False
