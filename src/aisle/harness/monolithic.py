"""MON-1 treatment-difference table validation (SPEC 440)."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_HASH = re.compile(r"^[0-9a-f]{64}$")
_KINDS = {"identical", "representation-equivalent", "intentionally-different"}


class TreatmentTableError(ValueError):
    """A treatment table cannot establish a complete frozen comparison."""


class TypedSurfaceError(ValueError):
    """A typed deliverable escapes its frozen editable surface."""


def validate_typed_surface(root, editable_files: list[str], allowlist: list[str]) -> None:
    """Validate MON-2's authored typed surface without repairing it."""
    if sorted(editable_files) != sorted(set(editable_files)):
        raise TypedSurfaceError("allowlist contains duplicate files")
    if set(editable_files) - set(allowlist):
        raise TypedSurfaceError("editable files exceed the frozen allowlist")
    base = root.resolve()
    for relative in editable_files:
        path = (base / relative).resolve()
        if base not in path.parents and path != base:
            raise TypedSurfaceError("editable file escapes root")
        if not path.is_file():
            raise TypedSurfaceError(f"missing editable file: {relative}")


def validate_treatment_table(table: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(table, dict)
        or table.get("schema_version") != "aisle.monolithic-treatment.v1"
    ):
        raise TreatmentTableError("invalid treatment-table schema")
    rows = table.get("rows")
    if not isinstance(rows, list) or not rows:
        raise TreatmentTableError("treatment table rows are required")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise TreatmentTableError("each treatment row must be an object")
        required = {
            "id",
            "surface",
            "classification",
            "typed",
            "monolithic",
            "justification",
            "analysis",
        }
        if set(row) != required:
            raise TreatmentTableError("treatment row fields are incomplete or undeclared")
        if not isinstance(row["id"], str) or not row["id"] or row["id"] in seen:
            raise TreatmentTableError("treatment row ids must be unique")
        seen.add(row["id"])
        if row["classification"] not in _KINDS:
            raise TreatmentTableError("invalid treatment classification")
        if not all(isinstance(row[k], str) and row[k].strip() for k in required - {"id"}):
            raise TreatmentTableError("treatment row values must be resolved strings")
        for arm in ("typed", "monolithic"):
            path, digest = row[arm].split("#sha256:", 1) if "#sha256:" in row[arm] else ("", "")
            if not path or not _HASH.fullmatch(digest):
                raise TreatmentTableError(f"{arm} artifact must contain a path and SHA-256")
    canonical = json.dumps(
        table, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return {
        "valid": True,
        "immutable_id": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "row_count": len(rows),
    }
