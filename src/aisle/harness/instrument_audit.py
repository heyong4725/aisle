"""Fail-closed instrument-inventory validation for SPEC 430 AUD-1/AUD-2."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "aisle.instrument-inventory.v1"
REPORT_SCHEMA_VERSION = "aisle.instrument-inventory-validation.v1"
INSTRUMENT_CATEGORIES = (
    "category:verifier-scorer-boundary",
    "category:validator-false-accept",
    "category:validator-false-reject",
    "category:analyzer-arithmetic",
    "category:analyzer-provenance",
    "category:inclusion-exclusion",
    "category:stopping-credit-windows",
    "category:safety-exposure-accounting",
    "category:hidden-leakage-treatment-contamination",
    "category:figure-table-derivation",
)
_PROJECT_ROOT = Path(__file__).parents[3]
_HASH_LENGTH = 64


class InventoryError(RuntimeError):
    """The inventory or its retained validation record is unusable."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HASH_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _nonempty_strings(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
        and len(value) == len(set(value))
    )


def _safe_file(root: Path, value: Any, label: str, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label} path is absent")
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        errors.append(f"{label} path is unsafe: {value}")
        return None
    candidate = root / relative
    try:
        cursor = root
        for component in relative.parts:
            cursor /= component
            if cursor.is_symlink():
                errors.append(f"{label} path is unsafe: symlink {value}")
                return None
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        errors.append(f"{label} path is unresolved or outside project root: {value}")
        return None
    if not resolved.is_file():
        errors.append(f"{label} path is not a file: {value}")
        return None
    return resolved


def _verify_bound_file(
    root: Path,
    record: Any,
    label: str,
    errors: list[str],
    retained: list[dict[str, str]],
) -> Path | None:
    if not isinstance(record, dict):
        errors.append(f"{label} record is absent")
        return None
    path = _safe_file(root, record.get("path"), label, errors)
    expected = record.get("sha256")
    if not _is_hash(expected):
        errors.append(f"{label} sha256 is invalid")
        return None
    if path is None:
        return None
    actual = _sha256_bytes(path.read_bytes())
    if actual != expected:
        errors.append(f"{label} hash mismatch: {record.get('path')}")
        return None
    retained.append({"kind": label, "path": str(record["path"]), "sha256": actual})
    return path


def _module_name(path: str) -> str:
    parts = list(Path(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    return ".".join(parts)


def _verify_entrypoint(
    implementation: dict[str, Any], path: Path | None, label: str, errors: list[str]
) -> None:
    kind = implementation.get("entrypoint_kind")
    entrypoint = implementation.get("entrypoint")
    declared_path = implementation.get("path")
    if kind == "python_callable":
        if not isinstance(entrypoint, str) or entrypoint.count(":") != 1:
            errors.append(f"{label} implementation entrypoint is invalid")
            return
        module, callable_name = entrypoint.split(":", 1)
        if not module or not callable_name or module != _module_name(str(declared_path)):
            errors.append(f"{label} implementation callable is unresolvable")
            return
        if path is None:
            return
        try:
            tree = ast.parse(path.read_text())
        except (OSError, SyntaxError):
            errors.append(f"{label} implementation callable is unresolvable")
            return
        definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef))
        }
        if callable_name not in definitions:
            errors.append(f"{label} implementation callable is unresolvable")
    elif kind == "cli":
        if (
            not isinstance(entrypoint, list)
            or not entrypoint
            or any(not isinstance(item, str) or not item for item in entrypoint)
        ):
            errors.append(f"{label} implementation entrypoint is invalid")
            return
        module = _module_name(str(declared_path))
        module_launch = any(
            entrypoint[index : index + 2] == ["-m", module]
            for index in range(max(0, len(entrypoint) - 1))
        )
        if str(declared_path) not in entrypoint and not module_launch:
            errors.append(f"{label} implementation CLI is unresolvable")
    else:
        errors.append(f"{label} implementation entrypoint_kind is unsupported")


