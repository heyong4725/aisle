"""Mutation-catalog and independent-oracle validation for SPEC 430."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aisle.harness.instrument_audit import INSTRUMENT_CATEGORIES, validate_inventory

SCHEMA_VERSION = "aisle.instrument-mutation-catalog.v1"
REPORT_SCHEMA_VERSION = "aisle.instrument-mutation-catalog-validation.v1"
_SEVERITIES = {"critical", "noncritical"}
_COMPARISONS = {"exact", "numeric_tolerance", "status_error"}
_OPERATOR_KINDS = {"json_patch", "record_transform", "source_patch"}
_PROJECT_ROOT = Path(__file__).parents[3]


class CatalogError(RuntimeError):
    """The mutation catalog or retained validation report is unusable."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _string_list(value: Any, *, empty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (empty or bool(value))
        and all(isinstance(item, str) and bool(item) for item in value)
        and len(value) == len(set(value))
    )


def _safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def _bound_file(root: Path, record: Any, label: str, errors: list[str]) -> Path | None:
    if not isinstance(record, dict):
        errors.append(f"{label} record is absent")
        return None
    relative = record.get("path")
    if not _safe_relative(relative):
        errors.append(f"{label} path is unsafe")
        return None
    candidate = root / str(relative)
    try:
        cursor = root
        for component in Path(str(relative)).parts:
            cursor /= component
            if cursor.is_symlink():
                errors.append(f"{label} path is unsafe")
                return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        errors.append(f"{label} path is unresolved")
        return None
    expected = record.get("sha256")
    if not _is_hash(expected):
        errors.append(f"{label} sha256 is invalid")
        return None
    if _sha256(resolved.read_bytes()) != expected:
        errors.append(f"{label} hash mismatch")
        return None
    return resolved


