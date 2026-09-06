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
        "quickstart.py", "--root", str(tmp_path), "--out", str(tmp_path / "qs"), "--skip-sync"
    )
    record = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert record["ok"] is False
    assert record["local_overrides"] == ["--skip-sync"]
    assert record["stages"][0]["name"] == "sync" and record["stages"][0]["ok"] is False
    assert record["mode"] == "development_public"
    assert (tmp_path / "qs" / "quickstart-record.json").exists()


@pytest.fixture
def quickstart_module(monkeypatch):
    import importlib.util

    monkeypatch.syspath_prepend(str(REPO_ROOT / "tools"))

    spec = importlib.util.spec_from_file_location(
        "benchmark_quickstart", REPO_ROOT / "tools/quickstart.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failed_stage", ["validate", "rollout"])
def test_quickstart_refuses_nonzero_exit_even_with_ok_json(
    quickstart_module, tmp_path, capsys, monkeypatch, failed_stage
):
    """BMK-7/CON-8: a failed subprocess cannot be accepted from its JSON alone."""
    calls = []

    def run(cmd, cwd, env=None):
        calls.append(cmd)
        if "validate" in cmd or "rollout" in cmd:
            return (
                (1 if failed_stage in cmd else 0),
                json.dumps({"ok": True, "episodes": [{}]}),
                "deliberate failure",
            )
        if cmd == ["dora", "--version"]:
            return 0, "dora-cli 1.0.1", ""
        if "tools/env_hash.py" in cmd:
            return 0, json.dumps({"ok": True, "env_hash": "a" * 64}), ""
        return 0, "3.13.15", ""

    monkeypatch.setattr(quickstart_module, "_run", run)
    assert quickstart_module.main(["--root", str(tmp_path), "--out", "result"]) == 1
    record = json.loads(capsys.readouterr().out)
    failed = next(stage for stage in record["stages"] if stage["name"] == failed_stage)
    assert failed["ok"] is False
    assert not any(stage["name"] == "bundle" for stage in record["stages"])
    if failed_stage == "validate":
        assert not any("rollout" in cmd for cmd in calls)


@pytest.mark.parametrize("existing_run", ["quickstart-t0-seed0", "another-run"])
def test_quickstart_rejects_existing_run_before_starting(
    quickstart_module, tmp_path, capsys, monkeypatch, existing_run
):
    """BMK-7: retained run inputs must not be overwritten by a refused quickstart."""
    run = tmp_path / "runs" / existing_run
    run.mkdir(parents=True)
    evidence = run / "episodes.jsonl"
    evidence.write_text("retained evidence\n")
    monkeypatch.setattr(
        quickstart_module,
        "_run",
        lambda *args, **kwargs: pytest.fail("must refuse before launching a subprocess"),
    )
    assert quickstart_module.main(["--root", str(tmp_path), "--out", "result"]) == 1
    record = json.loads(capsys.readouterr().out)
    assert record["ok"] is False
    assert record["local_overrides"]
    assert evidence.read_text() == "retained evidence\n"


def test_quickstart_bundle_binds_executed_graph_and_stops_on_invalid_bundle(
    quickstart_module, tmp_path, capsys, monkeypatch
):
    """BMK-7/BMK-13: run-manifest hashes become valid, distinct submission digests."""
    run = tmp_path / "runs/quickstart-t0-seed0"
    authored = "a" * 64
    executed = "b" * 64

    def fake_run(cmd, cwd, env=None):
        if "rollout" in cmd:
            run.mkdir(parents=True)
            (run / "manifest.json").write_text(
                json.dumps({"graph_hash": authored, "exec_graph_hashes": [executed]})
            )
            (run / "episodes.jsonl").write_text(json.dumps({"status": "success"}) + "\n")
            return 0, json.dumps({"ok": True, "episodes": [{"status": "success"}]}), ""
        if "validate" in cmd:
            return 0, '{"ok": true}', ""
        if "tools/env_hash.py" in cmd:
            return 0, json.dumps({"env_hash": "c" * 64}), ""
        return 0, "1.0.1", ""

    monkeypatch.setattr(quickstart_module, "_run", fake_run)
    assert quickstart_module.main(["--root", str(tmp_path), "--out", "result"]) == 1
    record = json.loads(capsys.readouterr().out)
    payload = json.loads((tmp_path / "result/submission.json").read_text())
    assert payload["artifacts"]["authored_hash"] == "sha256:" + authored
    assert payload["artifacts"]["executed_hash"] == "sha256:" + executed
    # Other fixture provenance is deliberately incomplete: no report may be produced.
    assert next(s for s in record["stages"] if s["name"] == "validate_bundle")["ok"] is False
    assert "report" not in record["outputs"]
    assert not (tmp_path / "result/report.json").exists()


