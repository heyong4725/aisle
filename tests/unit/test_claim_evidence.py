"""Acceptance tests for the canonical claim-to-evidence catalog (SPEC 410).

The fixtures build small tracked repositories so path, evidence-kind, marker,
status-source, architecture, publication-boundary, and release-review checks
exercise the same contract as the project catalog without borrowing evidence
from the checkout under test.
"""

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from cli_helpers import REPO_ROOT, run_tool

pytestmark = pytest.mark.unit


def _na(reason: str) -> dict[str, str]:
    return {"value": "not_applicable", "rationale": reason}


def _scope(environment: str = "simulation") -> dict[str, str]:
    return {
        "environment": environment,
        "task": "fixture-task",
        "perception": "L0",
        "agent_model": "fixture-agent",
        "platform": "fixture-platform",
    }


def _row(
    claim_id: str,
    *,
    claim_type: str = "structural",
    status: str = "unrun",
    safety_category: str | None = None,
) -> dict:
    row = {
        "id": claim_id,
        "claim": f"Fixture claim {claim_id}",
        "type": claim_type,
        "status": status,
        "scope": _scope(),
        "experimental_unit": _na("No measured session exists for this fixture claim."),
        "sample": _na("No sample exists for an unrun fixture claim."),
        "uncertainty": _na("No estimate exists for an unrun fixture claim."),
        "attestation": {
            "status": "unattested",
            "rationale": "The fixture carries no empirical attestation.",
        },
        "evidence": [_na("No supporting result exists for an unrun fixture claim.")],
        "counterevidence": [_na("No counterevidence has been collected for this fixture.")],
        "limitations": ["This is a deliberately bounded test fixture."],
        "allowed_wording": {
            "readme": f"UNRUN: {claim_id} is not yet evaluated.",
            "technical_report": f"UNRUN: {claim_id} is not yet evaluated.",
            "focused_paper": f"UNRUN: {claim_id} is not yet evaluated.",
        },
        "headlines": [],
    }
    if safety_category is not None:
        row["safety_category"] = safety_category
    return row


def _supported_topology_row() -> dict:
    row = _row("safety-topology", status="supported", safety_category="graph_topology")
    row.update(
        {
            "experimental_unit": _na("This is a structural repository claim."),
            "sample": _na("This is a structural repository claim."),
            "uncertainty": _na("Sampling uncertainty does not apply to a structural claim."),
            "attestation": {
                "status": "repository_verified",
                "rationale": "Tracked source and an independent unit test establish the structure.",
            },
            "evidence": [
                {
                    "kind": "source",
                    "path": "src/topology.py",
                    "scope": "agnostic",
                    "rationale": "The fixture source declares the graph boundary.",
                },
                {
                    "kind": "test",
                    "path": "tests/unit/test_topology.py",
                    "node": "tests/unit/test_topology.py::test_topology",
                    "scope": "agnostic",
                    "rationale": "The test independently checks the declared boundary.",
                },
            ],
            "allowed_wording": {
                "readme": "SUPPORTED structural claim: declared graph paths traverse the guard.",
                "technical_report": (
                    "SUPPORTED structural claim: declared graph paths traverse the guard."
                ),
                "focused_paper": (
                    "SUPPORTED structural claim: declared graph paths traverse the guard."
                ),
            },
            "headlines": [
                {
                    "path": "README.md",
                    "marker": "safety-topology/readme",
                }
            ],
        }
    )
    return row


SAFETY_CATEGORIES = (
    "graph_topology",
    "kinematic_enforcement",
    "semantic_detection",
    "identity_authorization",
    "observed_outcomes",
)


