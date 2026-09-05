"""Benchmark v1 participant package: version manifest, release audit,
submission validation, schemas, and quickstart record (BMK-1, BMK-2, BMK-3,
BMK-7, BMK-9, BMK-13, BMK-14, BMK-16, BMK-17, BMK-18, BMK-19, BMK-20,
BMK-22; SPEC 540, issue #357).

Internal evidence can pass a document or tool criterion; the audit must
never pass the external-user, blind-isolation, or public-publication
criteria from it.
"""

from __future__ import annotations

import copy
import json

import pytest
from cli_helpers import REPO_ROOT, run_tool

from aisle.harness.benchmark_submission import validate_submission

pytestmark = pytest.mark.unit

V1 = REPO_ROOT / "docs" / "benchmark" / "v1"


def _bundle() -> dict:
    digest = "sha256:" + "a" * 64
    return {
        "schema_version": "aisle.benchmark.submission.v1",
        "submission_id": "sub-1",
        "benchmark_version": "aisle-benchmark-v1-draft",
        "agent": {
            "provider": "p",
            "model_id": "m",
            "requested_parameters": {},
            "client_version": "1",
            "access_date": "2026-09-05",
            "nondeterminism": "none",
        },
        "contract_hashes": {
            "participant_contract": digest,
            "prompt": digest,
            "tool_contract": digest,
        },
        "treatment": "typed",
        "artifacts": {"authored_hash": digest, "executed_hash": digest},
        "environment": {"lock_hash": digest, "env_hash": digest, "platform": "macOS"},
        "sessions": [
            {
                "session_id": "s-1",
                "attempt_id": "1",
                "treatment": "typed",
                "provenance": {"git_sha": "abc"},
                "budget": {"tokens": 1000},
                "outcome": {"session_success": True},
                "exclusion": None,
            }
        ],
        "resources": {
            "tokens": 1000,
            "cached_tokens": None,
            "wall_seconds": 10.0,
            "tool_calls": 3,
            "api_cost": None,
            "retries": 0,
            "parallel_agents": 1,
        },
        "evidence": {"commands": "a", "receipts": "b", "interventions": "c", "outcomes": "d"},
        "transcript": {"kind": "full", "path": "t.jsonl"},
        "attestation": {"signed_by": "ctl", "signature": "sig", "integrity_controller": "ctl"},
        "declared_score": None,
    }


def test_valid_bundle_passes_and_every_defect_is_reported(tmp_path):
    """BMK-13 / BMK-14: a complete bundle validates; missing and unknown
    fields, version drift, digest format, treatment mismatch, incomplete
    denominators, budget overrun, unregistered exclusion, unattested
    execution, a leaked private marker, and a participant score are each a
    reported reason, and the reasons are deterministic."""
    assert validate_submission(_bundle(), root=REPO_ROOT) == []
    bad = copy.deepcopy(_bundle())
    del bad["evidence"]["outcomes"]
    bad["extra"] = 1
    bad["benchmark_version"] = "aisle-benchmark-v9"
    bad["artifacts"]["authored_hash"] = "md5:nope"
    bad["sessions"][0]["treatment"] = "monolithic"
    bad["sessions"][0]["outcome"] = {}
    bad["sessions"][0]["exclusion"] = {"kind": "operator_choice"}
    bad["sessions"][0]["provenance"] = {}
    bad["resources"]["tokens"] = 10**9
    bad["transcript"]["path"] = "~/aisle-private/x"
    bad["declared_score"] = 0.9
    problems = validate_submission(bad, root=REPO_ROOT)
    expected_fragments = [
        "evidence.outcomes: missing",
        "bundle.extra: unknown field",
        "version drift",
        "digest format invalid",
        "parity mismatch",
        "incomplete denominator",
        "unregistered exclusion",
        "unattested execution",
        "budget overrun",
        "leaked private marker",
        "participant-supplied score",
    ]
    for fragment in expected_fragments:
        assert any(fragment in p for p in problems), (fragment, problems)
    assert problems == sorted(set(problems))
    assert validate_submission(bad, root=REPO_ROOT) == problems


