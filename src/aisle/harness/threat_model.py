"""Fail-closed validation for SPEC 460 actuation threat models."""

from __future__ import annotations

import re
from typing import Any

_HASH = re.compile(r"^[0-9a-f]{64}$")


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


def validate_authority_audit(actions: list[str], receipts: list[str]) -> None:
    """Require a one-to-one reconciled authority audit stream."""
    if not actions or set(actions) != set(receipts) or len(receipts) != len(set(receipts)):
        raise ThreatModelError("authority audit stream is unreconciled")


def validate_attack_catalog(entries: list[dict[str, Any]]) -> None:
    """Require classified attacks with explicit oracle and negative-control flags."""
    if not entries:
        raise ThreatModelError("attack catalog is empty")
    for entry in entries:
        if set(entry) != {"name", "class", "oracle", "negative_control"}:
            raise ThreatModelError("attack catalog entry is incomplete")
        if not all(isinstance(entry[key], str) and entry[key] for key in ("name", "class")):
            raise ThreatModelError("attack catalog identity is incomplete")
        if not isinstance(entry["oracle"], bool) or not isinstance(entry["negative_control"], bool):
            raise ThreatModelError("attack catalog flags are invalid")


def validate_conformance_evidence(evidence: dict[str, Any]) -> None:
    """Require declared synthetic runner provenance and complete evidence."""
    required = {"runner", "evidence_kind", "passed", "artifacts"}
    if set(evidence) != required or evidence["runner"] != "fake-driver":
        raise ThreatModelError("conformance runner provenance is invalid")
    if evidence["evidence_kind"] != "synthetic":
        raise ThreatModelError("conformance evidence kind is not synthetic")
    if (
        evidence["passed"] is not True
        or not isinstance(evidence["artifacts"], list)
        or not evidence["artifacts"]
    ):
        raise ThreatModelError("conformance evidence is incomplete")


def validate_matched_profiles(left: dict[str, Any], right: dict[str, Any]) -> None:
    """Require Claude/Codex parity profiles to match before comparison."""
    required = {"tasks", "seeds", "resource_ceiling", "access_boundary"}
    if set(left) != required or set(right) != required:
        raise ThreatModelError("matched parity profile is incomplete")
    if left != right:
        raise ThreatModelError("matched parity profiles differ")


def validate_bypass_report(entries: list[dict[str, Any]]) -> None:
    """Require exhaustive accounting of bypass attempts and dispositions."""
    if not entries:
        raise ThreatModelError("bypass report is empty")
    for entry in entries:
        if set(entry) != {"attempt", "disposition", "evidence"}:
            raise ThreatModelError("bypass report entry is incomplete")
        if (
            entry["disposition"] not in {"blocked", "allowed", "inconclusive"}
            or not entry["evidence"]
        ):
            raise ThreatModelError("bypass report accounting is incomplete")


def validate_review_record(record: dict[str, Any]) -> None:
    """Require an independently attributable, hash-bound review disposition."""
    required = {"reviewer", "artifact_sha256", "disposition"}
    if set(record) != required or not isinstance(record["reviewer"], str) or not record["reviewer"]:
        raise ThreatModelError("review record is incomplete")
    if not isinstance(record["artifact_sha256"], str) or not _HASH.fullmatch(
        record["artifact_sha256"]
    ):
        raise ThreatModelError("review artifact hash is invalid")
    if record["disposition"] not in {"accepted", "weakened", "rejected"}:
        raise ThreatModelError("review disposition is invalid")
