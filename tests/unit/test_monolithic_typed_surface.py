from pathlib import Path

import pytest

from aisle.harness.fault_bank import (
    FaultBankError,
    validate_activation_record,
    validate_bank_lifecycle,
    validate_calibration_records,
    validate_conformance_matrix,
    validate_fault_manifest,
    validate_injection_request,
    validate_opaque_assignment,
    validate_paired_efficacy,
    validate_participant_surface,
    validate_reveal_replay,
    validate_safety_assets,
    validate_sealed_ledger,
    validate_sealed_location,
    validate_sham_parity,
)
from aisle.harness.monolithic import (
    TypedSurfaceError,
    classify_bypass_attempt,
    validate_artifact_hashes,
    validate_broker_route,
    validate_campaign_purpose,
    validate_common_evidence,
    validate_conformance,
    validate_expert_artifacts,
    validate_freeze_record,
    validate_interface_map,
    validate_matched_treatment,
    validate_monolithic_surface,
    validate_parity_protocol,
    validate_trusted_preflight,
    validate_typed_graph,
    validate_typed_surface,
)
from aisle.harness.safety_exposure import (
    SafetyExposureError,
    validate_exposure_analysis,
    validate_exposure_hardware_boundary,
    validate_exposure_record,
    validate_fixed_trace_protocol,
    validate_observe_only_mode,
    validate_occurrence_audit,
    validate_paired_analysis,
    validate_proposal_accounting,
    validate_raw_retention,
    validate_source_strata,
    validate_trace_corpus,
    validate_zero_event_bound,
)
from aisle.harness.semantic_authorization import (
    SemanticAuthorizationError,
    validate_permit,
    validate_stage_gates,
)
from aisle.harness.threat_model import (
    ThreatModelError,
    validate_attack_catalog,
    validate_authority_audit,
    validate_bypass_report,
    validate_conformance_evidence,
    validate_evidence_boundary,
    validate_gateway_contract,
    validate_matched_profiles,
    validate_residual_paths,
    validate_review_record,
    validate_scoped_claim,
    validate_threat_model,
)

pytestmark = pytest.mark.unit


def test_semantic_permit_rejects_replay():
    with pytest.raises(SemanticAuthorizationError, match="replayable"):
        validate_permit({"permit_id": "p", "task_id": "t", "credential_epoch": 1, "used": True})


def test_stage_gates_require_order_and_renewal():
    with pytest.raises(SemanticAuthorizationError, match="order"):
        validate_stage_gates(
            [
                {"stage": "carry", "renewed": True},
                {"stage": "grasp", "renewed": True},
                {"stage": "delivery", "renewed": True},
            ],
            True,
        )


def test_raw_retention_requires_immutable_hash():
    with pytest.raises(SafetyExposureError, match="retention"):
        validate_raw_retention([{"record_id": "r", "sha256": "bad", "retained": True}])


def test_occurrence_audit_rejects_missing_denominator():
    with pytest.raises(SafetyExposureError, match="occurrence"):
        validate_occurrence_audit(1, 0, ["r"])


def test_exposure_boundary_rejects_physical_without_hardware():
    with pytest.raises(SafetyExposureError, match="hardware"):
        validate_exposure_hardware_boundary("physical", False)


def test_trace_corpus_requires_watchdog_classification():
    with pytest.raises(SafetyExposureError, match="corpus"):
        validate_trace_corpus([{"trace_id": "t", "legal": True, "violation": False}])


def test_paired_analysis_requires_uncertainty():
    with pytest.raises(SafetyExposureError, match="paired"):
        validate_paired_analysis(
            {"estimate": 1.0, "uncertainty": -1, "excluded": [], "unit": "episode"}
        )


def test_observe_only_mode_rejects_writes():
    with pytest.raises(SafetyExposureError, match="contained"):
        validate_observe_only_mode(
            {"authority": "observe-only", "containment": True, "writes_allowed": True}
        )


def test_exposure_analysis_rejects_silent_omission():
    with pytest.raises(SafetyExposureError, match="exhaustive"):
        validate_exposure_analysis(["e1", "e2"], ["e1"])