def _coverage_items(
    coverage: dict[str, Any],
    key: str,
    label: str,
    protocol_ids: set[str],
    errors: list[str],
) -> list[str]:
    rows = coverage.get(key)
    if not isinstance(rows, list) or not rows:
        errors.append(f"{label} inventory must not be empty")
        return []
    identifiers: list[str] = []
    for index, row in enumerate(rows):
        item_label = f"{label} {index}"
        if not isinstance(row, dict):
            errors.append(f"{item_label} is not an object")
            continue
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{item_label} id is absent")
            continue
        identifiers.append(identifier)
        if row.get("status") != "frozen":
            errors.append(f"{label} {identifier} status must be frozen")
        if row.get("protocol_id") not in protocol_ids:
            errors.append(f"{label} {identifier} names an unknown protocol")
    duplicates = sorted({item for item in identifiers if identifiers.count(item) > 1})
    errors.extend(f"duplicate {label} id: {identifier}" for identifier in duplicates)
    return identifiers


def _validate_categories(
    coverage: dict[str, Any], protocol_ids: set[str], errors: list[str]
) -> tuple[list[str], list[str]]:
    rows = coverage.get("categories")
    if not isinstance(rows, list):
        errors.append("instrument categories are absent")
        return [], []
    identifiers = [row.get("id") for row in rows if isinstance(row, dict)]
    duplicates = sorted(
        {str(identifier) for identifier in identifiers if identifiers.count(identifier) > 1}
    )
    errors.extend(f"duplicate instrument category id: {identifier}" for identifier in duplicates)
    expected = set(INSTRUMENT_CATEGORIES)
    observed = {identifier for identifier in identifiers if isinstance(identifier, str)}
    errors.extend(
        f"required instrument category is absent: {item}" for item in sorted(expected - observed)
    )
    errors.extend(f"unknown instrument category: {item}" for item in sorted(observed - expected))

    covered: list[str] = []
    not_applicable: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            errors.append("instrument category row is not an object")
            continue
        identifier = row.get("id")
        if identifier not in expected:
            continue
        disposition = row.get("disposition")
        if disposition == "covered":
            covered.append(identifier)
        elif disposition == "not_applicable":
            not_applicable.append(identifier)
            for field in ("protocol_citation", "reason"):
                if not isinstance(row.get(field), str) or not row[field].strip():
                    errors.append(f"instrument category {identifier} {field} is absent")
            citation = row.get("protocol_citation")
            if isinstance(citation, str) and citation.split("#", 1)[0] not in protocol_ids:
                errors.append(
                    f"instrument category {identifier} protocol_citation names an unknown protocol"
                )
        else:
            errors.append(f"instrument category {identifier} disposition is unresolved")
    return covered, not_applicable