@pytest.mark.parametrize("contents", ["", "retained run data"])
def test_quickstart_distinguishes_empty_directory_marker_from_run_input(
    quickstart_module, tmp_path, capsys, monkeypatch, contents
):
    """BMK-7: a fresh clone's empty runs/.gitkeep is not a pre-existing run."""
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / ".gitkeep").write_text(contents)
    calls = []

    def fake_run(cmd, cwd, env=None):
        calls.append(cmd)
        return 1, "", "deliberate sync failure"

    monkeypatch.setattr(quickstart_module, "_run", fake_run)
    assert quickstart_module.main(["--root", str(tmp_path), "--out", "result"]) == 1
    record = json.loads(capsys.readouterr().out)
    if contents:
        assert record["stages"][0]["name"] == "preflight" and not calls
    else:
        assert record["local_overrides"] == []
        assert record["stages"][0]["name"] == "sync" and calls


@pytest.mark.parametrize("measurement_fails", [False, True])
def test_quickstart_retains_memory_measurement_in_final_report(
    quickstart_module, tmp_path, capsys, monkeypatch, measurement_fails
):
    """BMK-8/BMK-17: actual quickstart finalization retains measured memory or an explicit gap."""
    import hashlib

    import process_resources

    import aisle.harness.benchmark_submission as submission

    def observe(_pid):
        if measurement_fails:
            raise OSError("inspection refused")
        return {"rss_bytes": 2048, "processes": 2}

    monkeypatch.setattr(process_resources, "observe_tree", observe)
    monkeypatch.setattr(submission, "validate_submission", lambda *a, **k: [])
    run_dir = tmp_path / "runs/quickstart-t0-seed0"

    def fake_run(cmd, cwd, env=None):
        if "rollout" in cmd:
            run_dir.mkdir(parents=True)
            (run_dir / "manifest.json").write_text(
                json.dumps({"graph_hash": "a" * 64, "exec_graph_hashes": ["b" * 64]})
            )
            (run_dir / "episodes.jsonl").write_text(json.dumps({"status": "success"}) + "\n")
            return 0, json.dumps({"ok": True, "episodes": [{"status": "success"}]}), ""
        if "validate" in cmd:
            return 0, '{"ok": true}', ""
        if "tools/env_hash.py" in cmd:
            return 0, json.dumps({"env_hash": "c" * 64}), ""
        return 0, "1.0.1", ""

    monkeypatch.setattr(quickstart_module, "_run", fake_run)
    assert quickstart_module.main(["--root", str(tmp_path), "--out", "result"]) == 0
    record = json.loads(capsys.readouterr().out)
    report_path = tmp_path / "result/report.json"
    report = json.loads(report_path.read_text())
    memory = record["memory_sampling"]
    assert memory["status"] == ("unmeasured" if measurement_fails else "measured")
    assert report["resources"]["memory_sampling"] == memory
    assert report["resources"]["peak_memory_bytes"] == (None if measurement_fails else 2048)
    report_stage = next(s for s in record["stages"] if s["name"] == "report")
    assert (
        report_stage["sha256"] == "sha256:" + hashlib.sha256(report_path.read_bytes()).hexdigest()
    )