def _catalog() -> dict:
    rows = [_supported_topology_row()]
    rows.extend(
        _row(f"safety-{category.replace('_', '-')}", safety_category=category)
        for category in SAFETY_CATEGORIES
        if category != "graph_topology"
    )
    return {
        "schema_version": 1,
        "canonical_status": {
            "path": "README.md",
            "marker": "current-status:canonical",
            "anchor": "#current-project-status",
        },
        "overview_documents": [
            {"path": "README.md", "role": "canonical"},
            {
                "path": "docs/overview.md",
                "role": "snapshot",
                "snapshot_date": "2026-08-31",
                "canonical_link": "../README.md#current-project-status",
            },
        ],
        "architecture": {
            "path": "docs/architecture.md",
            "marker": "architecture/four-zone-boundary",
            "experimental_unit": "coding_agent_session",
            "zones": {
                "mutable_participant": "Agent-controlled source and configuration.",
                "frozen_evaluator": "Read-only scoring and admissibility code.",
                "trusted_actuation": "Scoped command enforcement boundary.",
                "hidden_controller": "Task selection, randomization, and hidden faults.",
            },
            "inaccessible": ["sealed task and fault selection"],
            "forbidden": ["direct actuation outside declared participant interfaces"],
            "threat_model_issue": "#350",
        },
        "publications": {
            "technical_report": {
                "path": "docs/AISLE-technical-report.md",
                "marker": "publication-purpose/technical-report",
                "purpose": "Preserve the complete project and systems record.",
                "in_scope": ["complete_project_record", "historical_development_results"],
                "out_of_scope": ["focused_confirmatory_headline"],
            },
            "focused_paper": {
                "path": "docs/paper/aisle-paper.md",
                "marker": "publication-purpose/focused-paper",
                "purpose": "Test typed versus monolithic and typed evidence versus logs.",
                "in_scope": ["focused_confirmatory_headline"],
                "out_of_scope": ["historical_development_results"],
            },
        },
        "terminology_review": {
            "status": "pending",
            "required_before": "public_benchmark_release",
            "review_record": _na("An independent terminology reviewer has not signed yet."),
        },
        "claims": rows,
    }


def _write_fixture(root: Path, catalog: dict | None = None) -> dict:
    catalog = deepcopy(catalog or _catalog())
    root.joinpath("docs/paper").mkdir(parents=True)
    root.joinpath("src").mkdir()
    root.joinpath("tests/unit").mkdir(parents=True)

    root.joinpath("README.md").write_text(
        "# Fixture\n\n"
        "<!-- current-status:canonical -->\n"
        "## Current project status\n\n"
        "<!-- claim:safety-topology/readme -->\n"
        "SUPPORTED structural claim: declared graph paths traverse the guard.\n",
        encoding="utf-8",
    )
    root.joinpath("docs/overview.md").write_text(
        "# Overview\n\n"
        "<!-- status-snapshot:2026-08-31 canonical:../README.md#current-project-status -->\n"
        "Snapshot dated 2026-08-31; the [README status]"
        "(../README.md#current-project-status) controls on conflict.\n",
        encoding="utf-8",
    )
    root.joinpath("docs/architecture.md").write_text(
        "# Architecture\n\n"
        "<!-- claim:architecture/four-zone-boundary -->\n"
        "The coding-agent session is the experimental unit. The four zones are "
        "mutable participant, frozen evaluator, trusted actuation, and hidden controller. "
        "Sealed selection is inaccessible; direct actuation is forbidden. The wider bypass "
        "threat model is deferred to issue #350.\n",
        encoding="utf-8",
    )
    root.joinpath("docs/AISLE-technical-report.md").write_text(
        "# Technical report\n\n"
        "<!-- claim:publication-purpose/technical-report -->\n"
        "Purpose: preserve the complete project and systems record, including historical "
        "development results; focused confirmatory headlines are out of scope here.\n",
        encoding="utf-8",
    )
    root.joinpath("docs/paper/aisle-paper.md").write_text(
        "# Focused paper\n\n"
        "<!-- claim:publication-purpose/focused-paper -->\n"
        "Purpose: test typed versus monolithic and typed evidence versus logs. Historical "
        "development results remain outside this focused confirmatory headline.\n",
        encoding="utf-8",
    )
    root.joinpath("src/topology.py").write_text("DECLARED_GUARD_PATH = True\n", encoding="utf-8")
    root.joinpath("tests/unit/test_topology.py").write_text(
        "def test_topology():\n    assert True\n", encoding="utf-8"
    )
    root.joinpath("docs/claim-evidence.yaml").write_text(
        yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8"
    )

    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, capture_output=True)
    return catalog