def validate_inventory(inventory: Any, project_root: Path) -> dict[str, Any]:
    """Validate AUD-1/AUD-2 coverage and return a deterministic gap report."""
    root = Path(project_root).resolve()
    errors: list[str] = []
    retained: list[dict[str, str]] = []
    if not isinstance(inventory, dict):
        inventory = {}
        errors.append("inventory root must be an object")
    if inventory.get("schema_version") != SCHEMA_VERSION:
        errors.append("inventory schema_version is unsupported")
    if not isinstance(inventory.get("inventory_id"), str) or not inventory.get("inventory_id"):
        errors.append("inventory_id is absent")

    freeze = inventory.get("protocol_freeze")
    protocol_ids: set[str] = set()
    if not isinstance(freeze, dict):
        errors.append("protocol_freeze is absent")
        protocols: list[Any] = []
    else:
        if freeze.get("status") != "frozen":
            errors.append("protocol freeze status must be frozen")
        protocols = freeze.get("protocols")
        if not isinstance(protocols, list) or not protocols:
            errors.append("frozen protocol inventory must not be empty")
            protocols = []
    protocol_id_rows: list[str] = []
    for index, protocol in enumerate(protocols):
        label = f"protocol {index}"
        if not isinstance(protocol, dict):
            errors.append(f"{label} is not an object")
            continue
        identifier = protocol.get("id")
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{label} id is absent")
        else:
            protocol_ids.add(identifier)
            protocol_id_rows.append(identifier)
            label = f"protocol {identifier}"
        if protocol.get("status") != "frozen":
            errors.append(f"{label} status must be frozen")
        digest = protocol.get("sha256")
        if protocol.get("frozen_id") != f"sha256:{digest}" or not _is_hash(digest):
            errors.append(f"{label} frozen_id is invalid")
        _verify_bound_file(root, protocol, label, errors, retained)
    for identifier in sorted(
        {item for item in protocol_id_rows if protocol_id_rows.count(item) > 1}
    ):
        errors.append(f"duplicate protocol id: {identifier}")

    coverage = inventory.get("coverage")
    if not isinstance(coverage, dict):
        coverage = {}
        errors.append("coverage registry is absent")
    primary_ids = _coverage_items(
        coverage, "primary_estimands", "primary estimand", protocol_ids, errors
    )
    exclusion_ids = _coverage_items(
        coverage, "exclusion_rules", "exclusion rule", protocol_ids, errors
    )
    covered_categories, not_applicable_categories = _validate_categories(
        coverage, protocol_ids, errors
    )

    all_coverage_ids = [*primary_ids, *exclusion_ids, *INSTRUMENT_CATEGORIES]
    for identifier in sorted(
        {item for item in all_coverage_ids if all_coverage_ids.count(item) > 1}
    ):
        errors.append(f"duplicate cross-registry coverage id: {identifier}")
    known_coverage = set(all_coverage_ids)
    required_coverage = set(primary_ids) | set(exclusion_ids) | set(covered_categories)

    entries = inventory.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("instrument entries must not be empty")
        entries = []
    entry_ids = [
        row.get("id") for row in entries if isinstance(row, dict) and isinstance(row.get("id"), str)
    ]
    duplicate_entries = sorted({item for item in entry_ids if entry_ids.count(item) > 1})
    errors.extend(f"duplicate instrument id: {identifier}" for identifier in duplicate_entries)
    known_entries = set(entry_ids)
    mapped: set[str] = set()

    for index, entry in enumerate(entries):
        label = f"instrument {index}"
        if not isinstance(entry, dict):
            errors.append(f"{label} is not an object")
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier:
            errors.append(f"{label} id is absent")
        else:
            label = f"instrument {identifier}"

        coverage_ids = entry.get("coverage_ids")
        if not _nonempty_strings(coverage_ids):
            errors.append(f"{label} coverage_ids must be a unique non-empty string list")
            coverage_ids = []
        for coverage_id in coverage_ids:
            if coverage_id not in known_coverage:
                errors.append(f"{label} names unknown coverage id: {coverage_id}")
            elif coverage_id in not_applicable_categories:
                errors.append(f"{label} maps not-applicable category: {coverage_id}")
            else:
                mapped.add(coverage_id)

        source = entry.get("source")
        if not isinstance(source, dict):
            errors.append(f"{label} source is absent")
        else:
            if not isinstance(source.get("schema"), str) or not source.get("schema"):
                errors.append(f"{label} source schema is absent")
            if not _nonempty_strings(source.get("fields")):
                errors.append(f"{label} source fields must be a unique non-empty string list")

        implementation = entry.get("implementation")
        if not isinstance(implementation, dict):
            errors.append(f"{label} implementation record is absent")
            implementation = {}
        implementation_path = _verify_bound_file(
            root, implementation, f"{label} implementation", errors, retained
        )
        _verify_entrypoint(implementation, implementation_path, label, errors)
        if not _nonempty_strings(entry.get("output_fields")):
            errors.append(f"{label} output_fields must be a unique non-empty string list")

        authorship = entry.get("authorship")
        if not isinstance(authorship, dict):
            errors.append(f"{label} authorship record is absent")
        else:
            if not _nonempty_strings(authorship.get("responsible_authors")):
                errors.append(f"{label} authorship responsible_authors is absent")
            authorship_file = {
                "path": authorship.get("record_path"),
                "sha256": authorship.get("sha256"),
            }
            authorship_path = _verify_bound_file(
                root, authorship_file, f"{label} authorship", errors, retained
            )
            if authorship_path is not None:
                try:
                    authorship_record = json.loads(authorship_path.read_text())
                except (OSError, json.JSONDecodeError):
                    errors.append(f"{label} authorship record is not readable JSON")
                else:
                    recorded_authors = (
                        authorship_record.get("authors")
                        if isinstance(authorship_record, dict)
                        else None
                    )
                    responsible = authorship.get("responsible_authors")
                    if (
                        not _nonempty_strings(recorded_authors)
                        or not isinstance(responsible, list)
                        or not set(responsible).issubset(recorded_authors)
                    ):
                        errors.append(
                            f"{label} authorship responsible_authors do not match the bound record"
                        )

        for edge in ("upstream_ids", "downstream_ids"):
            values = entry.get(edge)
            if (
                not isinstance(values, list)
                or any(not isinstance(item, str) or not item for item in values)
                or len(values) != len(set(values))
            ):
                errors.append(f"{label} {edge} must be a unique string list")
                continue
            direction = edge.removesuffix("_ids")
            for target in values:
                if target == identifier:
                    errors.append(f"{label} has a self {direction} edge")
                elif target not in known_entries:
                    errors.append(f"{label} names unknown {direction}: {target}")

    errors.extend(
        f"required coverage id is uncovered: {item}" for item in sorted(required_coverage - mapped)
    )
    errors = sorted(set(errors))
    valid = not errors
    recorded_at = datetime.now(UTC)
    return {
        "confirmatory_ready": False,
        "coverage": {
            "categories_covered": len(set(covered_categories) & mapped),
            "categories_not_applicable": len(set(not_applicable_categories)),
            "categories_total": len(INSTRUMENT_CATEGORIES),
            "exclusion_rules_covered": len(set(exclusion_ids) & mapped),
            "exclusion_rules_total": len(set(exclusion_ids)),
            "primary_estimands_covered": len(set(primary_ids) & mapped),
            "primary_estimands_total": len(set(primary_ids)),
        },
        "environment": {
            "machine": platform.machine(),
            "os": platform.system(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        },
        "errors": errors,
        "inventory_id": inventory.get("inventory_id"),
        "inventory_sha256": _sha256_bytes(_canonical_bytes(inventory)),
        "inventory_valid": valid,
        "limitations": [
            "inventory validation does not execute mutation cases",
            "inventory validation does not provide outside-authorship review",
            "a valid inventory remains blocked pending AUD-3 through AUD-12",
        ],
        "protocol_ids": sorted(protocol_ids),
        "publication_gate": "blocked_pending_AUD_3_through_AUD_12" if valid else "blocked",
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "referenced_artifacts": sorted(retained, key=lambda row: (row["kind"], row["path"])),
        "schema_version": REPORT_SCHEMA_VERSION,
        "session_id": f"instrument-inventory-{recorded_at.strftime('%Y%m%dT%H%M%S%fZ')}",
        "source_sha256": _sha256_bytes(Path(__file__).read_bytes()),
    }


def write_inventory_validation(
    inventory_path: Path, output: Path, *, project_root: Path = _PROJECT_ROOT
) -> dict[str, Any]:
    """Validate one inventory and retain its report without overwriting."""
    inventory_path = Path(inventory_path)
    output = Path(output)
    if output.exists():
        raise InventoryError(f"inventory validation already exists: {output}")
    try:
        inventory = json.loads(inventory_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read instrument inventory {inventory_path}: {exc}") from exc
    report = validate_inventory(inventory, Path(project_root))
    report["inventory_file_sha256"] = _sha256_bytes(inventory_path.read_bytes())
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise InventoryError(f"inventory validation already exists: {output}") from exc
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-inventory")
    validate.add_argument("--inventory", type=Path, required=True)
    validate.add_argument("--output", type=Path, required=True)
    validate.add_argument("--project-root", type=Path, default=_PROJECT_ROOT)
    args = parser.parse_args(argv)
    try:
        report = write_inventory_validation(
            args.inventory, args.output, project_root=args.project_root
        )
    except InventoryError as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "confirmatory_ready": report["confirmatory_ready"],
                "inventory_valid": report["inventory_valid"],
                "ok": report["inventory_valid"],
                "output": str(args.output),
                "publication_gate": report["publication_gate"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["inventory_valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
