from pathlib import Path

import pytest

from aisle.harness.fault_bank import (
    FaultBankError,
    validate_fault_manifest,
    validate_opaque_assignment,
    validate_sealed_location,
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

pytestmark = pytest.mark.unit


def test_fault_manifest_requires_diverse_sealed_cells():
    cell = {
        "id": "a",
        "family": "perception",
        "mode": "sham",
        "target": "camera",
        "sha256": "a" * 64,
    }
    with pytest.raises(FaultBankError, match="diversity"):
        validate_fault_manifest({"schema": "aisle.fault-bank.v1", "version": "v1", "cells": [cell]})


def test_opaque_assignment_rejects_fault_metadata():
    with pytest.raises(FaultBankError, match="opaque"):
        validate_opaque_assignment({"session": "s", "seed": "1", "handle": "fault-camera"})


def test_sealed_location_rejects_worktree_path(tmp_path: Path):
    with pytest.raises(FaultBankError, match="worktree"):
        validate_sealed_location(tmp_path / "bank.json", tmp_path)


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
