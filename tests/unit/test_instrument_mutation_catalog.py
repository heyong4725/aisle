"""Acceptance tests for SPEC 430 mutation-catalog and oracle validation."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness.instrument_audit import INSTRUMENT_CATEGORIES
from aisle.harness.instrument_mutations import (
    CatalogError,
    validate_mutation_catalog,
    write_mutation_catalog_validation,
)

pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).parents[2]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(root: Path) -> tuple[dict, dict]:
    root.mkdir(parents=True, exist_ok=True)
    protocol = root / "protocol.json"
    instrument = root / "instrument.py"
    authorship = root / "authorship.json"
    fixture = root / "fixture.json"
    derivation = root / "oracle.md"
    protocol.write_text('{"frozen":true}\n')
    instrument.write_text("def analyze(rows):\n    return rows\n")
    authorship.write_text('{"authors":["instrument-author"]}\n')
    fixture.write_text('{"rows":[1,2]}\n')
    derivation.write_text("Hand derivation: (1 + 2) / 2 = 1.5.\n")
    protocol_hash = _sha(protocol)
    coverage = ["estimand:primary", "exclusion:infra", *INSTRUMENT_CATEGORIES]
    inventory = {
        "coverage": {
            "categories": [
                {"disposition": "covered", "id": item} for item in INSTRUMENT_CATEGORIES
            ],
            "exclusion_rules": [
                {"id": "exclusion:infra", "protocol_id": "protocol:p", "status": "frozen"}
            ],
            "primary_estimands": [
                {"id": "estimand:primary", "protocol_id": "protocol:p", "status": "frozen"}
            ],
        },
        "entries": [
            {
                "authorship": {
                    "record_path": "authorship.json",
                    "responsible_authors": ["instrument-author"],
                    "sha256": _sha(authorship),
                },
                "coverage_ids": coverage,
                "downstream_ids": [],
                "id": "instrument:analyzer",
                "implementation": {
                    "entrypoint": "instrument:analyze",
                    "entrypoint_kind": "python_callable",
                    "path": "instrument.py",
                    "sha256": _sha(instrument),
                },
                "output_fields": ["estimate"],
                "source": {"fields": ["value"], "schema": "fixture.v1"},
                "upstream_ids": [],
            }
        ],
        "inventory_id": "inventory:frozen",
        "protocol_freeze": {
            "protocols": [
                {
                    "frozen_id": f"sha256:{protocol_hash}",
                    "id": "protocol:p",
                    "path": "protocol.json",
                    "sha256": protocol_hash,
                    "status": "frozen",
                }
            ],
            "status": "frozen",
        },
        "schema_version": "aisle.instrument-inventory.v1",
    }
    catalog = {
        "catalog_id": "catalog:synthetic-complete",
        "inventory_sha256": hashlib.sha256(
            json.dumps(inventory, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest(),
        "mutations": [
            {
                "coverage_ids": coverage,
                "evidence_paths": ["cases/MUT-001/stdout.txt", "cases/MUT-001/detector.json"],
                "expected_detection": {
                    "comparison": "exact",
                    "layer": "instrument:analyzer",
                    "value": {"status": "excluded"},
                },
                "fixture": {"path": "fixture.json", "sha256": _sha(fixture)},
                "id": "MUT-001",
                "mutation_family": "arithmetic_and_exclusion",
                "operator": {
                    "kind": "json_patch",
                    "operation": "replace",
                    "path": "/rows/0",
                    "value": -1,
                },
                "oracle": {
                    "derivation_path": "oracle.md",
                    "kind": "hand_derived",
                    "sha256": _sha(derivation),
                    "shares_production_helpers": False,
                },
                "rationale": "Synthetic mutation changes a decision-bearing value.",
                "severity": "critical",
                "target_instrument_id": "instrument:analyzer",
            }
        ],
        "schema_version": "aisle.instrument-mutation-catalog.v1",
    }
    return inventory, catalog


def test_complete_catalog_mechanically_covers_inventory_with_independent_oracle(tmp_path: Path):
    """AUD-3/AUD-5: every required id maps to a bound mutation and fixed oracle."""
    inventory, catalog = _inputs(tmp_path)
    report = validate_mutation_catalog(inventory, catalog, tmp_path)
    assert report["catalog_valid"] is True
    assert report["errors"] == []
    assert report["coverage"] == {
        "covered": 12,
        "required": 12,
        "uncovered": [],
    }
    assert report["confirmatory_ready"] is False
    assert report["publication_gate"] == "blocked_pending_AUD_4_and_AUD_6_through_AUD_12"


@pytest.mark.parametrize(
    "coverage_id", ["estimand:primary", "exclusion:infra", *INSTRUMENT_CATEGORIES]
)
def test_every_primary_exclusion_and_category_requires_a_catalog_mutation(
    tmp_path: Path, coverage_id: str
):
    """AUD-3: catalog coverage is checked from inventory ids, never mutation names."""
    inventory, catalog = _inputs(tmp_path)
    catalog["mutations"][0]["coverage_ids"].remove(coverage_id)
    report = validate_mutation_catalog(inventory, catalog, tmp_path)
    assert report["catalog_valid"] is False
    assert coverage_id in report["coverage"]["uncovered"]


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (lambda c: c["mutations"].append(copy.deepcopy(c["mutations"][0])), "duplicate"),
        (
            lambda c: c["mutations"][0].update({"target_instrument_id": "instrument:unknown"}),
            "unknown target",
        ),
        (
            lambda c: c["mutations"][0]["coverage_ids"].append("estimand:unknown"),
            "unknown coverage",
        ),
        (lambda c: c["mutations"][0].update({"severity": "unknown"}), "severity"),
        (lambda c: c["mutations"][0].update({"rationale": ""}), "rationale"),
        (lambda c: c["mutations"][0].update({"evidence_paths": []}), "evidence_paths"),
        (lambda c: c["mutations"][0].update({"operator": {}}), "operator"),
    ],
)
def test_ambiguous_duplicate_or_unbound_catalog_rows_fail_closed(
    tmp_path: Path, mutation, expected: str
):
    """AUD-3: each mutation declares one exact, resolvable contract."""
    inventory, catalog = _inputs(tmp_path)
    mutation(catalog)
    report = validate_mutation_catalog(inventory, catalog, tmp_path)
    assert report["catalog_valid"] is False
    assert any(expected in error for error in report["errors"])


def test_fixture_hash_inventory_hash_and_evidence_paths_are_fail_closed(tmp_path: Path):
    """AUD-3/AUD-5: catalog inputs and retained-output paths are immutable and safe."""
    inventory, catalog = _inputs(tmp_path)
    catalog["inventory_sha256"] = "0" * 64
    catalog["mutations"][0]["fixture"]["sha256"] = "1" * 64
    catalog["mutations"][0]["evidence_paths"] = ["../escaped.txt"]
    report = validate_mutation_catalog(inventory, catalog, tmp_path)
    assert report["catalog_valid"] is False
    assert any("inventory hash mismatch" in error for error in report["errors"])
    assert any("fixture hash mismatch" in error for error in report["errors"])
    assert any("evidence path is unsafe" in error for error in report["errors"])


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (
            lambda m: m["expected_detection"].update({"layer": "instrument:unknown"}),
            "detection layer",
        ),
        (
            lambda m: m["expected_detection"].update({"comparison": "anything_nonzero"}),
            "comparison",
        ),
        (lambda m: m.update({"oracle": {}}), "oracle"),
        (lambda m: m["oracle"].update({"shares_production_helpers": True}), "shares production"),
        (
            lambda m: m["oracle"].update({"derivation_path": "instrument.py", "sha256": None}),
            "independent",
        ),
    ],
)
def test_oracle_layer_value_and_independence_are_fixed_before_execution(
    tmp_path: Path, mutation, expected: str
):
    """AUD-5: crashes, wrong layers, and production-derived expectations cannot pass."""
    inventory, catalog = _inputs(tmp_path)
    mutation(catalog["mutations"][0])
    report = validate_mutation_catalog(inventory, catalog, tmp_path)
    assert report["catalog_valid"] is False
    assert any(expected in error for error in report["errors"])


def test_invalid_inventory_blocks_catalog_validation(tmp_path: Path):
    """AUD-3: a catalog cannot bless an unresolved or incomplete AUD-1 inventory."""
    inventory, catalog = _inputs(tmp_path)
    inventory["protocol_freeze"]["status"] = "proposed"
    report = validate_mutation_catalog(inventory, catalog, tmp_path)
    assert report["catalog_valid"] is False
    assert any("inventory is invalid" in error for error in report["errors"])


def test_writer_cli_and_primary_gap_are_non_overwriting_and_source_bound(tmp_path: Path):
    """AUD-3/AUD-5: absent live coverage remains retained and publication-blocking."""
    inventory, catalog = _inputs(tmp_path)
    inventory_path = tmp_path / "inventory.json"
    catalog_path = tmp_path / "catalog.json"
    inventory_path.write_text(json.dumps(inventory))
    catalog_path.write_text(json.dumps(catalog))
    output = tmp_path / "report.json"
    report = write_mutation_catalog_validation(
        inventory_path, catalog_path, output, project_root=tmp_path
    )
    assert json.loads(output.read_text()) == report
    with pytest.raises(CatalogError, match="already exists"):
        write_mutation_catalog_validation(
            inventory_path, catalog_path, output, project_root=tmp_path
        )

    cli_output = tmp_path / "cli.json"
    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.instrument_mutations",
            "--inventory",
            str(inventory_path),
            "--catalog",
            str(catalog_path),
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

    primary = (
        _PROJECT_ROOT / "analysis/instrument-audit/mutation-catalog-capability/current-gap.json"
    )
    retained = json.loads(primary.read_text())
    source = _PROJECT_ROOT / "src/aisle/harness/instrument_mutations.py"
    assert retained["source_sha256"] == _sha(source)
    assert retained["catalog_valid"] is False
    assert retained["publication_gate"] == "blocked"
    assert retained["confirmatory_ready"] is False
