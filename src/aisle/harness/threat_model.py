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


def validate_gateway_contract(contract: dict[str, Any]) -> None:
    """Require authenticated, leased gateway routing with fail-closed expiry."""
    required = {"authority", "endpoint", "credential_epoch", "lease_seconds", "fail_closed"}
    if set(contract) != required:
        raise ThreatModelError("gateway contract is incomplete")
    if (
        contract["authority"] != "actuation-gateway"
        or not isinstance(contract["endpoint"], str)
        or not contract["endpoint"]
    ):
        raise ThreatModelError("gateway authority or endpoint is invalid")
    if not isinstance(contract["credential_epoch"], int) or contract["credential_epoch"] < 1:
        raise ThreatModelError("credential epoch is invalid")
    if not isinstance(contract["lease_seconds"], (int, float)) or contract["lease_seconds"] <= 0:
        raise ThreatModelError("lease is invalid")
    if contract["fail_closed"] is not True:
        raise ThreatModelError("gateway must fail closed")
