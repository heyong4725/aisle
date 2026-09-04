"""Acceptance tests for SPEC 420 treatment-component mutation auditing."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import aisle.harness.treatment_mutations as treatment_mutations
from aisle.harness.treatment_integrity import _REQUIRED_PATHS
from aisle.harness.treatment_mutations import (
    MutationAuditError,
    mutation_components,
    run_treatment_mutation_audit,
    summarize_mutation_cases,
    write_treatment_mutation_audit,
)

pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).parents[2]


def test_mutation_inventory_is_exactly_the_complete_trt1_tuple():
    """TRT-13: every TRT-1 component has independent missing and drift mutations."""
    components = mutation_components()

    assert components == tuple(_REQUIRED_PATHS)
    assert len(components) == 40
    assert len(set(components)) == len(components)


def test_audit_inventory_is_independent_and_detector_inventory_drift_fails_closed(monkeypatch):
    """TRT-13: detector requirement deletion cannot erase its own mutation case."""
    independent = mutation_components()

    monkeypatch.setattr(treatment_mutations, "_REQUIRED_PATHS", independent[:-1])

    with pytest.raises(MutationAuditError, match="detector treatment inventory differs"):
        run_treatment_mutation_audit()


def test_every_missing_and_drifted_component_is_detected_independently():
    """TRT-13: no critical treatment-component mutation survives the audit."""
    report = run_treatment_mutation_audit()
    cases = report["mutation_cases"]

    assert len(cases) == 80
    assert {(case["component"], case["mutation_kind"]) for case in cases} == {
        (component, kind) for component in mutation_components() for kind in ("drift", "missing")
    }
    assert all(case["critical"] is True for case in cases)
    assert all(case["detected"] is True for case in cases)
    assert all(case["mutation_id"].startswith("TRT13-") for case in cases)
    assert report["summary"]["mutations_total"] == 80
    assert report["summary"]["mutations_detected"] == 80
    assert report["summary"]["detection_rate"] == 1.0
    assert report["summary"]["by_kind"] == {
        "drift": {"detected": 40, "detection_rate": 1.0, "total": 40},
        "missing": {"detected": 40, "detection_rate": 1.0, "total": 40},
    }
    assert report["summary"]["surviving_blind_spots"] == []


def test_missing_mutations_refuse_preflight_and_drift_mutations_exclude_postflight():
    """TRT-13: mutation stage and disposition remain visible, not only pass counts."""
    report = run_treatment_mutation_audit()
    missing = [case for case in report["mutation_cases"] if case["mutation_kind"] == "missing"]
    drift = [case for case in report["mutation_cases"] if case["mutation_kind"] == "drift"]

    assert all(case["stage"] == "preflight" for case in missing)
    assert all(case["observed"] == "refusal" for case in missing)
    assert all(case["stage"] == "postflight" for case in drift)
    assert all(case["observed"] == "infrastructure_exclusion" for case in drift)
    assert all(case["exclusion_reasons"] for case in drift)


def test_false_alarm_controls_are_reported_and_pass_cleanly():
    """TRT-13: unchanged and irrelevant-file controls quantify false alarms."""
    report = run_treatment_mutation_audit()

    assert len(report["false_alarm_cases"]) >= 2
    assert all(case["false_alarm"] is False for case in report["false_alarm_cases"])
    assert report["summary"]["false_alarm_cases"] == len(report["false_alarm_cases"])
    assert report["summary"]["false_alarms"] == 0
    assert report["summary"]["false_alarm_rate"] == 0.0


def test_summary_fails_closed_on_a_surviving_critical_mutation():
    """TRT-13: one surviving critical mutation blocks the capability verdict."""
    cases = [
        {
            "component": "model.served_identity",
            "critical": True,
            "detected": False,
            "mutation_id": "TRT13-drift-model-served-identity",
            "mutation_kind": "drift",
        },
        {
            "component": "budget.ceiling",
            "critical": True,
            "detected": True,
            "mutation_id": "TRT13-missing-budget-ceiling",
            "mutation_kind": "missing",
        },
    ]
    controls = [{"control_id": "unchanged", "false_alarm": False}]

    summary = summarize_mutation_cases(cases, controls)

    assert summary["detection_rate"] == 0.5
    assert summary["surviving_blind_spots"] == ["TRT13-drift-model-served-identity"]
    assert summary["critical_survivor_blocks_confirmatory"] is True
    assert summary["capability_pass"] is False


def test_report_is_synthetic_source_bound_and_contains_no_treatment_secrets():
    """TRT-13: mutation evidence is auditable without exposing hidden material."""
    report = run_treatment_mutation_audit()
    rendered = json.dumps(report, sort_keys=True)

    assert report["schema_version"] == "aisle.treatment-mutation-audit.v1"
    assert report["evidence_class"] == "synthetic_unscored_treatment_mutation_audit"
    assert report["confirmatory_ready"] is False
    assert report["capability_pass"] is True
    assert report["environment"]["os"]
    assert report["randomization"]["seed"] is None
    assert "synthetic-hidden-sentinel" not in rendered
    assert "refresh_token" not in rendered
    assert len(report["source_sha256"]) == 64


def test_writer_cli_and_primary_artifact_are_non_overwriting_and_bound(tmp_path: Path):
    """TRT-13: the mutation report is reproducible, immutable, and implementation-bound."""
    output = tmp_path / "audit.json"
    report = write_treatment_mutation_audit(output)
    assert json.loads(output.read_text()) == report
    with pytest.raises(MutationAuditError, match="already exists"):
        write_treatment_mutation_audit(output)

    cli_output = tmp_path / "cli.json"
    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.treatment_mutations",
            "audit",
            "--output",
            str(cli_output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout)["confirmatory_ready"] is False

    primary = (
        _PROJECT_ROOT / "analysis" / "treatment-integrity" / "mutation-capability" / "audit.json"
    )
    retained = json.loads(primary.read_text())
    source = _PROJECT_ROOT / "src" / "aisle" / "harness" / "treatment_mutations.py"
    assert retained["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert retained["summary"]["mutations_detected"] == 80
    assert retained["summary"]["false_alarms"] == 0