def _save_catalog(root: Path, catalog: dict) -> None:
    root.joinpath("docs/claim-evidence.yaml").write_text(
        yaml.safe_dump(catalog, sort_keys=False), encoding="utf-8"
    )


def _run(root: Path, mode: str = "--check", *extra: str):
    return run_tool(
        "claim_evidence.py",
        "--root",
        str(root),
        "--catalog",
        "docs/claim-evidence.yaml",
        "--output",
        "docs/generated/claim-evidence.md",
        mode,
        *extra,
    )


def _report(proc) -> dict:
    assert proc.stdout, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("limitations", None),
        ("uncertainty", ""),
        ("attestation", {}),
        ("allowed_wording", {"readme": "UNRUN only"}),
    ],
)
def test_required_fields_and_explicit_absence_are_fail_closed(
    tmp_path: Path, field: str, bad_value: object
):
    """CLM-1: every row carries the complete schema and explicit N/A rationale."""
    catalog = _write_fixture(tmp_path)
    catalog["claims"][0][field] = bad_value
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert any(error["requirement"] == "CLM-1" for error in report["errors"])


@pytest.mark.parametrize(
    "status",
    ["weakened", "rejected", "unrun", "undecidable", "unattested", "hardware_pending"],
)
def test_every_non_supported_status_is_rendered_and_visibly_qualified(tmp_path: Path, status: str):
    """CLM-2, CLM-6, CLM-9: non-supported states remain distinct and visible."""
    catalog = _write_fixture(tmp_path)
    row = catalog["claims"][0]
    row["status"] = status
    qualifier = status.upper().replace("_", " ")
    row["allowed_wording"] = {
        surface: f"{qualifier}: the fixture claim is not supported."
        for surface in ("readme", "technical_report", "focused_paper")
    }
    tmp_path.joinpath("README.md").write_text(
        "# Fixture\n\n<!-- current-status:canonical -->\n## Current project status\n\n"
        f"<!-- claim:safety-topology/readme -->\n{qualifier}: "
        "the fixture claim is not supported.\n",
        encoding="utf-8",
    )
    _save_catalog(tmp_path, catalog)

    written = _run(tmp_path, "--write")
    assert written.returncode == 0, written.stdout + written.stderr
    text = tmp_path.joinpath("docs/generated/claim-evidence.md").read_text(encoding="utf-8")
    assert f"`{status}`" in text
    assert qualifier in text


def test_unknown_type_and_simulation_as_hardware_are_rejected(tmp_path: Path):
    """CLM-2: types are closed and simulation cannot establish hardware scope."""
    catalog = _write_fixture(tmp_path)
    catalog["claims"][0]["type"] = "marketing"
    catalog["claims"][0]["scope"]["environment"] = "hardware"
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert sum(error["requirement"] == "CLM-2" for error in report["errors"]) >= 2


def test_hardware_empirical_claim_requires_hardware_raw_records(tmp_path: Path):
    """CLM-2: a hardware-tagged source cannot launder simulation observations."""
    catalog = _write_fixture(tmp_path)
    raw = tmp_path / "analysis/simulation-record.json"
    raw.parent.mkdir()
    raw.write_text("{}\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "analysis/simulation-record.json"],
        check=True,
        capture_output=True,
    )
    row = catalog["claims"][0]
    row["type"] = "empirical"
    row["scope"]["environment"] = "hardware"
    row["evidence"][0]["scope"] = "hardware"
    row["evidence"].extend(
        [
            {
                "kind": "raw_record",
                "path": "analysis/simulation-record.json",
                "scope": "simulation",
                "rationale": "This observation came only from simulation.",
            },
            {
                "kind": "analyzer",
                "path": "src/topology.py",
                "scope": "agnostic",
                "rationale": "Fixture analyzer.",
            },
        ]
    )
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert any(
        error["requirement"] == "CLM-2" and error["code"] == "SIMULATION_AS_HARDWARE"
        for error in report["errors"]
    )


