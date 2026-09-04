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
