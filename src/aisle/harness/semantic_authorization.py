"""Fail-closed validation for SPEC 480 semantic authorization."""

from __future__ import annotations

from typing import Any


class SemanticAuthorizationError(ValueError):
    """Semantic authorization declaration is incomplete or unsafe."""


def validate_identity(identity: dict[str, Any]) -> None:
    """Require provenance, carrier, and task binding for an identity."""
    required = {"subject", "provenance", "carrier", "task_id"}
    if set(identity) != required or not all(
        isinstance(identity[key], str) and identity[key] for key in required
    ):
        raise SemanticAuthorizationError("identity schema is incomplete")


def validate_permit(permit: dict[str, Any]) -> None:
    """Require authenticated, non-replayable permits bound to one task."""
    required = {"permit_id", "task_id", "credential_epoch", "used"}
    if set(permit) != required or not all(
        isinstance(permit[key], str) and permit[key] for key in ("permit_id", "task_id")
    ):
        raise SemanticAuthorizationError("permit schema is incomplete")
    if not isinstance(permit["credential_epoch"], int) or permit["credential_epoch"] < 1:
        raise SemanticAuthorizationError("permit credential epoch is invalid")
    if permit["used"] is not False:
        raise SemanticAuthorizationError("permit is replayable")


def validate_stage_gates(stages: list[dict[str, Any]], thresholds_frozen: bool) -> None:
    """Require ordered stage permits and frozen acceptance thresholds."""
    required = ["grasp", "carry", "delivery"]
    if not thresholds_frozen or len(stages) != len(required):
        raise SemanticAuthorizationError("stage-gate thresholds or stages are incomplete")
    if [stage.get("stage") for stage in stages] != required:
        raise SemanticAuthorizationError("stage gates are out of order")
    if any(stage.get("renewed") is not True for stage in stages):
        raise SemanticAuthorizationError("stage permit renewal is incomplete")


def validate_authorization_state(state: dict[str, Any]) -> None:
    """Fail closed on stale, missing, disagreeing, or revoked authorization."""
    required = {"permit", "lease_valid", "revoked", "agreement"}
    if set(state) != required or not isinstance(state["permit"], str) or not state["permit"]:
        raise SemanticAuthorizationError("authorization state is incomplete")
    if (
        state["lease_valid"] is not True
        or state["revoked"] is not False
        or state["agreement"] is not True
    ):
        raise SemanticAuthorizationError("authorization state is not fail closed")


def validate_frozen_thresholds(thresholds: dict[str, Any]) -> None:
    """Require positive, frozen authorization envelope thresholds."""
    required = {"max_force", "max_duration", "frozen"}
    if set(thresholds) != required or thresholds["frozen"] is not True:
        raise SemanticAuthorizationError("threshold envelope is not frozen")
    if not all(
        isinstance(thresholds[key], (int, float)) and thresholds[key] > 0
        for key in ("max_force", "max_duration")
    ):
        raise SemanticAuthorizationError("threshold envelope is invalid")


def validate_independent_containment(policy_fields: list[str], verifier_fields: list[str]) -> None:
    """Require privileged authorization state to remain independently masked."""
    privileged = {"oracle_pose", "scene_truth", "object_id", "permit_secret", "verifier_verdict"}
    if not verifier_fields or privileged.intersection(policy_fields):
        raise SemanticAuthorizationError("authorization containment boundary is violated")
    if not privileged.intersection(verifier_fields):
        raise SemanticAuthorizationError("verifier-only containment is not declared")


def validate_held_plan(plan: dict[str, Any]) -> None:
    """Require a frozen, identity-bound held plan before confirmatory execution."""
    required = {"plan_hash", "randomization_hash", "identity_hash", "frozen", "revealed"}
    if set(plan) != required or plan["frozen"] is not True:
        raise SemanticAuthorizationError("held plan is incomplete or not frozen")
    if plan["revealed"] is not False:
        raise SemanticAuthorizationError("held plan was revealed early")
    if not all(
        isinstance(plan[key], str) and plan[key]
        for key in ("plan_hash", "randomization_hash", "identity_hash")
    ):
        raise SemanticAuthorizationError("held plan hashes are missing")


def validate_adversarial_corpus(cases: list[dict[str, Any]]) -> None:
    """Require wrong-object and authorization-lifecycle cases with expected verdicts."""
    if not cases:
        raise SemanticAuthorizationError("adversarial corpus is empty")
    required = {"case_id", "kind", "expected", "evidence"}
    if any(set(case) != required for case in cases):
        raise SemanticAuthorizationError("adversarial case is incomplete")
    kinds = {case["kind"] for case in cases}
    if not {"wrong_target", "stale_permit", "revoked_permit"}.issubset(kinds):
        raise SemanticAuthorizationError("adversarial corpus lacks lifecycle coverage")
    if any(case["expected"] not in {"block", "allow"} or not case["evidence"] for case in cases):
        raise SemanticAuthorizationError("adversarial expected outcome is invalid")