def test_supported_causal_claim_refuses_structural_evidence(tmp_path: Path):
    """CLM-2, CLM-3: a causal verdict needs registered control and session evidence."""
    catalog = _write_fixture(tmp_path)
    row = catalog["claims"][0]
    row["type"] = "causal"
    row["registered_control"] = _na("No control was run.")
    row["experimental_unit"] = "coding_agent_session"
    row["uncertainty"] = {"method": "none", "estimate": _na("No estimate exists.")}
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert any(error["requirement"] == "CLM-3" for error in report["errors"])


@pytest.mark.parametrize("mutation", ["missing", "wrong_kind", "missing_test_node"])
def test_referenced_evidence_is_tracked_existing_and_kind_correct(tmp_path: Path, mutation: str):
    """CLM-4: missing paths, false kinds, and absent test node ids fail CI."""
    catalog = _write_fixture(tmp_path)
    evidence = catalog["claims"][0]["evidence"]
    if mutation == "missing":
        evidence[0]["path"] = "src/does-not-exist.py"
    elif mutation == "wrong_kind":
        evidence[0]["kind"] = "raw_record"
    else:
        evidence[1]["node"] = "tests/unit/test_topology.py::test_absent"
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert any(error["requirement"] == "CLM-4" for error in report["errors"])


def test_all_five_safety_claims_are_separate_and_semantic_overclaim_is_refused(
    tmp_path: Path,
):
    """CLM-5: topology, kinematics, semantics, authorization, and outcomes are distinct."""
    catalog = _write_fixture(tmp_path)
    assert {row["safety_category"] for row in catalog["claims"]} == set(SAFETY_CATEGORIES)
    semantic = next(
        row for row in catalog["claims"] if row["safety_category"] == "semantic_detection"
    )
    semantic["allowed_wording"]["readme"] = (
        "UNRUN: the verifier prevents wrong-object delivery through the guard."
    )
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert any(error["requirement"] == "CLM-5" for error in report["errors"])


@pytest.mark.parametrize(
    "relative",
    [
        "README.md",
        "docs/AISLE-technical-report.md",
        "docs/Project_AISLE_Experiment_Design.md",
        "docs/architecture.md",
        "docs/glossary.md",
        "docs/paper/aisle-paper.md",
    ],
)
def test_public_surfaces_do_not_call_the_current_boundary_unbypassable(relative: str):
    """CLM-5: public wording stays within graph-path and frozen-artifact evidence."""
    text = REPO_ROOT.joinpath(relative).read_text(encoding="utf-8").lower()
    assert "unbypassable" not in text
    assert "cannot bypass" not in text


def test_a_missing_safety_category_fails_the_catalog(tmp_path: Path):
    """CLM-5: omitting even one safety layer cannot collapse the public claim."""
    catalog = _write_fixture(tmp_path)
    catalog["claims"] = [
        row for row in catalog["claims"] if row["safety_category"] != "observed_outcomes"
    ]
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert any(error["requirement"] == "CLM-5" for error in report["errors"])


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "missing", "unqualified"])
def test_headline_markers_are_known_unique_present_and_qualified(tmp_path: Path, mutation: str):
    """CLM-6: headline markers close both the orphan-claim and silent-status gaps."""
    catalog = _write_fixture(tmp_path)
    readme = tmp_path.joinpath("README.md")
    text = readme.read_text(encoding="utf-8")
    if mutation == "unknown":
        text += "\n<!-- claim:not-in-catalog/readme -->\n"
    elif mutation == "duplicate":
        text += "\n<!-- claim:safety-topology/readme -->\n"
    elif mutation == "missing":
        text = text.replace("<!-- claim:safety-topology/readme -->\n", "")
    else:
        catalog["claims"][0]["status"] = "unrun"
        catalog["claims"][0]["allowed_wording"] = {
            surface: "UNRUN: result not collected."
            for surface in ("readme", "technical_report", "focused_paper")
        }
        text = text.replace(
            "SUPPORTED structural claim: declared graph paths traverse the guard.",
            "The graph path result is described without its status.",
        )
    readme.write_text(text, encoding="utf-8")
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert any(error["requirement"] == "CLM-6" for error in report["errors"])


