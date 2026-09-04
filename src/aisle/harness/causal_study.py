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