def _inventory_surfaces(
    inventory: dict[str, Any],
) -> tuple[set[str], dict[str, set[str]], dict[str, str]]:
    coverage = inventory.get("coverage") if isinstance(inventory.get("coverage"), dict) else {}
    required = {
        str(row.get("id"))
        for key in ("primary_estimands", "exclusion_rules")
        for row in coverage.get(key, [])
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    required.update(INSTRUMENT_CATEGORIES)
    instruments: dict[str, set[str]] = {}
    implementation_paths: dict[str, str] = {}
    for row in inventory.get("entries", []):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        identifier = row["id"]
        instruments[identifier] = {
            item for item in row.get("coverage_ids", []) if isinstance(item, str)
        }
        implementation = row.get("implementation")
        if isinstance(implementation, dict) and isinstance(implementation.get("path"), str):
            implementation_paths[identifier] = implementation["path"]
    return required, instruments, implementation_paths


def _validate_expected_detection(
    mutation: dict[str, Any], instruments: set[str], label: str, errors: list[str]
) -> None:
    expected = mutation.get("expected_detection")
    if not isinstance(expected, dict):
        errors.append(f"{label} expected detection is absent")
        return
    if expected.get("layer") not in instruments:
        errors.append(f"{label} detection layer is unknown")
    comparison = expected.get("comparison")
    if comparison not in _COMPARISONS:
        errors.append(f"{label} comparison is unsupported")
    elif comparison == "exact" and "value" not in expected:
        errors.append(f"{label} exact comparison value is absent")
    elif comparison == "numeric_tolerance":
        tolerance = expected.get("tolerance")
        if (
            not isinstance(expected.get("value"), (int, float))
            or not isinstance(tolerance, (int, float))
            or tolerance < 0
        ):
            errors.append(f"{label} numeric comparison is invalid")
    elif comparison == "status_error" and not any(
        isinstance(expected.get(field), str) and expected[field] for field in ("status", "error")
    ):
        errors.append(f"{label} status/error oracle is absent")


def _validate_oracle(
    mutation: dict[str, Any],
    target_path: str | None,
    root: Path,
    label: str,
    errors: list[str],
) -> None:
    oracle = mutation.get("oracle")
    if not isinstance(oracle, dict):
        errors.append(f"{label} oracle is absent")
        return
    if oracle.get("kind") not in {"hand_derived", "independent_implementation"}:
        errors.append(f"{label} oracle kind is unsupported")
    if oracle.get("shares_production_helpers") is not False:
        errors.append(f"{label} oracle shares production helpers")
    derivation = {
        "path": oracle.get("derivation_path"),
        "sha256": oracle.get("sha256"),
    }
    bound = _bound_file(root, derivation, f"{label} oracle", errors)
    if target_path == oracle.get("derivation_path"):
        errors.append(f"{label} oracle is not independent from the target instrument")
    if bound is not None and target_path:
        target = root / target_path
        try:
            if target.resolve(strict=True) == bound:
                errors.append(f"{label} oracle is not independent from the target instrument")
        except OSError:
            pass


def validate_mutation_catalog(inventory: Any, catalog: Any, project_root: Path) -> dict[str, Any]:
    """Validate AUD-3/AUD-5 catalog coverage and independent fixed oracles."""
    root = Path(project_root).resolve()
    errors: list[str] = []
    inventory_report = validate_inventory(inventory, root)
    if not inventory_report["inventory_valid"]:
        errors.append("AUD-1 inventory is invalid")
    if not isinstance(inventory, dict):
        inventory = {}
    if not isinstance(catalog, dict):
        catalog = {}
        errors.append("catalog root must be an object")
    if catalog.get("schema_version") != SCHEMA_VERSION:
        errors.append("catalog schema_version is unsupported")
    if not isinstance(catalog.get("catalog_id"), str) or not catalog.get("catalog_id"):
        errors.append("catalog_id is absent")
    inventory_hash = _sha256(_canonical_bytes(inventory))
    if catalog.get("inventory_sha256") != inventory_hash:
        errors.append("inventory hash mismatch")

    required, instruments, implementation_paths = _inventory_surfaces(inventory)
    rows = catalog.get("mutations")
    if not isinstance(rows, list) or not rows:
        errors.append("mutation catalog must not be empty")
        rows = []
    mutation_ids = [
        row.get("id") for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]
    errors.extend(
        f"duplicate mutation id: {identifier}"
        for identifier in sorted({item for item in mutation_ids if mutation_ids.count(item) > 1})
    )
    covered: set[str] = set()
    for index, mutation in enumerate(rows):
        label = f"mutation {index}"
        if not isinstance(mutation, dict):
            errors.append(f"{label} is not an object")
            continue
        identifier = mutation.get("id")
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{label} id is absent")
        else:
            label = f"mutation {identifier}"
        target = mutation.get("target_instrument_id")
        if target not in instruments:
            errors.append(f"{label} names an unknown target instrument")
            target_coverage: set[str] = set()
        else:
            target_coverage = instruments[target]
        coverage_ids = mutation.get("coverage_ids")
        if not _string_list(coverage_ids):
            errors.append(f"{label} coverage_ids must be a unique non-empty list")
            coverage_ids = []
        for coverage_id in coverage_ids:
            if coverage_id not in required:
                errors.append(f"{label} names unknown coverage id: {coverage_id}")
            elif coverage_id not in target_coverage:
                errors.append(f"{label} target does not cover: {coverage_id}")
            else:
                covered.add(coverage_id)
        if mutation.get("severity") not in _SEVERITIES:
            errors.append(f"{label} severity is unsupported")
        for field in ("mutation_family", "rationale"):
            if not isinstance(mutation.get(field), str) or not mutation[field]:
                errors.append(f"{label} {field} is absent")
        operator = mutation.get("operator")
        if (
            not isinstance(operator, dict)
            or operator.get("kind") not in _OPERATOR_KINDS
            or not all(
                isinstance(operator.get(field), str) and operator[field]
                for field in ("operation", "path")
            )
        ):
            errors.append(f"{label} operator is invalid")
        _bound_file(root, mutation.get("fixture"), f"{label} fixture", errors)
        evidence_paths = mutation.get("evidence_paths")
        if not _string_list(evidence_paths):
            errors.append(f"{label} evidence_paths must be a unique non-empty list")
        else:
            for path in evidence_paths:
                if not _safe_relative(path) or not path.startswith(f"cases/{identifier}/"):
                    errors.append(f"{label} evidence path is unsafe or not case-scoped")
        _validate_expected_detection(mutation, set(instruments), label, errors)
        _validate_oracle(mutation, implementation_paths.get(str(target)), root, label, errors)

    uncovered = sorted(required - covered)
    errors.extend(f"required catalog coverage is absent: {item}" for item in uncovered)
    errors = sorted(set(errors))
    valid = not errors
    recorded_at = datetime.now(UTC)
    return {
        "catalog_id": catalog.get("catalog_id"),
        "catalog_sha256": _sha256(_canonical_bytes(catalog)),
        "catalog_valid": valid,
        "confirmatory_ready": False,
        "coverage": {
            "covered": len(required & covered),
            "required": len(required),
            "uncovered": uncovered,
        },
        "errors": errors,
        "environment": {
            "machine": platform.machine(),
            "os": platform.system(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        },
        "inventory_sha256": inventory_hash,
        "limitations": [
            "catalog validation does not execute mutations",
            "catalog validation does not independently review scientific coverage choices",
        ],
        "publication_gate": (
            "blocked_pending_AUD_4_and_AUD_6_through_AUD_12" if valid else "blocked"
        ),
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "schema_version": REPORT_SCHEMA_VERSION,
        "session_id": f"instrument-catalog-{recorded_at.strftime('%Y%m%dT%H%M%S%fZ')}",
        "source_sha256": _sha256(Path(__file__).read_bytes()),
    }


def write_mutation_catalog_validation(
    inventory_path: Path,
    catalog_path: Path,
    output: Path,
    *,
    project_root: Path = _PROJECT_ROOT,
) -> dict[str, Any]:
    """Validate one catalog and retain the result without overwriting."""
    output = Path(output)
    if output.exists():
        raise CatalogError(f"mutation catalog validation already exists: {output}")
    try:
        inventory_bytes = Path(inventory_path).read_bytes()
        catalog_bytes = Path(catalog_path).read_bytes()
        inventory = json.loads(inventory_bytes)
        catalog = json.loads(catalog_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot read mutation catalog inputs: {exc}") from exc
    report = validate_mutation_catalog(inventory, catalog, project_root)
    report["inventory_file_sha256"] = _sha256(inventory_bytes)
    report["catalog_file_sha256"] = _sha256(catalog_bytes)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise CatalogError(f"mutation catalog validation already exists: {output}") from exc
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=_PROJECT_ROOT)
    args = parser.parse_args(argv)
    try:
        report = write_mutation_catalog_validation(
            args.inventory, args.catalog, args.output, project_root=args.project_root
        )
    except CatalogError as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "catalog_valid": report["catalog_valid"],
                "confirmatory_ready": report["confirmatory_ready"],
                "ok": report["catalog_valid"],
                "output": str(args.output),
                "publication_gate": report["publication_gate"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["catalog_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
