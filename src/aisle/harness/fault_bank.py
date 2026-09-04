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
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


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
    repairs: list[str] = []
    for cell in cells:
        if set(cell) != {"id", "family", "mode", "target", "sha256", "repair"}:
            raise FaultBankError("fault cell declaration is incomplete")
        if not all(isinstance(cell[key], str) and cell[key] for key in cell):
            raise FaultBankError("fault cell fields must be non-empty strings")
        if cell["family"] not in _FAMILIES or cell["mode"] not in _MODES:
            raise FaultBankError("unsupported fault family or mode")
        if cell["repair"] not in {"restoration", "novel", "diagnosis_only"}:
            raise FaultBankError("unsupported repair class")
        digest = cell["sha256"]
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise FaultBankError("fault cell hash is not a sha256 digest")
        families.add(cell["family"])
        modes.add(cell["mode"])
        targets.add(cell["target"])
        repairs.append(cell["repair"])
    if (
        families != _FAMILIES
        or not _MODES.issubset(modes)
        or len(targets) < 2
        or repairs.count("novel") < 2
    ):
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


def validate_participant_surface(values: list[str]) -> None:
    """Reject bank identifiers and metadata from participant-visible surfaces."""
    forbidden = ("fault-", "fault_", "perception", "decision", "motion", "target=")
    for value in values:
        if not isinstance(value, str):
            raise FaultBankError("participant surface values must be strings")
        lowered = value.lower()
        if any(token in lowered for token in forbidden):
            raise FaultBankError("participant surface leaks sealed fault metadata")


def validate_injection_request(request: dict[str, Any]) -> None:
    """Require an opaque, content-addressed and atomic injector request."""
    if set(request) != {"handle", "preimage_sha256", "postimage_sha256", "atomic"}:
        raise FaultBankError("injector request exposes fault metadata")
    if not _HANDLE.fullmatch(request["handle"]):
        raise FaultBankError("injector handle is not opaque")
    for key in ("preimage_sha256", "postimage_sha256"):
        if not isinstance(request[key], str) or not _DIGEST.fullmatch(request[key]):
            raise FaultBankError("injector image is not content-addressed")
    if request["atomic"] is not True:
        raise FaultBankError("injector must apply atomically")


def validate_sham_parity(sham: dict[str, Any], fault: dict[str, Any]) -> None:
    """Require sham/fault starts to share the same public execution envelope."""
    required = {"surface", "timing", "retention"}
    if set(sham) != required or set(fault) != required:
        raise FaultBankError("sham and fault execution surfaces are incomplete")
    if any(sham[key] != fault[key] for key in required):
        raise FaultBankError("sham and fault execution surfaces differ")


def validate_paired_efficacy(records: list[dict[str, Any]]) -> None:
    """Require each scored cell to retain both clean and degraded outcomes."""
    if not records:
        raise FaultBankError("efficacy records are empty")
    grouped: dict[str, set[str]] = {}
    for record in records:
        if set(record) != {"cell", "condition", "outcome"}:
            raise FaultBankError("efficacy record is incomplete")
        if record["condition"] not in {"clean", "degraded"}:
            raise FaultBankError("invalid efficacy condition")
        grouped.setdefault(record["cell"], set()).add(record["condition"])
    if any(conditions != {"clean", "degraded"} for conditions in grouped.values()):
        raise FaultBankError("paired clean/degraded efficacy evidence is incomplete")


def validate_calibration_records(campaign_purpose: str, records: list[dict[str, Any]]) -> None:
    """Keep calibration explicitly excluded and retain every attempted run."""
    if campaign_purpose != "excluded_pilot" or not records:
        raise FaultBankError("calibration must be an excluded pilot with retained records")
    for record in records:
        if set(record) != {"attempt", "outcome"} or not isinstance(record["attempt"], str):
            raise FaultBankError("calibration record is incomplete")


def validate_safety_assets(asset: dict[str, Any]) -> None:
    """Require frozen safety assets without granting fault/oracle information."""
    required = {"allowed_targets", "allowed_operators", "frozen", "oracle_access"}
    if set(asset) != required or not asset["allowed_targets"] or not asset["allowed_operators"]:
        raise FaultBankError("safety allowlist is incomplete")
    if asset["frozen"] is not True or asset["oracle_access"] is not False:
        raise FaultBankError("safety assets are mutable or oracle-bearing")
