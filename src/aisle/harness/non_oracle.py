"""Fail-closed validators for the non-oracle task-band instrument (SPEC 490)."""

from typing import Any


class NonOracleError(ValueError):
    """Raised when a task card violates the non-oracle contract."""


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
