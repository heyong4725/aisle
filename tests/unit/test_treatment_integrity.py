"""Tests for the SPEC 420 treatment-manifest preflight."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness.treatment_integrity import (
    ManifestError,
    create_treatment_manifest,
    verify_treatment_manifest,
    write_treatment_manifest,
)

pytestmark = pytest.mark.unit


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _candidate(root: Path) -> dict:
    (root / "AGENTS.md").write_text("agent contract\n")
    (root / "src").mkdir()
    (root / "src" / "worker.py").write_text("print('worker')\n")
    return {
        "schema_version": "aisle.treatment.v1",
        "repository": {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "visible_allowlist": ["AGENTS.md", "src/worker.py"],
        },
        "model": {
            "requested_identity": "codex-frontier",
            "served_identity": "codex-frontier-2026-08-31",
        },
        "agent": {
            "kind": "codex",
            "cli_revision": "codex-cli-1.2.3",
            "cli_binary_sha256": _sha("codex binary"),
        },
        "sampling": {"temperature": 0, "seed": 1729},
        "prompts": {
            "system_sha256": _sha("system prompt"),
            "research_contract_sha256": _sha("research contract"),
        },
        "runtime_binaries": [
            {"name": "dora", "sha256": _sha("dora binary")},
            {"name": "vendor-api", "sha256": _sha("api client")},
        ],
        "environment": {
            "fingerprint_sha256": _sha("environment"),
            "simulator_backend": "genesis-cpu-0.3.3",
            "platform": "macos-arm64-15.6.1",
        },
        "policy": {
            "approval": "never",
            "tool_policy_sha256": _sha("tool policy"),
            "network": "vendor-api-only",
            "allowed_external_tools": ["git", "uv"],
        },
        "state": {
            "credential_source_class": "isolated-session-keychain",
            "credential_provenance": "campaign auth probe; values not retained",
            "credential_policy_sha256": _sha("credential policy"),
            "home_baseline_sha256": _sha("home"),
            "config_baseline_sha256": _sha("config"),
            "cache_baseline_sha256": _sha("cache"),
            "environment_baseline_sha256": _sha("environment variables"),
        },
        "prior_context": {
            "findings_sha256": _sha("no prior findings"),
            "skills_sha256": _sha("frozen skills"),
            "context_sha256": _sha("empty prior context"),
        },
        "budget": {"unit": "provider_reported_tokens", "ceiling": 200_000},
        "assignment": {
            "temporal_block": "block-001",
            "arm": "typed",
            "randomization_seed_commitment": _sha("concealed seed"),
        },
        "host_load": {
            "sampling_rule_sha256": _sha("load average before spawn"),
            "baseline": {"load_1m": 1.25, "logical_cpus": 10},
        },
        "confinement": {
            "adapter_binary_sha256": _sha("adapter"),
            "profile_sha256": _sha("profile"),
            "policy_sha256": _sha("confinement policy"),
        },
    }


def _drop(candidate: dict, dotted_path: str) -> None:
    parts = dotted_path.split(".")
    target = candidate
    for part in parts[:-1]:
        target = target[part]
    del target[parts[-1]]


def test_complete_manifest_is_machine_readable_content_addressed_and_deterministic(
    tmp_path: Path,
):
    """TRT-1: one complete preflight manifest has an immutable identity."""
    candidate = _candidate(tmp_path)

    first = create_treatment_manifest(candidate, tmp_path)
    second = create_treatment_manifest(copy.deepcopy(candidate), tmp_path)

    assert first == second
    assert first["immutable_id"].startswith("sha256:")
    assert [row["path"] for row in first["repository"]["visible_files"]] == [
        "AGENTS.md",
        "src/worker.py",
    ]
    assert first["repository"]["visible_files"][0]["sha256"] == _sha("agent contract\n")
    assert verify_treatment_manifest(first, tmp_path) == first
    json.dumps(first)


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "repository.commit",
        "repository.tree",
        "repository.visible_allowlist",
        "model.requested_identity",
        "model.served_identity",
        "agent.kind",
        "agent.cli_revision",
        "agent.cli_binary_sha256",
        "sampling",
        "prompts.system_sha256",
        "prompts.research_contract_sha256",
        "runtime_binaries",
        "environment.fingerprint_sha256",
        "environment.simulator_backend",
        "environment.platform",
        "policy.approval",
        "policy.tool_policy_sha256",
        "policy.network",
        "policy.allowed_external_tools",
        "state.credential_source_class",
        "state.credential_provenance",
        "state.credential_policy_sha256",
        "state.home_baseline_sha256",
        "state.config_baseline_sha256",
        "state.cache_baseline_sha256",
        "state.environment_baseline_sha256",
        "prior_context.findings_sha256",
        "prior_context.skills_sha256",
        "prior_context.context_sha256",
        "budget.unit",
        "budget.ceiling",
        "assignment.temporal_block",
        "assignment.arm",
        "assignment.randomization_seed_commitment",
        "host_load.sampling_rule_sha256",
        "host_load.baseline",
        "confinement.adapter_binary_sha256",
        "confinement.profile_sha256",
        "confinement.policy_sha256",
    ],
)
def test_every_treatment_component_is_required(tmp_path: Path, field: str):
    """TRT-2: absent treatment identity fails closed before launch."""
    candidate = _candidate(tmp_path)
    _drop(candidate, field)

    with pytest.raises(ManifestError, match="required"):
        create_treatment_manifest(candidate, tmp_path)


@pytest.mark.parametrize("value", ["", "unknown", "TBD", "placeholder", "latest"])
def test_ambiguous_identity_values_are_refused(tmp_path: Path, value: str):
    """TRT-2: placeholder and ambiguous identities cannot enter a manifest."""
    candidate = _candidate(tmp_path)
    candidate["model"]["served_identity"] = value

    with pytest.raises(ManifestError, match="model.served_identity"):
        create_treatment_manifest(candidate, tmp_path)


def test_versions_cannot_substitute_for_required_content_hashes(tmp_path: Path):
    """TRT-2: critical binary/content identities are exact SHA-256 values."""
    candidate = _candidate(tmp_path)
    candidate["agent"]["cli_binary_sha256"] = "codex-cli-1.2.3"

    with pytest.raises(ManifestError, match="agent.cli_binary_sha256"):
        create_treatment_manifest(candidate, tmp_path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda c: c["repository"].update(commit="f" * 39), "repository.commit"),
        (lambda c: c["budget"].update(ceiling=0), "budget.ceiling"),
        (lambda c: c["sampling"].update(temperature=float("nan")), "sampling"),
        (lambda c: c["runtime_binaries"].append(c["runtime_binaries"][0]), "duplicate"),
        (lambda c: c["policy"].update(allowed_external_tools=["git", "git"]), "duplicate"),
    ],
)
def test_internally_inconsistent_components_fail_closed(tmp_path: Path, mutation, message: str):
    """TRT-2: internally inconsistent treatment fields refuse preflight."""
    candidate = _candidate(tmp_path)
    mutation(candidate)

    with pytest.raises(ManifestError, match=message):
        create_treatment_manifest(candidate, tmp_path)


def test_secret_material_is_rejected_but_redacted_provenance_is_retained(tmp_path: Path):
    """TRT-2: manifests retain credential provenance, never credential bytes."""
    candidate = _candidate(tmp_path)
    candidate["state"]["api_token"] = "sk-example-secret-material"

    with pytest.raises(ManifestError, match="secret-bearing key"):
        create_treatment_manifest(candidate, tmp_path)

    del candidate["state"]["api_token"]
    manifest = create_treatment_manifest(candidate, tmp_path)
    assert manifest["state"]["credential_provenance"].endswith("values not retained")
    assert "sk-example" not in json.dumps(manifest)


@pytest.mark.parametrize(
    "allowlist",
    [
        ["../hidden.json"],
        ["/private/hidden.json"],
        ["AGENTS.md", "AGENTS.md"],
        ["missing.txt"],
    ],
)
def test_visible_file_inventory_refuses_unsafe_or_unresolved_paths(
    tmp_path: Path, allowlist: list[str]
):
    """TRT-1: the emitted visible-file identity is complete and unambiguous."""
    candidate = _candidate(tmp_path)
    candidate["repository"]["visible_allowlist"] = allowlist

    with pytest.raises(ManifestError, match="visible_allowlist"):
        create_treatment_manifest(candidate, tmp_path)


def test_manifest_verification_detects_visible_file_and_payload_drift(tmp_path: Path):
    """TRT-1: immutable identity and visible content are independently verifiable."""
    manifest = create_treatment_manifest(_candidate(tmp_path), tmp_path)
    (tmp_path / "AGENTS.md").write_text("drifted\n")

    with pytest.raises(ManifestError, match="visible file drift"):
        verify_treatment_manifest(manifest, tmp_path)

    (tmp_path / "AGENTS.md").write_text("agent contract\n")
    manifest["budget"]["ceiling"] += 1
    with pytest.raises(ManifestError, match="immutable_id"):
        verify_treatment_manifest(manifest, tmp_path)


def test_manifest_writer_refuses_to_overwrite_an_existing_record(tmp_path: Path):
    """TRT-1: preflight emits exactly one immutable manifest per output path."""
    manifest_path = tmp_path / "evidence" / "preflight.json"
    candidate = _candidate(tmp_path)

    written = write_treatment_manifest(candidate, tmp_path, manifest_path)
    assert json.loads(manifest_path.read_text()) == written

    with pytest.raises(ManifestError, match="already exists"):
        write_treatment_manifest(candidate, tmp_path, manifest_path)


def test_controller_cli_creates_then_verifies_the_retained_manifest(tmp_path: Path):
    """TRT-1: the controller has a machine-readable preflight/verification surface."""
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "evidence" / "preflight.json"
    candidate_path.write_text(json.dumps(_candidate(tmp_path)))

    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.treatment_integrity",
            "create",
            "--candidate",
            str(candidate_path),
            "--root",
            str(tmp_path),
            "--output",
            str(manifest_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    created_report = json.loads(created.stdout)
    assert created_report == {
        "immutable_id": json.loads(manifest_path.read_text())["immutable_id"],
        "ok": True,
        "output": str(manifest_path),
    }

    verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.treatment_integrity",
            "verify",
            "--manifest",
            str(manifest_path),
            "--root",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["immutable_id"] == created_report["immutable_id"]


def test_controller_cli_reports_a_json_refusal_without_writing_output(tmp_path: Path):
    """TRT-2: unresolved preflight input fails closed through the campaign CLI."""
    candidate = _candidate(tmp_path)
    candidate["model"]["served_identity"] = "unknown"
    candidate_path = tmp_path / "candidate.json"
    manifest_path = tmp_path / "preflight.json"
    candidate_path.write_text(json.dumps(candidate))

    refused = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.treatment_integrity",
            "create",
            "--candidate",
            str(candidate_path),
            "--root",
            str(tmp_path),
            "--output",
            str(manifest_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert refused.returncode == 2
    assert json.loads(refused.stderr) == {
        "error": "required treatment field is ambiguous: model.served_identity",
        "ok": False,
    }
    assert not manifest_path.exists()
