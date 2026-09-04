"""Fail-closed validation for SPEC 450 sealed fault-bank manifests."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class FaultBankError(ValueError):
    """A fault-bank manifest cannot establish sealed scoring coverage."""


_FAMILIES = {"perception", "decision", "motion"}
_MODES = {"persistent", "intermittent", "coupled", "sham"}
_HANDLE = re.compile(r"^assignment:[0-9a-f]{64}$")


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


def validate_opaque_assignment(assignment: dict[str, Any]) -> None:
    """Reject operator-selected cells and fault metadata outside the sealed view."""
    if set(assignment) != {"session", "seed", "handle"}:
        raise FaultBankError("assignment exposes sealed fault metadata")
    if not all(isinstance(assignment[key], str) and assignment[key] for key in assignment):
        raise FaultBankError("assignment fields must be non-empty strings")
    if not _HANDLE.fullmatch(assignment["handle"]):
        raise FaultBankError("assignment handle is not opaque and content-addressed")
    if any(token in assignment["handle"].lower() for token in ("fault", "family", "target")):
        raise FaultBankError("assignment handle leaks fault metadata")


def validate_sealed_location(bank_path: Path, worktree: Path) -> None:
    """Require sealed bank bytes to live outside the participant worktree."""
    bank = bank_path.resolve()
    root = worktree.resolve()
    try:
        bank.relative_to(root)
    except ValueError:
        return
    raise FaultBankError("sealed fault bank is inside participant worktree")
