"""Fail-closed validation for SPEC 460 actuation threat models."""

from __future__ import annotations

from typing import Any


class ThreatModelError(ValueError):
    """Threat-model declaration is incomplete or ambiguous."""


def validate_threat_model(model: dict[str, Any]) -> None:
    """Require named gateway plus explicit attacker and out-of-scope registries."""
    required = {"gateway", "attacker_powers", "out_of_scope", "claims"}
    if set(model) != required:
        raise ThreatModelError("threat model schema is incomplete")
    if model["gateway"] != "actuation-gateway":
        raise ThreatModelError("actuation gateway is not uniquely named")
    for key in ("attacker_powers", "out_of_scope", "claims"):
        if (
            not isinstance(model[key], list)
            or not model[key]
            or not all(isinstance(item, str) and item for item in model[key])
        ):
            raise ThreatModelError(f"{key} registry is incomplete")
