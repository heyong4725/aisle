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
