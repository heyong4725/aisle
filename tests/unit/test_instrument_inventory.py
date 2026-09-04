"""Acceptance tests for SPEC 430 instrument-inventory validation."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness.instrument_audit import (
    INSTRUMENT_CATEGORIES,
    InventoryError,
    validate_inventory,
    write_inventory_validation,
)

pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_inventory(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    protocol = root / "protocol.json"
    implementation = root / "instrument.py"
    authorship = root / "authorship.json"
    protocol.write_text('{"primary":"session success"}\n')
    implementation.write_text("def analyze(rows):\n    return rows\n")
    authorship.write_text('{"authors":["independent-fixture-author"]}\n')
    protocol_hash = _sha256(protocol)
    coverage_ids = [
        "estimand:session-success",
        "exclusion:infrastructure",
        *INSTRUMENT_CATEGORIES,
    ]
    return {
        "coverage": {
            "categories": [
                {"disposition": "covered", "id": category} for category in INSTRUMENT_CATEGORIES
            ],
            "exclusion_rules": [
                {
                    "id": "exclusion:infrastructure",
                    "protocol_id": "protocol:synthetic",
                    "status": "frozen",
                }
            ],
            "primary_estimands": [
                {
                    "id": "estimand:session-success",
                    "protocol_id": "protocol:synthetic",
                    "status": "frozen",
                }
            ],
        },
        "entries": [
            {
                "authorship": {
                    "record_path": "authorship.json",
                    "responsible_authors": ["independent-fixture-author"],
                    "sha256": _sha256(authorship),
                },
                "coverage_ids": coverage_ids,
                "downstream_ids": [],
                "id": "instrument:synthetic-analyzer",
                "implementation": {
                    "entrypoint": "instrument:analyze",
                    "entrypoint_kind": "python_callable",
                    "path": "instrument.py",
                    "sha256": _sha256(implementation),
                },
                "output_fields": ["estimate", "inclusion_status"],
                "source": {"fields": ["session_id", "outcome"], "schema": "fixture.v1"},
                "upstream_ids": [],
            }
        ],
        "inventory_id": "inventory:synthetic-complete",
        "protocol_freeze": {
            "protocols": [
                {
                    "frozen_id": f"sha256:{protocol_hash}",
                    "id": "protocol:synthetic",
                    "path": "protocol.json",
                    "sha256": protocol_hash,
                    "status": "frozen",
                }
            ],
            "status": "frozen",
        },
        "schema_version": "aisle.instrument-inventory.v1",
    }


def test_complete_frozen_inventory_is_valid_and_reports_mechanical_coverage(tmp_path: Path):
    """AUD-1/AUD-2: complete frozen coverage is computed, not inferred from prose."""
    inventory = _valid_inventory(tmp_path)

    report = validate_inventory(inventory, tmp_path)

    assert report["inventory_valid"] is True
    assert report["publication_gate"] == "blocked_pending_AUD_3_through_AUD_12"
    assert report["errors"] == []
    assert report["coverage"] == {
        "categories_covered": 10,
        "categories_not_applicable": 0,
        "categories_total": 10,
        "exclusion_rules_covered": 1,
        "exclusion_rules_total": 1,
        "primary_estimands_covered": 1,
        "primary_estimands_total": 1,
    }
    assert report["confirmatory_ready"] is False


def test_unfrozen_protocol_and_unresolved_primary_item_fail_closed(tmp_path: Path):
    """AUD-1: proposed protocols or unresolved primary entries cannot validate."""
    inventory = _valid_inventory(tmp_path)
    inventory["protocol_freeze"]["status"] = "proposed"
    inventory["protocol_freeze"]["protocols"][0]["status"] = "proposed"
    inventory["protocol_freeze"]["protocols"][0]["frozen_id"] = "not_frozen"
    inventory["coverage"]["primary_estimands"][0]["status"] = "unresolved"

    report = validate_inventory(inventory, tmp_path)

    assert report["inventory_valid"] is False
    assert report["publication_gate"] == "blocked"
    assert any("protocol freeze status" in error for error in report["errors"])
    assert any("primary estimand" in error and "frozen" in error for error in report["errors"])


@pytest.mark.parametrize(
    "coverage_id",
    ["estimand:session-success", "exclusion:infrastructure", *INSTRUMENT_CATEGORIES],
)
def test_each_required_coverage_id_is_independently_enforced(tmp_path: Path, coverage_id: str):
    """AUD-1/AUD-2: every estimand, exclusion, and category must map to an instrument."""
    inventory = _valid_inventory(tmp_path)
    inventory["entries"][0]["coverage_ids"].remove(coverage_id)

    report = validate_inventory(inventory, tmp_path)

    assert report["inventory_valid"] is False
    assert any(coverage_id in error and "uncovered" in error for error in report["errors"])


def test_not_applicable_category_requires_protocol_citation_and_reason(tmp_path: Path):
    """AUD-2: category N/A is reviewable and does not masquerade as instrument coverage."""
    inventory = _valid_inventory(tmp_path)
    category = INSTRUMENT_CATEGORIES[-1]
    inventory["coverage"]["categories"][-1] = {
        "disposition": "not_applicable",
        "id": category,
        "protocol_citation": "protocol:synthetic#publication-outputs",
        "reason": "The synthetic protocol declares no publication transform.",
    }
    inventory["entries"][0]["coverage_ids"].remove(category)

    accepted = validate_inventory(inventory, tmp_path)
    assert accepted["inventory_valid"] is True
    assert accepted["coverage"]["categories_not_applicable"] == 1

    for missing in ("protocol_citation", "reason"):
        malformed = copy.deepcopy(inventory)
        del malformed["coverage"]["categories"][-1][missing]
        refused = validate_inventory(malformed, tmp_path)
        assert refused["inventory_valid"] is False
        assert any(category in error and missing in error for error in refused["errors"])

    unknown = copy.deepcopy(inventory)
    unknown["coverage"]["categories"][-1]["protocol_citation"] = "protocol:unknown#rule"
    refused = validate_inventory(unknown, tmp_path)
    assert refused["inventory_valid"] is False
    assert any(category in error and "unknown protocol" in error for error in refused["errors"])


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda value: value["entries"].append(copy.deepcopy(value["entries"][0])), "duplicate"),
        (
            lambda value: value["entries"][0]["coverage_ids"].append("estimand:unknown"),
            "unknown coverage id",
        ),
        (lambda value: value["entries"][0].update({"output_fields": []}), "output_fields"),
        (
            lambda value: value["entries"][0]["implementation"].update({"entrypoint": ""}),
            "entrypoint",
        ),
        (
            lambda value: value["entries"][0]["implementation"].update(
                {"entrypoint_kind": "unknown"}
            ),
            "entrypoint_kind",
        ),
        (
            lambda value: value["entries"][0]["authorship"].update({"responsible_authors": []}),
            "responsible_authors",
        ),
    ],
)
def test_malformed_duplicate_and_unknown_inventory_fields_fail_closed(
    tmp_path: Path, mutation, expected: str
):
    """AUD-1: ambiguous inventory structure cannot become a valid audit input."""
    inventory = _valid_inventory(tmp_path)
    mutation(inventory)

    report = validate_inventory(inventory, tmp_path)

    assert report["inventory_valid"] is False
    assert any(expected in error for error in report["errors"])


def test_hash_drift_unsafe_paths_and_authorship_drift_are_detected(tmp_path: Path):
    """AUD-1: protocol, instrument, and authorship records are content-bound."""
    for field, filename in (
        ("protocol", "protocol.json"),
        ("implementation", "instrument.py"),
        ("authorship", "authorship.json"),
    ):
        root = tmp_path / field
        root.mkdir()
        inventory = _valid_inventory(root)
        (root / filename).write_text("drifted\n")
        report = validate_inventory(inventory, root)
        assert report["inventory_valid"] is False
        assert any("hash mismatch" in error for error in report["errors"])

    unsafe = _valid_inventory(tmp_path / "unsafe")
    unsafe["entries"][0]["implementation"]["path"] = "../instrument.py"
    report = validate_inventory(unsafe, tmp_path / "unsafe")
    assert report["inventory_valid"] is False
    assert any("unsafe" in error for error in report["errors"])

    symlink_root = tmp_path / "symlink"
    inventory = _valid_inventory(symlink_root)
    real = symlink_root / "real"
    real.mkdir()
    (real / "instrument.py").write_text("def analyze(rows):\n    return rows\n")
    (symlink_root / "alias").symlink_to(real, target_is_directory=True)
    inventory["entries"][0]["implementation"] = {
        "entrypoint": "instrument:analyze",
        "entrypoint_kind": "python_callable",
        "path": "alias/instrument.py",
        "sha256": _sha256(real / "instrument.py"),
    }
    report = validate_inventory(inventory, symlink_root)
    assert report["inventory_valid"] is False
    assert any("unsafe: symlink" in error for error in report["errors"])


def test_responsible_authors_must_match_the_bound_authorship_record(tmp_path: Path):
    """AUD-1: inventory prose cannot substitute an unbound authorship assertion."""
    inventory = _valid_inventory(tmp_path)
    inventory["entries"][0]["authorship"]["responsible_authors"] = ["invented-author"]

    report = validate_inventory(inventory, tmp_path)

    assert report["inventory_valid"] is False
    assert any("do not match the bound record" in error for error in report["errors"])


def test_python_callable_and_cli_entrypoints_must_resolve_to_the_bound_file(tmp_path: Path):
    """AUD-1: named callable/CLI identities cannot be decorative strings."""
    inventory = _valid_inventory(tmp_path)
    inventory["entries"][0]["implementation"]["entrypoint"] = "instrument:missing"
    report = validate_inventory(inventory, tmp_path)
    assert report["inventory_valid"] is False
    assert any("callable is unresolvable" in error for error in report["errors"])

    cli = _valid_inventory(tmp_path / "cli")
    cli["entries"][0]["implementation"].update(
        {"entrypoint": ["python", "instrument.py"], "entrypoint_kind": "cli"}
    )
    assert validate_inventory(cli, tmp_path / "cli")["inventory_valid"] is True

    cli["entries"][0]["implementation"]["entrypoint"] = ["python", "other.py"]
    report = validate_inventory(cli, tmp_path / "cli")
    assert report["inventory_valid"] is False
    assert any("CLI is unresolvable" in error for error in report["errors"])


def test_dependency_edges_must_name_distinct_known_instruments(tmp_path: Path):
    """AUD-1: upstream/downstream inventory topology is explicit and resolvable."""
    inventory = _valid_inventory(tmp_path)
    inventory["entries"][0]["downstream_ids"] = ["instrument:missing"]
    inventory["entries"][0]["upstream_ids"] = ["instrument:synthetic-analyzer"]

    report = validate_inventory(inventory, tmp_path)

    assert report["inventory_valid"] is False
    assert any("unknown downstream" in error for error in report["errors"])
    assert any("self upstream" in error for error in report["errors"])


def test_writer_cli_and_retained_current_gap_are_non_overwriting_and_source_bound(
    tmp_path: Path,
):
    """AUD-1/AUD-2: current incompleteness is retained as a blocker, not hidden."""
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps(_valid_inventory(tmp_path), indent=2))
    output = tmp_path / "report.json"

    report = write_inventory_validation(inventory_path, output, project_root=tmp_path)
    assert json.loads(output.read_text()) == report
    with pytest.raises(InventoryError, match="already exists"):
        write_inventory_validation(inventory_path, output, project_root=tmp_path)

    cli_output = tmp_path / "cli-report.json"
    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.instrument_audit",
            "validate-inventory",
            "--inventory",
            str(inventory_path),
            "--project-root",
            str(tmp_path),
            "--output",
            str(cli_output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert created.returncode == 0, created.stderr
    assert json.loads(created.stdout)["inventory_valid"] is True

    primary = _PROJECT_ROOT / "analysis/instrument-audit/inventory-capability/current-gap.json"
    retained = json.loads(primary.read_text())
    source = _PROJECT_ROOT / "src/aisle/harness/instrument_audit.py"
    assert retained["source_sha256"] == _sha256(source)
    assert retained["inventory_valid"] is False
    assert retained["publication_gate"] == "blocked"
    assert retained["confirmatory_ready"] is False
