"""Fail-closed validation for SPEC 470 safety-exposure ledgers."""

from __future__ import annotations

from typing import Any


class SafetyExposureError(ValueError):
    """Exposure evidence is incomplete or not attributable."""


def validate_exposure_record(record: dict[str, Any]) -> None:
    """Require an attributable, layered exposure observation."""
    required = {"session_id", "layer", "unit", "source", "value"}
    if set(record) != required:
        raise SafetyExposureError("exposure record is incomplete")
    if not all(
        isinstance(record[key], str) and record[key]
        for key in ("session_id", "layer", "unit", "source")
    ):
        raise SafetyExposureError("exposure identity is incomplete")
    if record["layer"] not in {"claim", "delivery", "contact", "command"}:
        raise SafetyExposureError("exposure evidence layer is invalid")
    if record["unit"] not in {"episode", "attempt", "event"}:
        raise SafetyExposureError("exposure unit is invalid")


def validate_proposal_accounting(records: list[dict[str, Any]]) -> None:
    """Require each proposal to retain decision and correlation accounting."""
    if not records:
        raise SafetyExposureError("proposal accounting is empty")
    for record in records:
        if set(record) != {"proposal_id", "decision", "correlation"}:
            raise SafetyExposureError("proposal accounting record is incomplete")
        if record["decision"] not in {"accepted", "rejected", "held"}:
            raise SafetyExposureError("proposal decision is invalid")
        if not isinstance(record["correlation"], bool):
            raise SafetyExposureError("proposal correlation flag is invalid")


def validate_exposure_analysis(raw_ids: list[str], derived_ids: list[str]) -> None:
    """Require analyzer output to account for every raw exposure identifier."""
    if not raw_ids or len(set(raw_ids)) != len(raw_ids):
        raise SafetyExposureError("raw exposure identifiers are incomplete")
    if set(raw_ids) != set(derived_ids) or len(derived_ids) != len(set(derived_ids)):
        raise SafetyExposureError("exposure analysis is not exhaustive")


def validate_source_strata(records: list[dict[str, Any]]) -> None:
    """Require each exposure source stratum to include provenance and rate."""
    if not records:
        raise SafetyExposureError("source strata are empty")
    for record in records:
        if set(record) != {"source", "provenance", "rate", "unknown"}:
            raise SafetyExposureError("source stratum is incomplete")
        if not all(
            isinstance(record[key], str) and record[key] for key in ("source", "provenance")
        ):
            raise SafetyExposureError("source provenance is incomplete")
        if not isinstance(record["rate"], (int, float)) or record["rate"] < 0:
            raise SafetyExposureError("source rate is invalid")
        if not isinstance(record["unknown"], bool):
            raise SafetyExposureError("unknown-source flag is invalid")


def validate_zero_event_bound(denominator: int, observed: int, refusals: int) -> None:
    """Require a valid denominator and explicit refusal accounting."""
    if denominator <= 0 or observed < 0 or refusals < 0 or observed + refusals > denominator:
        raise SafetyExposureError("zero-event bound denominator is invalid")


def validate_fixed_trace_protocol(protocol: dict[str, Any]) -> None:
    """Require immutable trace identity and explicit randomized seeds."""
    required = {"trace_id", "seeds", "randomized", "frozen"}
    if (
        set(protocol) != required
        or not isinstance(protocol["trace_id"], str)
        or not protocol["trace_id"]
    ):
        raise SafetyExposureError("fixed-trace protocol is incomplete")
    if (
        not isinstance(protocol["seeds"], list)
        or not protocol["seeds"]
        or protocol["randomized"] is not True
    ):
        raise SafetyExposureError("fixed-trace randomization is incomplete")
    if protocol["frozen"] is not True:
        raise SafetyExposureError("fixed-trace identity is not frozen")
