"""Fail-closed validation for SPEC 470 safety-exposure ledgers."""

from __future__ import annotations

import re
from typing import Any

_HASH = re.compile(r"^[0-9a-f]{64}$")


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


def validate_observe_only_mode(mode: dict[str, Any]) -> None:
    """Require exposure observation mode to be non-actuating and contained."""
    required = {"authority", "containment", "writes_allowed"}
    if set(mode) != required or mode["authority"] != "observe-only":
        raise SafetyExposureError("observe-only mode authority is invalid")
    if mode["containment"] is not True or mode["writes_allowed"] is not False:
        raise SafetyExposureError("observe-only mode is not contained")


def validate_trace_corpus(records: list[dict[str, Any]]) -> None:
    """Require trace records to classify legality, violations, and watchdog outcome."""
    if not records:
        raise SafetyExposureError("trace corpus is empty")
    for record in records:
        if set(record) != {"trace_id", "legal", "violation", "watchdog"}:
            raise SafetyExposureError("trace corpus record is incomplete")
        if not isinstance(record["trace_id"], str) or not record["trace_id"]:
            raise SafetyExposureError("trace identity is incomplete")
        if not all(isinstance(record[key], bool) for key in ("legal", "violation", "watchdog")):
            raise SafetyExposureError("trace classifications are invalid")


def validate_paired_analysis(record: dict[str, Any]) -> None:
    """Require paired analysis outputs to retain uncertainty and exclusions."""
    required = {"estimate", "uncertainty", "excluded", "unit"}
    if set(record) != required or not isinstance(record["estimate"], (int, float)):
        raise SafetyExposureError("paired analysis is incomplete")
    if not isinstance(record["uncertainty"], (int, float)) or record["uncertainty"] < 0:
        raise SafetyExposureError("paired analysis uncertainty is invalid")
    if not isinstance(record["excluded"], list) or record["unit"] not in {"episode", "attempt"}:
        raise SafetyExposureError("paired analysis exclusions or unit are invalid")


def validate_raw_retention(records: list[dict[str, Any]]) -> None:
    """Require raw observations to remain retained with immutable hashes."""
    if not records:
        raise SafetyExposureError("raw retention is empty")
    for record in records:
        if set(record) != {"record_id", "sha256", "retained"}:
            raise SafetyExposureError("raw retention record is incomplete")
        if not isinstance(record["record_id"], str) or not record["record_id"]:
            raise SafetyExposureError("raw retention identity is incomplete")
        if not _HASH.fullmatch(record["sha256"]) or record["retained"] is not True:
            raise SafetyExposureError("raw retention hash or flag is invalid")


def validate_occurrence_audit(count: int, denominator: int, source_ids: list[str]) -> None:
    """Require occurrence reports to retain denominator and source linkage."""
    if count < 0 or denominator <= 0 or count > denominator or not source_ids:
        raise SafetyExposureError("occurrence audit is incomplete")


def validate_exposure_hardware_boundary(evidence_kind: str, hardware_available: bool) -> None:
    """Reject physical exposure labels unless real hardware is available."""
    if evidence_kind not in {"unit", "synthetic", "simulation", "physical", "hardware_pending"}:
        raise SafetyExposureError("exposure evidence kind is invalid")
    if evidence_kind == "physical" and hardware_available is not True:
        raise SafetyExposureError("physical exposure evidence requires hardware")
