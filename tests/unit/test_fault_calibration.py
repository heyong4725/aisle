"""Severity calibration scoring on the seed-pair unit (FLT-9, FLT-10,
FLT-12; issue #348).

Values are fixed independently: 8/8 clean versus 2/8 fault is a paired
difference of 0.75 with non-overlapping exact intervals; 8/8 versus 7/8
falls below the 0.25 minimum; a sham at 8/8 is parity; a clean baseline
with zero successes is saturated and cannot calibrate.
"""

from __future__ import annotations

import pytest

from aisle.harness.fault_calibration import calibration_report, score_rung

pytestmark = pytest.mark.unit


def _episodes(successes: int, n: int = 8, failure: str = "never_grasped") -> list[dict]:
    return [
        {
            "seed": s,
            "status": "success" if s < successes else "fail",
            "failure": None if s < successes else failure,
        }
        for s in range(n)
    ]


def test_effective_rung_is_selected_on_paired_seeds():
    """FLT-9: the seed pair is the unit; the paired difference, exact
    intervals, discordant pairs, and failure classes are all reported."""
    score = score_rung(_episodes(8), _episodes(2))
    assert score["decision"] == "selected"
    assert score["pairs"] == 8 and score["paired_difference"] == 0.75
    assert score["discordant_pairs"] == {"clean_only": 6, "fault_only": 0}
    assert score["discordant_clean_only_interval"]["lower"] > 0.5
    assert score["fault_failure_classes"] == {"never_grasped": 6, "success": 2}
    assert score["rule"]["unit"] == "seed_pair"


def test_weak_saturated_and_unpaired_rungs_are_rejected_or_invalid():
    """FLT-9 / FLT-10: below the minimum meaningful degradation or with
    overlapping intervals the rung is rejected; a zero-success clean
    baseline is saturated; no paired seeds is invalid, never scored."""
    assert score_rung(_episodes(8), _episodes(7))["decision"] == "rejected"
    saturated = score_rung(_episodes(0), _episodes(0))
    assert saturated["decision"] == "rejected" and "saturated" in saturated["reason"]
    unpaired = score_rung(_episodes(8), [{"seed": 99, "status": "fail", "failure": "x"}])
    assert unpaired["decision"] == "invalid" and unpaired["missing_seeds"] == list(range(8)) + [99]


def test_sham_must_be_indistinguishable_from_clean():
    """FLT-8 / FLT-9: a sham that degrades the baseline blocks; one at
    parity is a control."""
    assert score_rung(_episodes(8), _episodes(8), sham=True)["decision"] == "parity"
    assert score_rung(_episodes(8), _episodes(1), sham=True)["decision"] == "rejected"


def test_report_selects_least_severe_effective_rung_and_retains_everything():
    """FLT-10: every attempted rung is retained under excluded_pilot; the
    least severe effective rung is selected; a rejected instance and a
    sham control keep their dispositions; the report is content-hashed."""
    rungs = [
        {
            "opaque_id": "p1",
            "family": "perception",
            "severity_index": 0,
            "sham": False,
            "score": score_rung(_episodes(8), _episodes(7)),
        },
        {
            "opaque_id": "p1",
            "family": "perception",
            "severity_index": 1,
            "sham": False,
            "score": score_rung(_episodes(8), _episodes(2)),
        },
        {
            "opaque_id": "m1",
            "family": "motion",
            "severity_index": 0,
            "sham": False,
            "score": score_rung(_episodes(8), _episodes(8)),
        },
        {
            "opaque_id": "sham",
            "family": "sham",
            "severity_index": 0,
            "sham": True,
            "score": score_rung(_episodes(8), _episodes(8), sham=True),
        },
    ]
    report = calibration_report("bank", "clean-run", rungs, campaign_id="cal")
    assert report["campaign_purpose"] == "excluded_pilot"
    assert report["selection"]["p1"] == {
        "disposition": "selected",
        "severity_index": 1,
        "rule": "least severe rung that meets the frozen degradation rule",
        "rungs": [0, 1],
    }
    assert report["selection"]["m1"]["disposition"] == "rejected"
    assert report["selection"]["sham"]["disposition"] == "control"
    assert len(report["rungs"]) == 4 and report["report_hash"].startswith("sha256:")
    again = calibration_report("bank", "clean-run", rungs, campaign_id="cal")
    assert again["report_hash"] == report["report_hash"]


def test_a_fault_that_causes_a_wrong_object_episode_is_a_semantic_hazard():
    """FLT-11: a wrong-object outcome under a fault rejects the instance
    outright, whatever its degradation."""
    score = score_rung(_episodes(8), _episodes(2, failure="wrong_object"))
    assert score["decision"] == "rejected" and "semantic hazard" in score["reason"]
    assert score["wrong_object_episodes"] == 6
