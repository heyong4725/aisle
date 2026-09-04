"""Fail-closed validators for the non-oracle task-band instrument (SPEC 490)."""

from typing import Any


class NonOracleError(ValueError):
    """Raised when a task card violates the non-oracle contract."""


def validate_perception_audit(record: dict[str, Any]) -> None:
    """Require independent, stratified perception evidence with raw provenance."""
    required = {
        "unit_id", "target_class", "stratum", "prediction", "truth_hidden",
        "confidence", "latency_ms", "record_hash", "model_hash", "split",
    }
    if not required.issubset(record) or record.get("truth_hidden") is not True:
        raise NonOracleError("perception audit is incomplete or truth is exposed")
    if record["split"] not in {"calibration", "evaluation"}:
        raise NonOracleError("perception split is invalid")
    if not isinstance(record["confidence"], (int, float)) or not 0 <= record["confidence"] <= 1:
        raise NonOracleError("perception confidence is invalid")
    if not isinstance(record["latency_ms"], (int, float)) or record["latency_ms"] < 0:
        raise NonOracleError("perception latency is invalid")


def validate_perception_envelope(envelope: dict[str, Any]) -> None:
    """Require calibration-frozen thresholds and explicit refusal behavior."""
    required = {
        "vocabulary", "max_position_error", "min_confidence", "max_latency_ms", "refusal", "frozen"
    }
    if set(envelope) != required or envelope.get("frozen") is not True:
        raise NonOracleError("perception envelope is not frozen")
    if not isinstance(envelope["vocabulary"], list) or not envelope["vocabulary"]:
        raise NonOracleError("perception vocabulary is missing")
    if not 0 <= envelope["min_confidence"] <= 1 or envelope["max_position_error"] < 0:
        raise NonOracleError("perception thresholds are invalid")
    if envelope["max_latency_ms"] <= 0 or envelope["refusal"] not in {"explicit", "reject"}:
        raise NonOracleError("perception refusal or latency is invalid")


def validate_perception_eligibility(strata: list[dict[str, Any]]) -> None:
    """Require every registered stratum to pass, without aggregate masking."""
    if not strata or any(not isinstance(item, dict) for item in strata):
        raise NonOracleError("perception strata are missing")
    required = {"name", "accuracy", "max_error", "latency_ms", "refusal_rate", "eligible"}
    if any(set(item) != required for item in strata):
        raise NonOracleError("perception stratum is incomplete")
    if any(item["eligible"] is not True for item in strata):
        raise NonOracleError("perception eligibility failed in a stratum")
    if any(not 0 <= item["accuracy"] <= 1 or not 0 <= item["refusal_rate"] <= 1 for item in strata):
        raise NonOracleError("perception stratum rates are invalid")


def validate_expert_parity(
    typed: dict[str, Any], monolithic: dict[str, Any], margins: dict[str, float]
) -> None:
    """Require matched expert surfaces and success/time equivalence margins."""
    surface = {"sensors", "feedback", "actuation", "verifier", "reset", "budget", "authority"}
    if set(typed) != surface or set(monolithic) != surface:
        raise NonOracleError("expert parity surface is incomplete")
    if any(typed[key] != monolithic[key] for key in surface):
        raise NonOracleError("expert parity surface is asymmetric")
    for key in ("success_delta", "completion_time_delta"):
        if key not in margins or margins[key] < 0:
            raise NonOracleError("expert parity margin is invalid")


def validate_oracle_boundary(policy_inputs: list[str], verifier_only: list[str]) -> None:
    """Reject privileged simulator state appearing on the policy path."""
    forbidden = {
        "simulator_pose", "segmentation_mask", "object_id", "attachment_state", "scene_truth"
    }
    leaked = forbidden.intersection(policy_inputs)
    if leaked or not verifier_only:
        raise NonOracleError("oracle state crosses the policy boundary")


def validate_task_card(card: dict[str, Any]) -> None:
    """Require an explicit, bounded task capability and privilege boundary."""
    required = {
        "task_id", "role", "physical_capability", "sensor_inputs", "action_outputs",
        "embodiment", "workspace", "episode_budget", "success_semantics",
        "failure_semantics", "permitted_feedback", "installed_capabilities",
        "agent_edit_authority", "excluded_privileges", "evidence_kind",
    }
    if not required.issubset(card) or card.get("evidence_kind") == "physical":
        raise NonOracleError("task card is incomplete or mislabels simulation evidence")
    if card["role"] not in {"short_composition", "engineering"}:
        raise NonOracleError("task role is not one of the frozen roles")
    if not isinstance(card["excluded_privileges"], list) or not card["excluded_privileges"]:
        raise NonOracleError("excluded privileges must be explicit")
    if not isinstance(card["episode_budget"], (int, float)) or card["episode_budget"] <= 0:
        raise NonOracleError("episode budget must be positive")
