"""H6 campaign scoring (ADR-h6-operation-protocol): cell verdicts and
the campaign verdict are DERIVED from the raw cell record — the table
is recomputed, never hand-written (the h4 discipline)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import h6_campaign as h6  # noqa: E402

pytestmark = pytest.mark.unit


def _rows(statuses):
    return [
        {"episode": i, "seed": i, "status": s, "failure": None if s == "success" else "timeout"}
        for i, s in enumerate(statuses)
    ]


def _record(
    *,
    statuses,
    timeline,
    inject_ts,
    diagnosis,
    repair_ts,
    wrong_object_at=None,
):
    rows = _rows(statuses)
    if wrong_object_at is not None:
        rows[wrong_object_at]["status"] = "fail"
        rows[wrong_object_at]["failure"] = "wrong_object"
    return {
        "cell": "F1",
        "node": "segmented-pose",
        "fault": "pose_bias",
        "stream_t0": 0.0,
        "rows": rows,
        "timeline": timeline,
        "inject_ts": inject_ts,
        "diagnosis": diagnosis,
        "repair_ts": repair_ts,
        "out_of_space": False,
    }


# A canonical healthy->faulted->repaired cell: 6 baseline passes,
# injection at t=100, 6 faulted failures, repair at t=300, 6 passes.
# Amendment 4 shape: the fault is baked from launch (inject_ts = stream
# start), 6 faulted fails, then 7 post passes — the FIRST post episode
# (6) straddles the repair (started at 60) and is credited to neither
# window, leaving a full 6-episode post window. The healthy baseline is
# the pre-registered expert 1.0 (BASELINE_EXPECTED), not an in-cell
# window.
GOOD = dict(
    statuses=["fail"] * 6 + ["success"] * 7,
    timeline=[float(t) for t in list(range(10, 70, 10)) + list(range(310, 380, 10))],
    inject_ts=0.0,
    diagnosis={
        "detected": True,
        "node": "segmented-pose",
        "evidence": ["episode results"],
        "ts": 200.0,
    },
    repair_ts=300.0,
)


def test_good_cell_passes():
    score = h6.score_cell(_record(**GOOD))
    assert score["fault_rate"] == 0.0
    assert score["post_rate"] == 1.0
    assert score["detected"] and score["localized"] and score["restored"]
    assert score["wrong_objects"] == 0
    assert score["verdict"] == "PASS"


def test_weak_fault_is_invalid_not_fail():
    """Amendment 4: the first-6 window must sit at least 2/6 under the
    registered baseline or the cell measured nothing."""
    rec = _record(**{**GOOD, "statuses": ["success"] * 6 + ["success"] * 7})
    assert h6.score_cell(rec)["verdict"] == "INVALID"


def test_wrong_node_diagnosis_is_not_localized():
    rec = _record(**{**GOOD, "diagnosis": {**GOOD["diagnosis"], "node": "ik-trajectory"}})
    score = h6.score_cell(rec)
    assert not score["localized"] and score["verdict"] == "FAIL"


def test_diagnosis_without_evidence_is_not_credited():
    rec = _record(**{**GOOD, "diagnosis": {**GOOD["diagnosis"], "evidence": []}})
    assert not h6.score_cell(rec)["localized"]


def test_missing_diagnosis_and_repair_fail_cleanly():
    rec = _record(**{**GOOD, "diagnosis": None, "repair_ts": None})
    score = h6.score_cell(rec)
    assert not score["detected"] and not score["localized"] and not score["restored"]
    assert score["verdict"] == "FAIL"


def test_unrestored_post_window_fails():
    rec = _record(**{**GOOD, "statuses": ["fail"] * 13})
    score = h6.score_cell(rec)
    assert not score["restored"] and score["verdict"] == "FAIL"


def test_short_post_window_is_not_restored():
    """Fewer than 6 completed post-repair episodes never count as restored
    — an agent repairing at the wall ceiling cannot buy credit from an
    unmeasured window."""
    rec = _record(
        **{
            **GOOD,
            "statuses": ["fail"] * 6 + ["success"] * 2,
            "timeline": GOOD["timeline"][:8],
        }
    )
    assert not h6.score_cell(rec)["restored"]


def test_any_wrong_object_fails_the_cell():
    rec = _record(**GOOD, wrong_object_at=9)
    score = h6.score_cell(rec)
    assert score["wrong_objects"] == 1 and score["verdict"] == "FAIL"


def test_post_window_counts_only_episodes_started_after_repair():
    """The h4 crediting rule: an episode straddling the repair is never
    credited to the post window. Episode i starts at timeline[i-1]."""
    # episode 6 STARTS at timeline[5]=60 < repair 300: straddler, excluded
    rec = _record(
        **{
            **GOOD,
            "statuses": ["fail"] * 6 + ["fail"] + ["success"] * 6,
            "timeline": GOOD["timeline"][:6] + [305.0] + [float(t) for t in range(310, 370, 10)],
        }
    )
    score = h6.score_cell(rec)
    assert score["post_rate"] == 1.0 and score["restored"]


def test_campaign_verdict_supported_partial_falsified():
    p = {"verdict": "PASS", "localized": True, "out_of_space": False}
    f = {"verdict": "FAIL", "localized": False, "out_of_space": False}
    assert h6.campaign_verdict([p, p, f])["verdict"] == "SUPPORTED"
    assert h6.campaign_verdict([p, f, f])["verdict"] == "PARTIAL"
    assert h6.campaign_verdict([f, f, f])["verdict"] == "FALSIFIED"
    oos = {"verdict": "FAIL", "localized": True, "out_of_space": True}
    assert h6.campaign_verdict([p, p, oos])["verdict"] == "FALSIFIED"
