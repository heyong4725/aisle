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
