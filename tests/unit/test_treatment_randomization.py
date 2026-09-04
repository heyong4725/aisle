"""Acceptance tests for SPEC 420 controller randomization and host-load records."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aisle.harness.treatment_randomization import (
    HostLoadRule,
    RandomizationError,
    classify_host_load,
    create_sealed_plan,
    make_host_load_record,
    reveal_assignment,
    run_randomization_capability_audit,
    sample_host_load,
    write_randomization_capability_audit,
)

pytestmark = pytest.mark.unit
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SEED = "0123456789abcdef" * 4


def _reveal_all(plan, count: int) -> list[dict]:
    history = []
    for index in range(count):
        history.append(reveal_assignment(plan, index, history))
    return history


def test_public_plan_is_balanced_committed_and_conceals_order_and_seed():
    """TRT-8: the participant-visible preassignment record reveals no assignment."""
    plan = create_sealed_plan(("typed", "monolithic"), ("block-01", "block-02"), _SEED)

    public = plan.public_commitment()

    assert public["schema_version"] == "aisle.randomization-commitment.v1"
    assert public["arms"] == ["monolithic", "typed"]
    assert public["temporal_blocks"] == ["block-01", "block-02"]
    assert public["assignments"] == 4
    assert (
        public["randomization_seed_commitment"] == hashlib.sha256(bytes.fromhex(_SEED)).hexdigest()
    )
    assert len(public["plan_commitment"]) == 64
    rendered = json.dumps(public, sort_keys=True)
    assert _SEED not in rendered
    assert "assignment_order" not in rendered
    assert "within_block_position" not in rendered


def test_same_seed_reproduces_and_different_seed_changes_the_plan():
    """TRT-8: frozen seed and algorithm reproduce exact balanced assignments."""
    first = create_sealed_plan(("typed", "monolithic"), ("b1", "b2", "b3"), _SEED)
    again = create_sealed_plan(("typed", "monolithic"), ("b1", "b2", "b3"), _SEED)
    different = create_sealed_plan(("typed", "monolithic"), ("b1", "b2", "b3"), "f" * 64)

    first_rows = _reveal_all(first, 6)
    again_rows = _reveal_all(again, 6)
    different_rows = _reveal_all(different, 6)
    assert [row["arm"] for row in first_rows] == [row["arm"] for row in again_rows]
    assert [row["arm"] for row in first_rows] != [row["arm"] for row in different_rows]
    for block in ("b1", "b2", "b3"):
        assert sorted(row["arm"] for row in first_rows if row["temporal_block"] == block) == [
            "monolithic",
            "typed",
        ]


def test_assignment_reveal_is_sequential_and_contains_no_future_arm():
    """TRT-8: only the current assignment is revealed after prior records verify."""
    plan = create_sealed_plan(("typed", "monolithic"), ("b1", "b2"), _SEED)
    history = []
    for index in range(4):
        row = reveal_assignment(plan, index, history)
        assert row["assignment_index"] == index
        assert row["plan_commitment"] == plan.public_commitment()["plan_commitment"]
        assert "next_arm" not in row and "future_assignments" not in row
        history.append(row)

    with pytest.raises(RandomizationError, match="sequential"):
        reveal_assignment(plan, 3, history[:1])
    tampered = [dict(history[0], arm="invented")]
    with pytest.raises(RandomizationError, match="history"):
        reveal_assignment(plan, 1, tampered)


@pytest.mark.parametrize(
    ("arms", "blocks", "seed", "message"),
    [
        ("typed", ("b1",), _SEED, "arms"),
        (("typed",), ("b1",), _SEED, "arms"),
        (("typed", "typed"), ("b1",), _SEED, "duplicate"),
        (("typed", "mono\x00lithic"), ("b1",), _SEED, "resolved"),
        (("typed", "monolithic"), (), _SEED, "block"),
        (("typed", "monolithic"), ("b1", "b1"), _SEED, "duplicate"),
        (("typed", "monolithic"), ("b1",), "short", "seed"),
    ],
)
def test_ambiguous_randomization_inputs_fail_closed(arms, blocks, seed, message):
    """TRT-8: unresolved arms, blocks, or seed cannot produce assignments."""
    with pytest.raises(RandomizationError, match=message):
        create_sealed_plan(arms, blocks, seed)


def _rule() -> HostLoadRule:
    return HostLoadRule(high_normalized_load=1.0, max_normalized_shift=0.25)


def test_frozen_host_load_rule_records_pre_and_post_and_surfaces_anomalies():
    """TRT-8: pre/post observations use one rule and load anomalies stay visible."""
    rule = _rule()
    before = make_host_load_record(
        "preflight",
        rule,
        load_average=(2.0, 1.5, 1.0),
        logical_cpus=8,
        observed_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
    )
    after = make_host_load_record(
        "postflight",
        rule,
        load_average=(12.0, 8.0, 4.0),
        logical_cpus=8,
        observed_at=datetime(2026, 9, 1, 11, 0, tzinfo=UTC),
    )

    audit = classify_host_load(before, after, rule)

    assert before["sampling_rule_sha256"] == after["sampling_rule_sha256"]
    assert audit["anomaly"] is True
    assert audit["anomaly_codes"] == ["HIGH_POSTFLIGHT_LOAD", "LOAD_SHIFT"]
    assert audit["preflight"]["normalized_load_1m"] == 0.25
    assert audit["postflight"]["normalized_load_1m"] == 1.5


def test_host_load_rule_or_record_drift_refuses_classification():
    """TRT-8: phase, rule, CPU, and finite load fields fail closed when invalid."""
    rule = _rule()
    before = make_host_load_record(
        "preflight",
        rule,
        load_average=(1.0, 1.0, 1.0),
        logical_cpus=4,
        observed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    after = make_host_load_record(
        "postflight",
        rule,
        load_average=(1.0, 1.0, 1.0),
        logical_cpus=4,
        observed_at=datetime(2026, 9, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(RandomizationError, match="sampling rule"):
        classify_host_load(before, after, HostLoadRule(2.0, 0.25))
    with pytest.raises(RandomizationError, match="logical_cpus"):
        make_host_load_record("preflight", rule, load_average=(1.0, 1.0, 1.0), logical_cpus=0)
    with pytest.raises(RandomizationError, match="phase"):
        make_host_load_record("during", rule, load_average=(1.0, 1.0, 1.0), logical_cpus=4)


def test_live_host_sampler_records_platform_observation():
    """TRT-8: the controller can capture a real machine-readable boundary sample."""
    record = sample_host_load("preflight", _rule())

    assert record["phase"] == "preflight"
    assert record["logical_cpus"] >= 1
    assert len(record["load_average"]) == 3
    assert record["observed_at"].endswith("+00:00")


def test_synthetic_audit_is_complete_reproducible_and_explicitly_unscored():
    """TRT-8: capability evidence covers concealment, balance, load, and anomaly."""
    report = run_randomization_capability_audit()

    assert report["capability_pass"] is True
    assert report["confirmatory_ready"] is False
    assert report["evidence_class"] == "synthetic_unscored_randomization_capability"
    assert report["summary"] == {
        "assignments": 6,
        "balanced_blocks": 3,
        "checks": 7,
        "detection_rate": 1.0,
    }
    assert all(row["passed"] for row in report["cases"])
    assert report["synthetic_anomaly_probe"]["anomaly"] is True
    assert report["session_id"].startswith("randomization-capability-")
    assert len(report["implementation_sha256"]) == 64
    rendered = json.dumps(report, sort_keys=True)
    assert _SEED not in rendered


def test_audit_writer_cli_and_primary_artifact_are_non_overwriting_and_bound(tmp_path: Path):
    """TRT-8: retained evidence is machine-readable, source-bound, and unscored."""
    output = tmp_path / "audit.json"
    report = write_randomization_capability_audit(output)
    assert json.loads(output.read_text()) == report
    with pytest.raises(RandomizationError, match="already exists"):
        write_randomization_capability_audit(output)

    cli_output = tmp_path / "cli.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.treatment_randomization",
            "audit-synthetic",
            "--output",
            str(cli_output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["confirmatory_ready"] is False

    primary = (
        _PROJECT_ROOT
        / "analysis"
        / "treatment-integrity"
        / "randomization-capability"
        / "audit-name-hardened.json"
    )
    retained = json.loads(primary.read_text())
    source = _PROJECT_ROOT / "src" / "aisle" / "harness" / "treatment_randomization.py"
    assert retained["implementation_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert retained["confirmatory_ready"] is False
