"""#267: how often did the guard CLAMP — i.e. how often did the action the
arm EXECUTED differ from the action the policy PROPOSED?

The guard publishes a `violation` record on every clamp (BG-3) and the
recorder writes it to Arrow, so the statistic has always been on the wire.
Nothing aggregated it, so nobody could answer the question that has to be
settled before any VLA fine-tune: which signal is the demonstration label,
`joint_cmd` (proposed) or `joint_cmd_safe` (executed)?

Pure aggregation over decoded rows — no sim, no dora (CON-12).
"""

import json

import pytest

pytestmark = pytest.mark.unit

from aisle.harness.guard_divergence import divergence_summary  # noqa: E402


def _viol(reason, joint=0, requested=1.0, clamped=0.5):
    return {"reason": reason, "requested": requested, "clamped": clamped, "joint": joint}


def test_a_clean_run_reports_zero_divergence_not_a_missing_number():
    """BG-3 (#267): a run where the guard never clamped means proposals and
    executions are IDENTICAL — the two candidate labels coincide, and the
    fine-tuning question is moot for that corpus. That has to read as 0.0,
    never as null, or the answer looks unmeasured rather than measured."""
    s = divergence_summary(violations=[], commands=120)
    assert s["commands"] == 120
    assert s["clamped_commands"] == 0
    assert s["divergence_rate"] == 0.0
    assert s["by_reason"] == {}
    assert s["labels_coincide"] is True


def test_divergence_is_per_COMMAND_not_per_violation():
    """#267: one command can violate several joints at once, and the guard
    emits a record per joint. Counting records would overstate how often the
    executed action differed — the label question is about COMMANDS."""
    rows = [
        # one command, three joints clamped
        [_viol("position", joint=0), _viol("position", joint=1), _viol("velocity", joint=2)],
        # a second command, one joint
        [_viol("velocity", joint=4)],
    ]
    s = divergence_summary(violations=rows, commands=10)
    assert s["clamped_commands"] == 2
    assert s["divergence_rate"] == pytest.approx(0.2)
    assert s["violation_records"] == 4  # kept, but never the rate's numerator
    assert s["by_reason"] == {"position": 2, "velocity": 2}


def test_magnitude_is_reported_so_a_rare_but_huge_clamp_is_visible():
    """#267: rate alone can mislead. A guard that clamps 1% of commands by
    2 rad is a very different supervision problem from one that clamps 40%
    by 1e-4 — the first makes proposals dangerous labels, the second makes
    the distinction nearly irrelevant."""
    rows = [
        [_viol("position", requested=1.5, clamped=1.0)],
        [_viol("position", requested=0.2, clamped=0.1)],
    ]
    s = divergence_summary(violations=rows, commands=4)
    assert s["max_abs_delta"] == pytest.approx(0.5)
    assert s["mean_abs_delta"] == pytest.approx(0.3)


def test_records_without_numbers_do_not_corrupt_the_magnitude():
    """BG-3: `wall_timeout` and `malformed` records carry requested/clamped
    as null — they are real clamps (the command was replaced by the last
    safe value) but carry no delta. They must count toward the RATE and be
    excluded from the magnitude, never coerced to 0.0, which would silently
    drag the mean toward zero."""
    rows = [
        [_viol("wall_timeout", requested=None, clamped=None)],
        [_viol("position", requested=1.0, clamped=0.4)],
    ]
    s = divergence_summary(violations=rows, commands=2)
    assert s["clamped_commands"] == 2
    assert s["divergence_rate"] == 1.0
    assert s["max_abs_delta"] == pytest.approx(0.6)
    assert s["mean_abs_delta"] == pytest.approx(0.6)  # the null row is not a 0.0
    assert s["records_without_delta"] == 1


def test_zero_commands_refuses_to_invent_a_rate():
    """CON-8 (#267): no commands means no denominator. A rate of 0.0 would
    read as 'the guard never clamped', which is a claim this run cannot
    support."""
    s = divergence_summary(violations=[], commands=0)
    assert s["divergence_rate"] is None
    assert s["labels_coincide"] is None


def test_malformed_rows_are_skipped_not_fatal():
    """The guard's payload is JSON on the wire; a truncated recorder tail can
    leave a partial row. Aggregation must degrade, never crash — this runs
    over evidence, and a traceback here loses the whole summary."""
    rows = [[_viol("position")], "not a list", [{"no_reason": True}], [_viol("velocity")]]
    s = divergence_summary(violations=rows, commands=8)
    assert s["clamped_commands"] == 2
    assert s["by_reason"] == {"position": 1, "velocity": 1}
    assert s["skipped_rows"] == 2


def test_summary_is_json_serializable_for_the_run_manifest():
    """CON-8: the summary is persisted beside the run's other metrics, so it
    must survive a round trip without custom encoders."""
    s = divergence_summary(violations=[[_viol("position")]], commands=3)
    assert json.loads(json.dumps(s)) == s


def test_the_run_manifest_carries_the_divergence_summary(tmp_path):
    """#267 + the #245/#266 lesson: a statistic that is only recomputable in
    principle is not retained in practice — the traces it derives from live
    under gitignored runs/. The rollout persists it into manifest.json, and
    a broken or absent trace loses the STATISTIC, never the run record."""
    from aisle.harness.rollout import _guard_divergence_or_none

    assert _guard_divergence_or_none(tmp_path)["error"] == "no command trace"
    assert _guard_divergence_or_none(tmp_path / "does-not-exist")["error"] == "no command trace"