def test_source_strata_requires_provenance_and_rate():
    with pytest.raises(SafetyExposureError, match="stratum"):
        validate_source_strata([{"source": "sensor"}])


def test_zero_event_bound_rejects_invalid_denominator():
    with pytest.raises(SafetyExposureError, match="denominator"):
        validate_zero_event_bound(0, 0, 0)


def test_fixed_trace_protocol_requires_frozen_identity():
    with pytest.raises(SafetyExposureError, match="frozen"):
        validate_fixed_trace_protocol(
            {"trace_id": "t", "seeds": [1], "randomized": True, "frozen": False}
        )


def test_exposure_record_requires_layer_and_unit():
    with pytest.raises(SafetyExposureError, match="incomplete"):
        validate_exposure_record({"session_id": "s", "layer": "claim", "unit": "episode"})


def test_proposal_accounting_requires_decision_and_correlation():
    with pytest.raises(SafetyExposureError, match="accounting"):
        validate_proposal_accounting([{"proposal_id": "p", "decision": "accepted"}])


def test_residual_paths_require_disposition():
    with pytest.raises(ThreatModelError, match="disposition"):
        validate_residual_paths([{"path": "alternate", "disposition": "unknown", "rationale": "x"}])


def test_evidence_boundary_rejects_physical_without_hardware():
    with pytest.raises(ThreatModelError, match="hardware"):
        validate_evidence_boundary("physical", False)


def test_matched_profiles_reject_different_seed_sets():
    profile = {
        "tasks": ["t1"],
        "seeds": [1],
        "resource_ceiling": "fixed",
        "access_boundary": "sealed",
    }
    with pytest.raises(ThreatModelError, match="differ"):
        validate_matched_profiles(profile, {**profile, "seeds": [2]})


def test_bypass_report_requires_disposition_and_evidence():
    with pytest.raises(ThreatModelError, match="accounting"):
        validate_bypass_report([{"attempt": "direct", "disposition": "blocked", "evidence": ""}])


def test_review_record_requires_hash_and_disposition():
    with pytest.raises(ThreatModelError, match="hash"):
        validate_review_record(
            {"reviewer": "independent", "artifact_sha256": "bad", "disposition": "accepted"}
        )


def test_authority_audit_rejects_unreconciled_receipt():
    with pytest.raises(ThreatModelError, match="unreconciled"):
        validate_authority_audit(["a1", "a2"], ["a1"])


def test_attack_catalog_requires_explicit_flags():
    with pytest.raises(ThreatModelError, match="incomplete"):
        validate_attack_catalog([{"name": "direct", "class": "route"}])


def test_conformance_evidence_requires_fake_driver_provenance():
    with pytest.raises(ThreatModelError, match="provenance"):
        validate_conformance_evidence(
            {
                "runner": "production",
                "evidence_kind": "synthetic",
                "passed": True,
                "artifacts": ["a"],
            }
        )


def test_fault_manifest_requires_diverse_sealed_cells():
    cell = {
        "id": "a",
        "family": "perception",
        "mode": "sham",
        "target": "camera",
        "sha256": "a" * 64,
        "repair": "novel",
    }
    with pytest.raises(FaultBankError, match="diversity"):
        validate_fault_manifest({"schema": "aisle.fault-bank.v1", "version": "v1", "cells": [cell]})


def test_opaque_assignment_rejects_fault_metadata():
    with pytest.raises(FaultBankError, match="opaque"):
        validate_opaque_assignment({"session": "s", "seed": "1", "handle": "fault-camera"})


def test_sealed_location_rejects_worktree_path(tmp_path: Path):
    with pytest.raises(FaultBankError, match="worktree"):
        validate_sealed_location(tmp_path / "bank.json", tmp_path)


def test_participant_surface_rejects_fault_metadata():
    with pytest.raises(FaultBankError, match="leaks"):
        validate_participant_surface(["status=ok", "target=gripper"])


