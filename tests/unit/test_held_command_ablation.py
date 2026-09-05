"""Fixed-proposal guard ablation on a fake driver (SFE-9, SFE-10, SFE-11,
SFE-12; issue #351).

The corpus is deterministic from its seed, every family is present with
legal negative controls and a watchdog-silence trace, both arms log the
identical decision, only the forwarded command differs, the trace is the
unit, containment invalidates a pair, and a legal trace changed by guard_on
blocks the study.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from cli_helpers import run_module

from aisle.harness.held_command import (
    FAMILIES,
    HeldCommandError,
    build_corpus,
    replay_trace,
    run_ablation,
)
from aisle.nodes.budget_guard import load_limits

pytestmark = pytest.mark.unit

LIMITS = load_limits("franka")


@pytest.fixture(scope="module")
def corpus() -> dict:
    return build_corpus(LIMITS, embodiment="franka", seed=11, per_family=3)


def test_corpus_is_deterministic_and_covers_every_family(corpus):
    """SFE-9 / SFE-11 / CON-5: same seed, same bytes and hashes; every
    family present with legal negative controls and a watchdog trace whose
    silence exceeds the wall deadline."""
    again = build_corpus(LIMITS, embodiment="franka", seed=11, per_family=3)
    assert json.dumps(corpus, sort_keys=True) == json.dumps(again, sort_keys=True)
    assert (
        build_corpus(LIMITS, embodiment="franka", seed=12, per_family=3)["corpus_hash"]
        != (corpus["corpus_hash"])
    )
    families = {t["family"] for t in corpus["traces"]}
    assert families == set(FAMILIES)
    legal = [t for t in corpus["traces"] if t["family"] == "legal_negative_control"]
    assert legal and all(not t["declared_at_risk"] for t in legal)
    watchdog = next(t for t in corpus["traces"] if t["family"] == "held_motion_watchdog_silence")
    assert max(watchdog["stamps_s"]) > LIMITS.wall_timeout_s
    assert all(t["expected_class"] for t in corpus["traces"])


def test_both_arms_log_identical_decisions_and_differ_only_in_forwarding(corpus):
    """SFE-10: observe-only computes the identical would-have decision and
    forwards the raw proposal; guard_on forwards the safe command."""
    trace = next(t for t in corpus["traces"] if t["family"] == "joint_position_limit")
    on = replay_trace(trace, "guard_on", LIMITS)
    off = replay_trace(trace, "guard_observe_only", LIMITS)
    assert on["decisions"] == off["decisions"]
    assert on["interventions"] > 0
    assert on["driver_received"]["total"] == 0
    assert off["driver_received"]["by_class"]["position"] >= 1
    assert on["received_hash"] != off["received_hash"]
    with pytest.raises(HeldCommandError, match="unknown arm"):
        replay_trace(trace, "guard_off", LIMITS)


def test_legal_controls_pass_untouched_in_both_arms(corpus):
    """SFE-11: legal traces produce zero interventions and zero
    driver-received violations in either arm."""
    for trace in (t for t in corpus["traces"] if t["family"] == "legal_negative_control"):
        for arm in ("guard_on", "guard_observe_only"):
            replayed = replay_trace(trace, arm, LIMITS)
            assert replayed["interventions"] == 0, (trace["trace_id"], arm)
            assert replayed["driver_received"]["total"] == 0, (trace["trace_id"], arm)


def test_ablation_reports_per_trace_paired_effect_with_strata(corpus):
    """SFE-12: the trace is the unit; every pair is retained; at-risk arms
    carry exact intervals and a seeded bootstrap; strata by class; the
    study is ok when no blocker fires."""
    result = run_ablation(corpus, LIMITS, analysis_seed=5)
    assert result["ok"] is True and result["blockers"] == []
    assert result["flow"] == {
        "pairs": 18,
        "included": 18,
        "excluded": 0,
        "at_risk": 15,
        "legal_controls": 3,
    }
    primary = result["primary"]
    assert primary["guard_on_any_violation"]["upper"] < 0.25
    assert primary["observe_only_any_violation"]["lower"] > 0.5
    assert primary["risk_difference_any_violation"] < 0
    assert primary["paired_difference_count"]["ci95"][1] < 0
    assert set(result["strata"]) == set(FAMILIES)
    assert result["secondary"]["watchdog_hold_pairs"] == 3
    assert result["secondary"]["collisions"].startswith("unmeasured")
    again = run_ablation(corpus, LIMITS, analysis_seed=5)
    assert again["result_hash"] == result["result_hash"]


def test_hash_drift_containment_and_legal_alteration_are_refusals_or_blocks(corpus):
    """SFE-9 / SFE-10 / SFE-11: a mutated trace fails its hash; a pair that
    touches the containment envelope is excluded with its reason; a legal
    trace altered by guard_on blocks the study with ok:false."""
    from aisle.harness.held_command import _containment_hit, _hash

    drifted = copy.deepcopy(corpus)
    drifted["traces"][0]["joint_proposals"][0][0] += 0.001
    with pytest.raises(HeldCommandError, match="corpus hash does not match"):
        run_ablation(drifted, LIMITS, analysis_seed=1)
    drifted["corpus_hash"] = _hash({k: v for k, v in drifted.items() if k != "corpus_hash"})
    with pytest.raises(HeldCommandError, match="trace hash drift"):
        run_ablation(drifted, LIMITS, analysis_seed=1)

    far = copy.deepcopy(corpus)
    trace = far["traces"][1]
    q = np.asarray(trace["joint_proposals"][9], dtype=np.float32)
    grid = np.linspace(-1.0, 1.0, 9)
    found = None  # search shoulder/elbow poses until the flange leaves the grown envelope
    for a in grid:
        for b in grid:
            probe = q.copy()
            probe[1] = float(LIMITS.q_min[1] + (a + 1) / 2 * (LIMITS.q_max[1] - LIMITS.q_min[1]))
            probe[3] = float(LIMITS.q_min[3] + (b + 1) / 2 * (LIMITS.q_max[3] - LIMITS.q_min[3]))
            if _containment_hit(probe, LIMITS):
                found = probe
                break
        if found is not None:
            break
    assert found is not None, "containment envelope must be reachable"
    q = found
    trace["joint_proposals"][10] = [float(v) for v in q]

    trace["trace_hash"] = _hash(
        {k: v for k, v in trace.items() if k not in ("trace_id", "trace_hash")}
    )
    far["corpus_hash"] = _hash({k: v for k, v in far.items() if k != "corpus_hash"})
    result = run_ablation(far, LIMITS, analysis_seed=1)
    excluded = [p for p in result["pairs"] if p["excluded"]]
    assert excluded and excluded[0]["exclusion_reason"] == "emergency containment activated"

    altered = copy.deepcopy(corpus)
    legal = next(t for t in altered["traces"] if t["family"] == "legal_negative_control")
    legal["joint_proposals"][5][0] = float(LIMITS.q_max[0] + 1.0)
    legal["trace_hash"] = _hash(
        {k: v for k, v in legal.items() if k not in ("trace_id", "trace_hash")}
    )
    altered["corpus_hash"] = _hash({k: v for k, v in altered.items() if k != "corpus_hash"})
    blocked = run_ablation(altered, LIMITS, analysis_seed=1)
    assert blocked["ok"] is False
    assert any("legal trace altered by guard_on" in b for b in blocked["blockers"])


def test_cli_corpus_and_ablate_follow_con8(tmp_path):
    """CON-8: JSON on stdout, exit 0 iff ok, corpus written then consumed."""
    corpus_path = tmp_path / "corpus.json"
    proc = run_module(
        "aisle.harness.cli",
        "exposure",
        "corpus",
        "--seed",
        "3",
        "--per-family",
        "2",
        "--output",
        str(corpus_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["ok"] is True
    proc = run_module(
        "aisle.harness.cli",
        "exposure",
        "ablate",
        "--corpus",
        str(corpus_path),
        "--analysis-seed",
        "9",
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"] is True and out["flow"]["pairs"] == 12 and "pairs" not in out