def test_write_is_deterministic_and_check_detects_generated_drift(tmp_path: Path):
    """CLM-7: --write is deterministic and --check fails on byte drift."""
    _write_fixture(tmp_path)
    first = _run(tmp_path, "--write")
    assert first.returncode == 0, first.stdout + first.stderr
    output = tmp_path / "docs/generated/claim-evidence.md"
    first_bytes = output.read_bytes()

    second = _run(tmp_path, "--write")
    assert second.returncode == 0, second.stdout + second.stderr
    assert output.read_bytes() == first_bytes

    output.write_text(output.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")
    checked = _run(tmp_path)
    report = _report(checked)
    assert checked.returncode != 0
    assert report["reason"] == "stale"


def test_only_one_canonical_current_status_is_allowed(tmp_path: Path):
    """CLM-8: README is canonical; other overviews are dated linked snapshots."""
    catalog = _write_fixture(tmp_path)
    catalog["overview_documents"][1] = {
        "path": "docs/overview.md",
        "role": "canonical",
    }
    tmp_path.joinpath("docs/overview.md").write_text(
        "# Competing current status\n\n<!-- current-status:canonical -->\n",
        encoding="utf-8",
    )
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert any(error["requirement"] == "CLM-8" for error in report["errors"])


def test_canonical_status_anchor_must_exist(tmp_path: Path):
    """CLM-8: snapshot links may not point at a nonexistent canonical heading."""
    catalog = _write_fixture(tmp_path)
    catalog["canonical_status"]["anchor"] = "#missing-status-heading"
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert any(
        error["requirement"] == "CLM-8" and error["code"] == "CANONICAL_ANCHOR_MISSING"
        for error in report["errors"]
    )


def test_generated_matrix_exposes_every_audit_field(tmp_path: Path):
    """CLM-9: rendered rows expose scope, evidence, unit, uncertainty, and caveats."""
    _write_fixture(tmp_path)
    proc = _run(tmp_path, "--write")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    text = tmp_path.joinpath("docs/generated/claim-evidence.md").read_text(encoding="utf-8")
    for heading in (
        "Scope",
        "Evidence",
        "Experimental unit and sample",
        "Uncertainty",
        "Attestation",
        "Counterevidence",
        "Limitations",
        "Allowed wording",
    ):
        assert heading in text


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("experimental_unit", "episode"),
        ("zones", {"mutable_participant": "only one zone"}),
        ("inaccessible", []),
        ("forbidden", []),
        ("threat_model_issue", "not-deferred"),
    ],
)
def test_architecture_contract_names_four_zones_unit_and_access_boundary(
    tmp_path: Path, field: str, bad_value: object
):
    """CLM-10: the external narrative separates trust zones and access meanings."""
    catalog = _write_fixture(tmp_path)
    catalog["architecture"][field] = bad_value
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert any(error["requirement"] == "CLM-10" for error in report["errors"])


def test_report_and_focused_paper_purposes_must_not_overlap(tmp_path: Path):
    """CLM-11: complete record and focused causal paper have explicit distinct scopes."""
    catalog = _write_fixture(tmp_path)
    catalog["publications"]["technical_report"]["in_scope"].append("focused_confirmatory_headline")
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path)
    report = _report(proc)
    assert proc.returncode != 0
    assert any(error["requirement"] == "CLM-11" for error in report["errors"])