def test_schemas_carry_registered_units_and_no_ranking_scalar():
    """BMK-16 / BMK-13: the report schema requires the registered unit,
    denominators, effects with uncertainty, exclusions, integrity, safety,
    resources, and claim status, and forbids ranking; the submission schema
    forbids a participant score."""
    report = json.loads((V1 / "leaderboard.schema.json").read_text())
    assert report["properties"]["experimental_unit"]["enum"] == ["agent_session"]
    assert report["properties"]["ranking"]["const"] == "none"
    for key in (
        "sample",
        "success",
        "effect",
        "exclusions",
        "integrity",
        "safety",
        "resources",
        "claim_status",
    ):
        assert key in report["required"]
    submission = json.loads((V1 / "submission.schema.json").read_text())
    assert submission["properties"]["declared_score"]["const"] is None
    assert submission["additionalProperties"] is False


def test_version_manifest_and_release_audit_are_current_and_honest():
    """BMK-1 / BMK-22 / BMK-20: the committed manifest binds every listed
    surface by hash and checks current; the audit never marks BMK-8,
    BMK-11, or BMK-21 passed, marks BMK-20 failed while no license exists,
    and reports release_ready false."""
    proc = run_tool("benchmark_release.py", "--root", str(REPO_ROOT), "--check")
    report = json.loads(proc.stdout)
    assert proc.returncode == 0, report
    assert report["ok"] is True and report["reason"] == "current"
    manifest = json.loads((V1 / "version-manifest.json").read_text())
    assert manifest["missing_surfaces"] == []
    assert all(
        v["sha256"] for k, v in manifest["surfaces"].items() if k != "hidden_bank_commitment"
    )
    audit = json.loads((V1 / "release-audit.json").read_text())
    status = {row["criterion"]: row["status"] for row in audit["criteria"]}
    assert len(status) == 22
    assert status["BMK-8"] == "external_pending"
    assert status["BMK-11"] == "external_pending"
    assert status["BMK-21"] == "external_pending"
    assert status["BMK-20"] == "failed"
    assert status["BMK-4"] == "dependency_pending"
    assert audit["release_ready"] is False


def test_contract_distributions_and_policies_state_the_required_items():
    """BMK-2 / BMK-3 / BMK-9 / BMK-17 / BMK-18 / BMK-19: the documents
    enumerate what the spec demands and keep development_public distinct."""
    contract = (V1 / "participant-contract.md").read_text()
    for item in (
        "Forbidden",
        "Budgets",
        "Refusal behaviour",
        "tokens",
        "wall time",
        "Human assistance",
        "network",
        "Persistence",
    ):
        assert item.lower() in contract.lower(), item
    dist = json.loads((V1 / "task-distributions.json").read_text())
    assert set(dist["instances"]) == {
        "development_public",
        "qualification_public",
        "evaluation_private",
    }
    assert dist["instances"]["evaluation_private"]["seeds"] == "withheld"
    assert set(dist["instances"]["development_public"]["seeds"]).isdisjoint(
        dist["instances"]["qualification_public"]["seeds"]
    )
    for fam in dist["families"].values():
        for key in ("perception_rung", "generator", "scorer_visible_truth", "sampling_weights"):
            assert key in fam
    accounting = (V1 / "resource-accounting.md").read_text().lower()
    for item in ("cached", "pric", "retr", "parallel", "amortiz", "composite"):
        assert item in accounting, item
    governance = (V1 / "governance.md").read_text().lower()
    for item in (
        "maintainers",
        "compatibility",
        "migration",
        "leak",
        "appeals",
        "withdraw",
        "errata",
    ):
        assert item in governance, item
    rotation = (V1 / "contamination-rotation.md").read_text().lower()
    for item in ("release date", "cutoff", "disclos", "quarantin", "rotation", "comparab"):
        assert item in rotation, item


def test_quickstart_records_a_local_override_as_failure(tmp_path):
    """BMK-7: the quickstart's CON-8 record exists on every path and a
    local override (skipped sync) makes it ok:false rather than silently
    passing; no simulator is needed for this refusal path."""
    proc = run_tool(
        "quickstart.py", "--root", str(REPO_ROOT), "--out", str(tmp_path / "qs"), "--skip-sync"
    )
    record = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert record["ok"] is False
    assert record["local_overrides"] == ["--skip-sync"]
    assert record["stages"][0]["name"] == "sync" and record["stages"][0]["ok"] is False
    assert record["mode"] == "development_public"
    assert (tmp_path / "qs" / "quickstart-record.json").exists()