def validate_authorization_endpoints(endpoints: dict[str, Any]) -> None:
    """Require bounded false-allow/false-block counts and intervention accounting."""
    required = {
        "false_allow", "false_block", "allow_denominator", "block_denominator", "interventions"
    }
    if set(endpoints) != required:
        raise SemanticAuthorizationError("authorization endpoints are incomplete")
    if endpoints["allow_denominator"] <= 0 or endpoints["block_denominator"] <= 0:
        raise SemanticAuthorizationError("authorization endpoint denominators are invalid")
    if not 0 <= endpoints["false_allow"] <= endpoints["allow_denominator"]:
        raise SemanticAuthorizationError("false-allow endpoint is invalid")
    if not 0 <= endpoints["false_block"] <= endpoints["block_denominator"]:
        raise SemanticAuthorizationError("false-block endpoint is invalid")
    if endpoints["interventions"] < 0:
        raise SemanticAuthorizationError("authorization intervention count is invalid")


def validate_metric_layers(layers: dict[str, list[str]]) -> None:
    """Require disjoint policy, intervention, and verifier metric namespaces."""
    required = {"policy", "intervention", "verifier"}
    if set(layers) != required or any(not isinstance(layers[key], list) for key in required):
        raise SemanticAuthorizationError("metric layers are incomplete")
    sets = [set(layers[key]) for key in required]
    if any(not values for values in sets):
        raise SemanticAuthorizationError("metric layer is empty")
    if any(
        left.intersection(right)
        for index, left in enumerate(sets)
        for right in sets[index + 1 :]
    ):
        raise SemanticAuthorizationError("metric layers overlap")


def validate_authorization_analysis(raw_ids: list[str], derived: dict[str, str]) -> None:
    """Require exhaustive metric derivation with stable source identifiers."""
    if not raw_ids or len(set(raw_ids)) != len(raw_ids):
        raise SemanticAuthorizationError("authorization raw IDs are incomplete")
    if set(derived) != set(raw_ids) or any(not value for value in derived.values()):
        raise SemanticAuthorizationError("authorization derivation is not exhaustive")


def validate_hardware_adapter(adapter: dict[str, Any]) -> None:
    """Require explicit adapter capability and fail-closed unavailable behavior."""
    required = {"name", "available", "evidence_kind", "refusal", "telemetry"}
    if set(adapter) != required or not adapter["name"]:
        raise SemanticAuthorizationError("hardware adapter declaration is incomplete")
    if adapter["evidence_kind"] not in {"simulation", "hardware_pending", "physical"}:
        raise SemanticAuthorizationError("hardware adapter evidence kind is invalid")
    if adapter["available"] is not True and adapter["refusal"] is not True:
        raise SemanticAuthorizationError("unavailable hardware adapter must refuse")
    if not isinstance(adapter["telemetry"], list) or not adapter["telemetry"]:
        raise SemanticAuthorizationError("hardware adapter telemetry is missing")


def validate_evidence_label(label: dict[str, Any]) -> None:
    """Require honest evidence kind and prevent oracle-derived physical claims."""
    required = {"kind", "oracle_used", "hardware_available"}
    if set(label) != required or label["kind"] not in {
        "unit", "synthetic", "simulation", "physical"
    }:
        raise SemanticAuthorizationError("evidence label is invalid")
    if label["kind"] == "physical" and (
        label["hardware_available"] is not True or label["oracle_used"] is True
    ):
        raise SemanticAuthorizationError("physical evidence label is unsupported")


def validate_claim_occurrence(claim: dict[str, Any]) -> None:
    """Require bounded claim occurrence counts linked to evidence sources."""
    required = {"claim_id", "count", "denominator", "source_ids", "evidence_kind"}
    if set(claim) != required or not claim["claim_id"]:
        raise SemanticAuthorizationError("claim occurrence is incomplete")
    if claim["denominator"] <= 0 or not 0 <= claim["count"] <= claim["denominator"]:
        raise SemanticAuthorizationError("claim occurrence bounds are invalid")
    if not isinstance(claim["source_ids"], list) or not claim["source_ids"]:
        raise SemanticAuthorizationError("claim occurrence sources are missing")
    if claim["evidence_kind"] not in {"unit", "synthetic", "simulation", "physical"}:
        raise SemanticAuthorizationError("claim occurrence evidence kind is invalid")
