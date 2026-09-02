"""Synthetic acceptance tests for SPEC 400 confirmatory statistics.

Expected interval values are fixed independently from the implementation:
the exact-binomial values come from R ``binom.test`` and the zero-event
closed form is ``1 - alpha ** (1 / n)``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from cli_helpers import run_module

from aisle.harness.benchmark_statistics import (
    StatisticsInputError,
    analyze_campaign,
    clopper_pearson_interval,
    power_analysis,
    protocol_core_hash,
    validate_protocol,
)

pytestmark = pytest.mark.unit


def _protocol(*, outcome_type: str = "binary", decision: str = "superiority") -> dict:
    outcome = {
        "field": "session_success" if outcome_type == "binary" else "cost",
        "type": outcome_type,
        "direction": "higher" if outcome_type == "binary" else "lower",
    }
    smallest_effect = {
        "measure": "risk_difference" if outcome_type == "binary" else "mean_difference",
        "value": 0.20 if outcome_type == "binary" else 0.50,
    }
    power = (
        {
            "control_probability": 0.50,
            "treatment_probability": 0.70,
            "test_sidedness": "two-sided",
            "sensitivity_effects": [0.10, 0.20, 0.30],
        }
        if outcome_type == "binary"
        else {
            "standard_deviation": 1.0,
            "mean_difference": -0.50,
            "test_sidedness": "two-sided",
            "sensitivity_effects": [0.25, 0.50, 0.75],
        }
    )
    return {
        "schema_version": "aisle.stats.protocol.v1",
        "protocol_id": "synthetic-protocol-v1",
        "campaign_id": "synthetic-campaign-v1",
        "campaign_phase": "synthetic",
        "primary_estimand": {
            "population": "autonomous coding-agent sessions",
            "contrast": "typed minus monolithic",
            "summary": smallest_effect["measure"],
        },
        "experimental_unit": "session",
        "treatment_arms": ["monolithic", "typed"],
        "control_arm": "monolithic",
        "treatment_arm": "typed",
        "outcome": outcome,
        "smallest_effect": smallest_effect,
        "alpha": 0.05,
        "target_power": 0.80,
        "allocation_ratio": 1.0,
        "stopping_rule": {"type": "fixed", "max_sessions_per_arm": 100},
        "inclusion_rules": ["randomized session with a resolved lifecycle"],
        "exclusion_rules": ["predeclared infrastructure failure only"],
        "analysis_seed": 8142,
        "decision": {
            "kind": decision,
            "margin": 0.10 if decision != "superiority" else None,
            "rationale": "superiority has no equivalence margin"
            if decision == "superiority"
            else None,
        },
        "power": power,
        "bootstrap": {"replicates": 400, "confidence_level": 0.95},
        "strata": ["agent_system"],
        "artifact_claim_threshold": 0.90,
        "survival": {"event_time_field": "accepted_time_s", "censor_time_field": "wall_s"},
        "zero_event": {
            "event_field": "safety_events",
            "exposure_field": "commands",
            "exposure_unit": "command",
            "confidence_level": 0.95,
        },
    }


def _session(
    session_id: str,
    arm: str,
    success: bool | None,
    *,
    agent: str = "agent-a",
    lifecycle: str = "completed",
    included: bool = True,
    reason: str | None = None,
    cost: float = 10.0,
    accepted_time_s: float | None = None,
    wall_s: float = 60.0,
    safety_events: int = 0,
    commands: int = 10,
    artifact_successes: tuple[bool, ...] = (),
) -> dict:
    return {
        "session_id": session_id,
        "protocol_id": "synthetic-protocol-v1",
        "campaign_id": "synthetic-campaign-v1",
        "campaign_phase": "synthetic",
        "treatment": arm,
        "agent_system": agent,
        "temporal_block": "block-1",
        "assignment_status": "randomized",
        "lifecycle_status": lifecycle,
        "inclusion": {"included": included, "reason": reason},
        "budget": {"censored": success is None, "reason": "budget" if success is None else None},
        "outcome": {
            "session_success": success,
            "cost": cost,
            "accepted_time_s": accepted_time_s,
        },
        "costs": {"cost": cost, "tokens": cost * 100},
        "exposure": {
            "safety_events": safety_events,
            "commands": commands,
            "unit": "command",
        },
        "artifacts": [
            {"artifact_id": "held-out-cell", "success": value} for value in artifact_successes
        ],
        "wall_s": wall_s,
    }


def _records(sessions: list[dict]) -> dict:
    return {
        "schema_version": "aisle.stats.records.v1",
        "protocol_id": "synthetic-protocol-v1",
        "campaign_id": "synthetic-campaign-v1",
        "campaign_phase": "synthetic",
        "sessions": sessions,
    }


def test_protocol_requires_complete_consistent_preregistration():
    """STA-1: the protocol requires every frozen design field and refuses
    unresolved or internally inconsistent choices."""
    protocol = _protocol()
    assert validate_protocol(protocol, purpose="power") == []

    for field in (
        "primary_estimand",
        "experimental_unit",
        "treatment_arms",
        "outcome",
        "smallest_effect",
        "alpha",
        "target_power",
        "stopping_rule",
        "inclusion_rules",
        "exclusion_rules",
        "analysis_seed",
        "decision",
    ):
        malformed = copy.deepcopy(protocol)
        malformed.pop(field)
        assert any(field in error for error in validate_protocol(malformed, purpose="power"))

    malformed = copy.deepcopy(protocol)
    malformed["experimental_unit"] = "episode"
    assert "experimental_unit must be session" in validate_protocol(malformed, purpose="power")

    malformed = copy.deepcopy(protocol)
    malformed["decision"] = {"kind": "equivalence", "margin": None}
    assert any("margin" in error for error in validate_protocol(malformed, purpose="power"))

    malformed = copy.deepcopy(protocol)
    malformed["power"]["treatment_probability"] = 0.60
    assert any(
        "smallest_effect" in error for error in validate_protocol(malformed, purpose="power")
    )

    malformed = copy.deepcopy(protocol)
    malformed["allocation_ratio"] = float("nan")
    assert any(
        "allocation_ratio" in error for error in validate_protocol(malformed, purpose="power")
    )


def test_power_supports_binary_and_continuous_session_outcomes():
    """STA-5: power planning reports per-arm n, achieved power, method,
    assumptions, and sensitivity without reading confirmatory outcomes."""
    binary = power_analysis(_protocol())
    assert binary["ok"] is True
    assert binary["method"] == "normal approximation for two independent session proportions"
    assert binary["per_arm_sample_size"] == {"monolithic": 93, "typed": 93}
    assert binary["achieved_power"] >= 0.80
    assert [row["effect"] for row in binary["sensitivity"]] == [0.10, 0.20, 0.30]

    one_sided_protocol = _protocol()
    one_sided_protocol["power"]["test_sidedness"] = "one-sided"
    one_sided = power_analysis(one_sided_protocol)
    assert (
        one_sided["per_arm_sample_size"]["monolithic"] < binary["per_arm_sample_size"]["monolithic"]
    )

    continuous = power_analysis(_protocol(outcome_type="continuous"))
    assert continuous["method"] == "normal approximation for two independent session means"
    assert continuous["per_arm_sample_size"] == {"monolithic": 63, "typed": 63}
    assert continuous["achieved_power"] >= 0.80
    assert continuous["assumptions"]["standard_deviation"] == 1.0


def test_exact_intervals_cover_extremes_and_refute_eight_of_eight_claim():
    """STA-6: exact artifact intervals cover all-success/all-failure and
    show that 8/8 has a one-sided 95% lower bound below 0.90."""
    eight = clopper_pearson_interval(8, 8, confidence_level=0.95, sidedness="lower")
    assert eight["lower"] == pytest.approx(0.687656, abs=1e-6)
    assert eight["upper"] == 1.0
    assert eight["lower"] < 0.90

    none = clopper_pearson_interval(0, 8, confidence_level=0.95, sidedness="two-sided")
    all_success = clopper_pearson_interval(8, 8, confidence_level=0.95)
    assert none["lower"] == 0.0
    assert none["upper"] == pytest.approx(0.369417, abs=1e-6)
    assert all_success["upper"] == 1.0
    assert all_success["lower"] == pytest.approx(0.630583, abs=1e-6)


def test_analyzer_retains_assignments_exclusions_and_reconciles_flow():
    """STA-2, STA-11: every assignment remains in the arm flow, exclusions
    never enter estimates, and randomized counts reconcile by reason."""
    sessions = [
        _session("m1", "monolithic", False),
        _session(
            "m2",
            "monolithic",
            None,
            lifecycle="never_started",
            included=False,
            reason="service outage",
        ),
        _session("t1", "typed", True),
        _session(
            "t2",
            "typed",
            None,
            lifecycle="infrastructure_excluded",
            included=False,
            reason="runner crash",
        ),
    ]
    report = analyze_campaign(_protocol(), _records(sessions))
    assert report["ok"] is True
    assert report["session_flow"]["overall"] == {
        "randomized": 4,
        "started": 3,
        "completed": 2,
        "included": 2,
        "infrastructure_excluded": 1,
        "censored": 0,
        "analyzed": 2,
    }
    assert report["session_flow"]["reasons"] == {
        "runner crash": 1,
        "service outage": 1,
    }
    assert report["binary_effect"]["arms"]["typed"]["n"] == 1
    assert report["binary_effect"]["arms"]["monolithic"]["n"] == 1

    malformed = _records(sessions)
    malformed["sessions"][0]["assignment_status"] = "not_randomized"
    with pytest.raises(StatisticsInputError, match="reconciliation"):
        analyze_campaign(_protocol(), malformed)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda rows: rows.append(copy.deepcopy(rows[0])), "duplicate session_id"),
        (lambda rows: rows[0].update(campaign_id="other"), "campaign_id"),
        (lambda rows: rows[0].update(campaign_phase="pilot"), "campaign_phase"),
        (lambda rows: rows[0].update(protocol_id="other"), "protocol_id"),
        (lambda rows: rows[0].update(treatment="unknown"), "treatment"),
        (lambda rows: rows[0]["outcome"].pop("session_success"), "outcome schema"),
        (lambda rows: rows[0]["costs"].update(extra_cost=1), "cost schema"),
    ],
)
def test_analyzer_refuses_mixed_duplicate_or_schema_drift(mutation, message):
    """STA-3: mixed campaign identities, pilot contamination, duplicates,
    and treatment/schema drift fail closed."""
    sessions = [_session("m1", "monolithic", False), _session("t1", "typed", True)]
    mutation(sessions)
    with pytest.raises(StatisticsInputError, match=message):
        analyze_campaign(_protocol(), _records(sessions))


def test_analyzer_refuses_rows_after_fixed_stopping_rule():
    """STA-3: sessions beyond the frozen fixed stopping rule are rejected."""
    protocol = _protocol()
    protocol["stopping_rule"]["max_sessions_per_arm"] = 1
    records = _records(
        [
            _session("m1", "monolithic", False),
            _session("m2", "monolithic", True),
            _session("t1", "typed", True),
        ]
    )
    with pytest.raises(StatisticsInputError, match="stopping rule"):
        analyze_campaign(protocol, records)


def test_binary_effect_uses_sessions_reports_strata_and_artifact_claim_guard():
    """STA-6, STA-7: binary effects use session rates/Newcombe intervals,
    report predeclared strata, and independently interval nested artifacts."""
    sessions = []
    for index, success in enumerate((False, False, True, False)):
        sessions.append(
            _session(
                f"m{index}",
                "monolithic",
                success,
                agent="agent-a" if index < 2 else "agent-b",
            )
        )
    for index, success in enumerate((True, True, True, False)):
        sessions.append(
            _session(
                f"t{index}",
                "typed",
                success,
                agent="agent-a" if index < 2 else "agent-b",
                artifact_successes=(True, True) if index < 4 else (),
            )
        )
    report = analyze_campaign(_protocol(), _records(sessions))
    effect = report["binary_effect"]
    assert effect["experimental_unit"] == "session"
    assert effect["estimate"] == pytest.approx(0.50)
    assert effect["interval"]["method"] == "Newcombe score"
    # Independent reference: Newcombe method 10 for 3/4 minus 1/4.
    assert effect["interval"]["lower"] == pytest.approx(-0.13548, abs=1e-5)
    assert effect["interval"]["upper"] == pytest.approx(0.78909, abs=1e-5)
    assert set(effect["strata"]) == {"agent-a", "agent-b"}
    assert len(effect["session_points"]) == 8
    assert {point["session_id"] for point in effect["session_points"]} == {
        *(f"m{index}" for index in range(4)),
        *(f"t{index}" for index in range(4)),
    }
    artifact = report["artifact_outcomes"][0]
    assert artifact["successes"] == 8 and artifact["n"] == 8
    assert artifact["claim_above_threshold"] is False
    assert artifact["interval"]["lower"] == pytest.approx(0.687656, abs=1e-6)


def test_clustered_continuous_outcome_bootstraps_whole_sessions_deterministically():
    """STA-7: nested observations are aggregated within sessions and the
    treatment-stratified bootstrap is deterministic from analysis_seed."""
    protocol = _protocol(outcome_type="continuous")
    protocol["outcome"]["source"] = "nested_outcomes"
    protocol["outcome"]["aggregation"] = "mean"
    sessions = [
        _session("m1", "monolithic", False, cost=10.0),
        _session("m2", "monolithic", False, cost=14.0),
        _session("t1", "typed", True, cost=7.0),
        _session("t2", "typed", True, cost=9.0),
    ]
    for session, values in zip(
        sessions,
        ((9.0, 11.0), (12.0, 16.0), (6.0, 8.0), (8.0, 10.0)),
        strict=True,
    ):
        session["nested_outcomes"] = [{"metric": "cost", "value": value} for value in values]
    first = analyze_campaign(protocol, _records(sessions))["continuous_effect"]
    second = analyze_campaign(protocol, _records(sessions))["continuous_effect"]
    assert first == second
    assert first["experimental_unit"] == "session"
    assert first["aggregation"] == "mean of nested observations within each session"
    assert first["estimate"] == pytest.approx(-4.0)
    assert [point["outcome"] for point in first["session_points"]] == [10.0, 14.0, 7.0, 9.0]
    assert first["bootstrap"]["resampling_unit"] == "session"
    assert first["bootstrap"]["stratified_by"] == "treatment"


@pytest.mark.parametrize(
    ("kind", "typed_costs", "expected"),
    [
        ("equivalence", (10.01, 10.02, 10.03, 10.04), True),
        ("equivalence", (11.50, 11.60, 11.70, 11.80), False),
        ("equivalence", (11.0, 11.0, 11.0, 11.0), False),
        ("non_inferiority", (10.00, 10.01, 10.02, 10.03), True),
        ("non_inferiority", (11.0, 11.0, 11.0, 11.0), False),
    ],
)
def test_equivalence_and_noninferiority_use_compatible_intervals(kind, typed_costs, expected):
    """STA-8: equivalence and non-inferiority expose the frozen margin,
    compatible confidence interval, decision rule, and bounded decision."""
    protocol = _protocol(outcome_type="continuous", decision=kind)
    protocol["decision"]["margin"] = 1.0
    sessions = [
        *[
            _session(f"m{i}", "monolithic", True, cost=value)
            for i, value in enumerate((10.0, 10.1, 9.9, 10.0))
        ],
        *[_session(f"t{i}", "typed", True, cost=value) for i, value in enumerate(typed_costs)],
    ]
    decision = analyze_campaign(protocol, _records(sessions))["decision"]
    assert decision["kind"] == kind
    assert decision["margin"] == 1.0
    assert decision["decision"] is expected
    assert decision["rule"]
    assert decision["interval"]["confidence_level"] == (
        pytest.approx(0.90) if kind == "equivalence" else pytest.approx(0.95)
    )


def test_no_significance_result_never_becomes_equivalence():
    """STA-8: a superiority analysis cannot label non-significance as
    equivalence."""
    report = analyze_campaign(
        _protocol(),
        _records(
            [
                _session("m1", "monolithic", True),
                _session("m2", "monolithic", False),
                _session("t1", "typed", True),
                _session("t2", "typed", False),
            ]
        ),
    )
    assert report["decision"] == {
        "kind": "superiority",
        "decision": "not_evaluated_by_equivalence_rule",
    }


def test_survival_summary_retains_right_censoring():
    """STA-9: Kaplan-Meier output retains right-censored sessions and
    exposes the at-risk/event/censor table."""
    sessions = [
        _session("m1", "monolithic", True, accepted_time_s=10.0, wall_s=10.0),
        _session("m2", "monolithic", None, wall_s=20.0),
        _session("t1", "typed", True, accepted_time_s=5.0, wall_s=5.0),
        _session("t2", "typed", None, wall_s=15.0),
    ]
    report = analyze_campaign(_protocol(), _records(sessions))
    survival = report["survival"]
    assert survival["monolithic"]["n"] == 2
    assert survival["monolithic"]["events"] == 1
    assert survival["monolithic"]["censored"] == 1
    assert survival["monolithic"]["table"] == [
        {"time": 10.0, "at_risk": 2, "events": 1, "censored": 0},
        {"time": 20.0, "at_risk": 1, "events": 0, "censored": 1},
    ]
    assert survival["monolithic"]["points"][-1]["survival"] == pytest.approx(0.5)
    assert report["session_flow"]["censor_reasons"] == {"budget": 2}
    assert report["warnings"] == [
        "2 included censored session(s) are retained in flow/survival but excluded from the "
        "primary effect"
    ]


def test_zero_event_upper_bound_uses_declared_exposure_and_fails_closed():
    """STA-10: zero-event claims use a one-sided exact upper bound and
    reject missing or mixed exposure units."""
    sessions = [
        _session("m1", "monolithic", False, commands=40),
        _session("t1", "typed", True, commands=60),
    ]
    result = analyze_campaign(_protocol(), _records(sessions))["zero_event"]
    assert result["events"] == 0
    assert result["exposure"] == 100
    assert result["exposure_unit"] == "command"
    assert result["upper"] == pytest.approx(0.029513, abs=1e-6)

    sessions[1]["exposure"]["unit"] = "episode"
    with pytest.raises(StatisticsInputError, match="exposure unit"):
        analyze_campaign(_protocol(), _records(sessions))


def test_cli_is_con8_json_and_hashes_named_inputs(tmp_path: Path):
    """STA-4: analyze/power are CON-8 CLIs derived from named inputs and
    emit schema, hashes, protocol identity, seed, assumptions, and values."""
    protocol_path = tmp_path / "protocol.json"
    records_path = tmp_path / "records.json"
    output_path = tmp_path / "analysis.json"
    protocol_path.write_text(json.dumps(_protocol(), indent=2) + "\n")
    records_path.write_text(
        json.dumps(
            _records(
                [
                    _session("m1", "monolithic", False),
                    _session("t1", "typed", True),
                ]
            ),
            indent=2,
        )
        + "\n"
    )

    proc = run_module(
        "aisle.harness.cli",
        "stats",
        "analyze",
        "--protocol",
        str(protocol_path),
        "--records",
        str(records_path),
        "--output",
        str(output_path),
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["schema_version"] == "aisle.stats.result.v1"
    assert report["input_hashes"] == {
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "records_sha256": hashlib.sha256(records_path.read_bytes()).hexdigest(),
    }
    assert report["protocol_id"] == "synthetic-protocol-v1"
    assert report["analysis_seed"] == 8142
    assert report["assumptions"]
    assert len(report["execution_environment"]["analysis_implementation_sha256"]) == 64
    assert report["execution_environment"]["python"]
    assert report["execution_environment"]["machine"]
    assert json.loads(output_path.read_text()) == report

    power = run_module("aisle.harness.cli", "stats", "power", "--protocol", str(protocol_path))
    assert power.returncode == 0, power.stderr
    assert json.loads(power.stdout)["ok"] is True

    original_protocol = protocol_path.read_bytes()
    collision = run_module(
        "aisle.harness.cli",
        "stats",
        "power",
        "--protocol",
        str(protocol_path),
        "--output",
        str(protocol_path),
    )
    assert collision.returncode != 0
    assert json.loads(collision.stdout)["error"] == "output path collides with an input"
    assert protocol_path.read_bytes() == original_protocol


def test_cli_refuses_invalid_input_as_json(tmp_path: Path):
    """STA-1, STA-3, STA-4: invalid named inputs fail closed with one JSON
    object on stdout and a non-zero exit status."""
    bad = tmp_path / "bad.json"
    bad.write_text("{}\n")
    proc = run_module("aisle.harness.cli", "stats", "power", "--protocol", str(bad))
    assert proc.returncode != 0
    refusal = json.loads(proc.stdout)
    assert refusal["ok"] is False
    assert refusal["error"] == "invalid protocol"
    assert refusal["details"]

    non_object = tmp_path / "list.json"
    non_object.write_text("[]\n")
    validation = run_module("aisle.harness.cli", "stats", "validate", "--protocol", str(non_object))
    assert validation.returncode != 0
    assert json.loads(validation.stdout)["errors"] == ["protocol must be a JSON object"]

    missing_argument = run_module("aisle.harness.cli", "stats", "analyze", "--protocol", str(bad))
    assert missing_argument.returncode != 0
    argument_refusal = json.loads(missing_argument.stdout)
    assert argument_refusal["ok"] is False
    assert argument_refusal["error"] == "invalid arguments"
    assert "--records" in argument_refusal["details"][0]


def test_confirmatory_collection_requires_independent_resolved_review(tmp_path: Path):
    """STA-12: confirmatory freeze requires an independent signed review,
    resolved findings, frozen hashes, and an external timestamp."""
    protocol = _protocol()
    protocol["campaign_phase"] = "confirmatory"
    errors = validate_protocol(protocol, purpose="freeze")
    assert any("independent statistical review" in error for error in errors)
    path = tmp_path / "confirmatory.json"
    path.write_text(json.dumps(protocol))
    refusal = run_module(
        "aisle.harness.cli",
        "stats",
        "validate",
        "--protocol",
        str(path),
        "--purpose",
        "freeze",
    )
    assert refusal.returncode != 0
    assert json.loads(refusal.stdout)["ok"] is False

    protocol["freeze"] = {
        "status": "frozen",
        "external_timestamp": "2026-09-01T12:00:00Z",
        "artifact_hashes": {
            "protocol_core": "sha256:REPLACE_AFTER_CORE_HASH",
            "analysis_script": "sha256:" + "2" * 64,
            "fixtures": "sha256:" + "3" * 64,
        },
        "review": {
            "reviewer_id": "independent-statistician-1",
            "reviewer_role": "independent statistical reviewer",
            "independent_from_analyzer_author": True,
            "signed_at": "2026-09-01T11:00:00Z",
            "signature": "external-signature-placeholder-for-synthetic-fixture",
            "findings": [
                {"id": "SR-1", "disposition": "resolved", "resolution": "fixture corrected"}
            ],
            "limitations_reviewed": True,
        },
    }
    protocol["freeze"]["artifact_hashes"]["protocol_core"] = "sha256:" + protocol_core_hash(
        protocol
    )
    wrong_hash = copy.deepcopy(protocol)
    wrong_hash["freeze"]["artifact_hashes"]["protocol_core"] = "sha256:" + "0" * 64
    assert any(
        "protocol_core hash" in error for error in validate_protocol(wrong_hash, purpose="freeze")
    )
    assert validate_protocol(protocol, purpose="freeze") == []
    path.write_text(json.dumps(protocol))
    accepted = run_module(
        "aisle.harness.cli",
        "stats",
        "validate",
        "--protocol",
        str(path),
        "--purpose",
        "freeze",
    )
    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["ok"] is True
