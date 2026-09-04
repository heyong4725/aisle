"""Fail-closed validation for SPEC 450 sealed fault-bank manifests."""

from __future__ import annotations

from typing import Any


class FaultBankError(ValueError):
    """A fault-bank manifest cannot establish sealed scoring coverage."""


_FAMILIES = {"perception", "decision", "motion"}
_MODES = {"persistent", "intermittent", "coupled", "sham"}


def validate_fault_manifest(manifest: dict[str, Any]) -> None:
    """Validate the public, non-secret shape and minimum diversity of a bank."""
    if (
        set(manifest) != {"schema", "version", "cells"}
        or manifest["schema"] != "aisle.fault-bank.v1"
    ):
        raise FaultBankError("invalid fault-bank schema")
    if not isinstance(manifest["version"], str) or not manifest["version"]:
        raise FaultBankError("fault-bank version is required")
    cells = manifest["cells"]
    if not isinstance(cells, list) or not cells:
        raise FaultBankError("fault-bank cells are empty")
    families: set[str] = set()
    modes: set[str] = set()
    targets: set[str] = set()
    for cell in cells:
        if set(cell) != {"id", "family", "mode", "target", "sha256"}:
            raise FaultBankError("fault cell declaration is incomplete")
        if not all(isinstance(cell[key], str) and cell[key] for key in cell):
            raise FaultBankError("fault cell fields must be non-empty strings")
        if cell["family"] not in _FAMILIES or cell["mode"] not in _MODES:
            raise FaultBankError("unsupported fault family or mode")
        digest = cell["sha256"]
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise FaultBankError("fault cell hash is not a sha256 digest")
        families.add(cell["family"])
        modes.add(cell["mode"])
        targets.add(cell["target"])
    if families != _FAMILIES or not _MODES.issubset(modes) or len(targets) < 2:
        raise FaultBankError("fault-bank diversity coverage is incomplete")