def test_injector_requires_atomic_content_addressed_request():
    with pytest.raises(FaultBankError, match="atomically"):
        validate_injection_request(
            {
                "handle": "assignment:" + "a" * 64,
                "preimage_sha256": "b" * 64,
                "postimage_sha256": "c" * 64,
                "atomic": False,
            }
        )


def test_sham_parity_rejects_different_timing_surface():
    with pytest.raises(FaultBankError, match="differ"):
        validate_sham_parity(
            {"surface": "controller", "timing": "t1", "retention": "sealed"},
            {"surface": "controller", "timing": "t2", "retention": "sealed"},
        )


def test_paired_efficacy_requires_clean_and_degraded_records():
    with pytest.raises(FaultBankError, match="paired"):
        validate_paired_efficacy([{"cell": "c1", "condition": "clean", "outcome": "ok"}])


def test_calibration_requires_excluded_pilot_purpose():
    with pytest.raises(FaultBankError, match="excluded"):
        validate_calibration_records("scoring", [{"attempt": "a", "outcome": "ok"}])


def test_safety_assets_reject_oracle_access():
    with pytest.raises(FaultBankError, match="oracle"):
        validate_safety_assets(
            {
                "allowed_targets": ["arm"],
                "allowed_operators": ["stop"],
                "frozen": True,
                "oracle_access": True,
            }
        )


def test_activation_record_rejects_assignment_drift():
    with pytest.raises(FaultBankError, match="drifted"):
        validate_activation_record(
            {"session": "s", "handle": "assignment:" + "b" * 64, "activated": True},
            {"session": "s", "handle": "assignment:" + "a" * 64},
        )


def test_sealed_ledger_requires_every_assignment():
    with pytest.raises(FaultBankError, match="incomplete"):
        validate_sealed_ledger(
            [
                {
                    "session": "s",
                    "handle": "assignment:" + "a" * 64,
                    "activated": True,
                    "append_only": True,
                }
            ],
            {"assignment:" + "a" * 64, "assignment:" + "b" * 64},
        )


def test_reveal_replay_rejects_commitment_mismatch():
    with pytest.raises(FaultBankError, match="reconstruct"):
        validate_reveal_replay(
            "assignment:" + "a" * 64, "assignment:" + "b" * 64, "assignment:" + "b" * 64
        )


def test_conformance_matrix_requires_corruption_case():
    with pytest.raises(FaultBankError, match="conformance"):
        validate_conformance_matrix(
            {"route": True, "corruption": False, "leakage": True, "ledger": True}
        )


def test_typed_surface_rejects_unallowlisted_files(tmp_path: Path):
    with pytest.raises(TypedSurfaceError, match="allowlist"):
        validate_typed_surface(
            tmp_path, ["graphs/task.yaml", "extra.py"], ["graphs/task.yaml", "src/node.py"]
        )


def test_typed_surface_requires_existing_allowlisted_files(tmp_path: Path):
    with pytest.raises(TypedSurfaceError, match="missing"):
        validate_typed_surface(tmp_path, ["graphs/task.yaml"], ["graphs/task.yaml"])


def test_typed_graph_uses_pinned_validator_without_repair(tmp_path: Path):
    report = validate_typed_graph(tmp_path / "missing.yaml", tmp_path, "franka")
    assert report["ok"] is False
    assert report["errors"]


def test_monolithic_surface_rejects_typed_facilities():
    with pytest.raises(TypedSurfaceError, match="forbidden"):
        validate_monolithic_surface(["control.py", "capability_registry.py"])


def test_interface_map_rejects_semantic_mismatch():
    with pytest.raises(TypedSurfaceError, match="mismatch"):
        validate_interface_map(
            [{"name": "action", "typed": "cmd", "monolithic": "other", "authority": "task"}]
        )


def test_broker_route_rejects_direct_actuation():
    with pytest.raises(TypedSurfaceError, match="guard"):
        validate_broker_route(["trusted_controller", "primitive_broker", "driver"])


