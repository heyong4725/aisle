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
