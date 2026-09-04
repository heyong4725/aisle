"""Fail-closed validators for session-level causal-study records."""

from typing import Any


class CausalStudyError(ValueError):
    """Raised when a causal-study record violates its preregistered contract."""


def validate_session_record(record: dict[str, Any]) -> None:
    """Require one retained randomized session and an analyzer classification."""
    required = {
        "session_id", "arm", "randomized", "success", "exclusion", "outcome_kind",
        "protocol_hash", "agent_hash", "raw_evidence",
    }
    if set(record) != required:
        raise CausalStudyError("session record is incomplete")
    if record["arm"] not in {"typed", "monolithic"} or record["randomized"] is not True:
        raise CausalStudyError("session arm or randomization is invalid")
    if record["success"] not in {True, False, None}:
        raise CausalStudyError("session outcome is invalid")
    if record["exclusion"] is not None and not isinstance(record["exclusion"], str):
        raise CausalStudyError("session exclusion is not classified")
    if (
        record["outcome_kind"] != "session_success"
        or not record["protocol_hash"]
        or not record["raw_evidence"]
    ):
        raise CausalStudyError("session evidence is not retained")


def validate_session_table(records: list[dict[str, Any]]) -> None:
    """Reject pseudoreplication, missing arms, duplicate sessions, or dropped exclusions."""
    if not records or any(not isinstance(record, dict) for record in records):
        raise CausalStudyError("session table is missing")
    ids = [record.get("session_id") for record in records]
    if None in ids or len(set(ids)) != len(ids):
        raise CausalStudyError("session units are duplicated")
    if {record.get("arm") for record in records} != {"typed", "monolithic"}:
        raise CausalStudyError("session arms are incomplete")
    if any("exclusion" not in record for record in records):
        raise CausalStudyError("session exclusions were dropped")


def validate_session_effect(effect: dict[str, Any]) -> None:
    """Require session-unit arm counts and a finite uncertainty interval."""
    required = {
        "typed_n", "monolithic_n", "typed_success", "monolithic_success",
        "risk_difference", "ci_low", "ci_high",
    }
    if set(effect) != required:
        raise CausalStudyError("session effect is incomplete")
    if effect["typed_n"] <= 0 or effect["monolithic_n"] <= 0:
        raise CausalStudyError("session arm count is invalid")
    if not 0 <= effect["typed_success"] <= effect["typed_n"] or not 0 <= effect[
        "monolithic_success"
    ] <= effect["monolithic_n"]:
        raise CausalStudyError("session success count is invalid")
    if effect["ci_low"] > effect["ci_high"] or not all(
        isinstance(effect[key], (int, float)) for key in ("risk_difference", "ci_low", "ci_high")
    ):
        raise CausalStudyError("session uncertainty interval is invalid")


def validate_exclusion_register(register: list[dict[str, Any]]) -> None:
    """Require every exclusion to be classified and retainable for sensitivity analysis."""
    if not register:
        raise CausalStudyError("exclusion register is missing")
    required = {"session_id", "reason", "pre_registered", "retained", "sensitivity_bound"}
    if any(set(item) != required for item in register):
        raise CausalStudyError("exclusion record is incomplete")
    if any(item["pre_registered"] is not True or item["retained"] is not True for item in register):
        raise CausalStudyError("exclusion was not retained under the protocol")
    if any(not isinstance(item["reason"], str) or not item["reason"] for item in register):
        raise CausalStudyError("exclusion reason is missing")


def validate_claim_disposition(disposition: dict[str, Any]) -> None:
    """Require an evidence-backed, direction-neutral causal disposition."""
    required = {"status", "estimand", "effect", "interval", "evidence_hash"}
    if set(disposition) != required:
        raise CausalStudyError("claim disposition is incomplete")
    if disposition["status"] not in {"typed_favoring", "monolithic_favoring", "null", "rejected"}:
        raise CausalStudyError("claim disposition status is invalid")
    if not disposition["estimand"] or not disposition["evidence_hash"]:
        raise CausalStudyError("claim disposition evidence is missing")
    if not isinstance(disposition["interval"], (list, tuple)) or len(disposition["interval"]) != 2:
        raise CausalStudyError("claim disposition interval is invalid")


def validate_fault_evidence_record(record: dict[str, Any]) -> None:
    """Require matched typed-evidence/logs-only surfaces and hidden-fault provenance."""
    required = {
        "session_id", "arm", "fault_id", "fault_hidden", "diagnosis", "repair", "raw_evidence"
    }
    if set(record) != required:
        raise CausalStudyError("fault evidence record is incomplete")
    if record["arm"] not in {"typed_evidence", "logs_only"} or record["fault_hidden"] is not True:
        raise CausalStudyError("fault evidence arm or concealment is invalid")
    if not record["fault_id"] or not record["raw_evidence"]:
        raise CausalStudyError("fault evidence provenance is missing")


def validate_paired_fault_diagnosis(pair: list[dict[str, Any]]) -> None:
    """Require one matched pair without exposing fault truth to either arm."""
    if len(pair) != 2 or {item.get("arm") for item in pair} != {"typed_evidence", "logs_only"}:
        raise CausalStudyError("fault diagnosis pair is incomplete")
    if (
        len({item.get("session_id") for item in pair}) != 1
        or len({item.get("fault_id") for item in pair}) != 1
    ):
        raise CausalStudyError("fault diagnosis pair is not matched")
    if any(item.get("fault_hidden") is not True for item in pair):
        raise CausalStudyError("fault diagnosis pair reveals truth")
    if any(item.get("fault_class") not in {"novel", "sham", "public"} for item in pair):
        raise CausalStudyError("fault class is invalid")


def validate_sham_rates(rows: list[dict[str, Any]]) -> None:
    """Require arm-complete sham denominators and bounded false-alarm rates."""
    if {row.get("arm") for row in rows} != {"typed_evidence", "logs_only"}:
        raise CausalStudyError("sham arms are incomplete")
    required = {"arm", "denominator", "false_alarms", "interventions"}
    if any(set(row) != required for row in rows):
        raise CausalStudyError("sham rate row is incomplete")
    if any(
        row["denominator"] <= 0
        or not 0 <= row["false_alarms"] <= row["denominator"]
        for row in rows
    ):
        raise CausalStudyError("sham rate denominator is invalid")
    if any(row["interventions"] < 0 for row in rows):
        raise CausalStudyError("sham intervention count is invalid")