def test_trusted_preflight_fails_closed_when_unresolved():
    with pytest.raises(TypedSurfaceError, match="unresolved"):
        validate_trusted_preflight(
            {"hashes": {}, "confinement": False, "route_map": {}, "evidence_sink": ""}
        )


def test_bypass_attempts_classify_and_unknown_fails_closed():
    assert classify_bypass_attempt("read hidden seed") == "hidden_seed_access"
    with pytest.raises(TypedSurfaceError, match="unclassified"):
        classify_bypass_attempt("invent a new bypass")


def test_matched_treatment_rejects_undeclared_difference():
    with pytest.raises(TypedSurfaceError, match="undeclared"):
        validate_matched_treatment(
            {"budget": 1, "arm": "typed"}, {"budget": 2, "arm": "mono"}, {"arm"}
        )


def test_expert_artifacts_require_two_arms_and_hashes():
    h = "a" * 64
    validate_expert_artifacts(
        [
            {"arm": "typed", "author": "expert-a", "path": "typed.py", "sha256": h},
            {"arm": "monolithic", "author": "expert-b", "path": "mono.py", "sha256": h},
        ]
    )


def test_artifact_hashes_fail_closed_on_drift():
    h = "a" * 64
    validate_artifact_hashes({"broker": h}, {"broker": h})
    with pytest.raises(TypedSurfaceError, match="drift"):
        validate_artifact_hashes({"broker": h}, {"broker": "b" * 64})


def test_conformance_requires_all_component_checks():
    required = {"surface", "route", "identity"}
    validate_conformance(dict.fromkeys(required, True), required)
    with pytest.raises(TypedSurfaceError, match="conformance"):
        validate_conformance({"surface": True}, required)


def test_freeze_record_requires_immutable_component_ids():
    required = {"protocol", "artifacts"}
    h = "sha256:" + "a" * 64
    validate_freeze_record({"protocol": h, "artifacts": h}, required)
    with pytest.raises(TypedSurfaceError, match="freeze"):
        validate_freeze_record({"protocol": "latest", "artifacts": h}, required)


def test_parity_protocol_requires_expert_parity_purpose():
    validate_parity_protocol(
        {
            "purpose": "expert_parity",
            "tasks": ["t1"],
            "paired_seeds": ["s1"],
            "acceptance": "all",
            "resource_ceiling": "fixed",
            "stopping_rule": "frozen",
        }
    )


def test_campaign_purpose_isolated_from_pooling():
    validate_campaign_purpose({"campaign_purpose": "expert_parity", "pooled": False})
    with pytest.raises(TypedSurfaceError, match="isolated"):
        validate_campaign_purpose({"campaign_purpose": "confirmatory"})


def test_common_evidence_requires_same_base_keys():
    required = {"session_id", "treatment", "timestamps"}
    validate_common_evidence(dict.fromkeys(required), dict.fromkeys(required), required)
    with pytest.raises(TypedSurfaceError, match="asymmetric"):
        validate_common_evidence({"session_id": 1}, {"session_id": 1, "treatment": 2}, required)


def test_bank_lifecycle_rejects_regression():
    with pytest.raises(FaultBankError, match="monotonic"):
        validate_bank_lifecycle(["draft", "sealed", "calibration"])


def test_threat_model_requires_explicit_scope_registries():
    with pytest.raises(ThreatModelError, match="incomplete"):
        validate_threat_model(
            {
                "gateway": "actuation-gateway",
                "attacker_powers": [],
                "out_of_scope": [],
                "claims": [],
            }
        )


def test_scoped_claim_rejects_unobserved_physical_evidence():
    with pytest.raises(ThreatModelError, match="physical"):
        validate_scoped_claim(
            {"statement": "stop latency", "scope": "hardware_pending", "evidence_kind": "physical"}
        )


def test_gateway_contract_rejects_non_fail_closed_lease():
    with pytest.raises(ThreatModelError, match="fail closed"):
        validate_gateway_contract(
            {
                "authority": "actuation-gateway",
                "endpoint": "/act",
                "credential_epoch": 1,
                "lease_seconds": 1,
                "fail_closed": False,
            }
        )