def test_pending_external_review_blocks_release_but_not_catalog_audit(tmp_path: Path):
    """CLM-12: an absent independent terminology review is visible and release-blocking."""
    _write_fixture(tmp_path)
    assert _run(tmp_path, "--write").returncode == 0

    audit = _run(tmp_path)
    audit_report = _report(audit)
    assert audit.returncode == 0
    assert audit_report["release_ready"] is False
    assert audit_report["release_blockers"] == ["CLM-12"]

    release = _run(tmp_path, "--check", "--require-release-ready")
    release_report = _report(release)
    assert release.returncode != 0
    assert release_report["ok"] is False
    assert release_report["release_blockers"] == ["CLM-12"]


def test_completed_external_review_requires_independent_signed_dispositions(tmp_path: Path):
    """CLM-12: completed review needs independence, signature, and dispositions."""
    catalog = _write_fixture(tmp_path)
    record = tmp_path / "analysis/terminology-review/review.json"
    record.parent.mkdir(parents=True)
    record.write_text(
        json.dumps(
            {
                "reviewer": "External Reviewer",
                "independent_from_authorship": False,
                "signed_at": "2026-08-31T00:00:00Z",
                "signature": "sha256:fixture",
                "findings": [{"id": "T-1", "disposition": ""}],
            }
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "analysis/terminology-review/review.json"],
        check=True,
        capture_output=True,
    )
    catalog["terminology_review"] = {
        "status": "complete",
        "required_before": "public_benchmark_release",
        "review_record": "analysis/terminology-review/review.json",
    }
    _save_catalog(tmp_path, catalog)

    proc = _run(tmp_path, "--check", "--require-release-ready")
    report = _report(proc)
    assert proc.returncode != 0
    assert any(error["requirement"] == "CLM-12" for error in report["errors"])


def test_project_catalog_is_current_and_external_review_remains_honestly_open():
    """CLM-1..CLM-12: the committed project matrix passes audit without faking release review."""
    proc = run_tool("claim_evidence.py", "--root", str(REPO_ROOT), "--check")
    report = _report(proc)
    assert proc.returncode == 0, report
    assert report["ok"] is True
    assert report["release_ready"] is False
    assert report["release_blockers"] == ["CLM-12"]

    catalog = yaml.safe_load(
        REPO_ROOT.joinpath("docs/claim-evidence.yaml").read_text(encoding="utf-8")
    )
    rows = {row["id"]: row for row in catalog["claims"]}
    assert "#347" in rows["typed-dataflow-causal"]["evidence"][0]["rationale"]
    assert "#349" in rows["typed-evidence-causal"]["evidence"][0]["rationale"]
    assert "#355" in rows["external-reproduction"]["evidence"][0]["rationale"]
    assert "#356" in rows["hardware-so101-validation"]["evidence"][0]["rationale"]

    audit_record = json.loads(
        REPO_ROOT.joinpath("analysis/claim-evidence/catalog-audit.json").read_text(encoding="utf-8")
    )
    assert audit_record == report
    release = run_tool(
        "claim_evidence.py", "--root", str(REPO_ROOT), "--check", "--require-release-ready"
    )
    release_report = _report(release)
    assert release.returncode != 0
    release_record = json.loads(
        REPO_ROOT.joinpath("analysis/claim-evidence/release-readiness.json").read_text(
            encoding="utf-8"
        )
    )
    assert release_record == release_report


def test_ci_runs_the_claim_catalog_drift_gate():
    """CLM-4, CLM-7: local and hosted CI fail if catalog evidence or output drifts."""
    command = "uv run python tools/claim_evidence.py --check"
    assert command in REPO_ROOT.joinpath("tools/ci.sh").read_text(encoding="utf-8")
    assert command in REPO_ROOT.joinpath(".github/workflows/ci.yml").read_text(encoding="utf-8")
