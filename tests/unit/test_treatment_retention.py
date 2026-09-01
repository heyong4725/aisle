"""Acceptance tests for SPEC 420 treatment-evidence retention."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness.treatment_retention import (
    RetentionError,
    RetentionInputs,
    require_retention_for_estimate,
    retain_evidence,
    verify_retention_archive,
)

pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).parents[2]


def _write_sources(root: Path, *, classification: str = "agent_outcome") -> RetentionInputs:
    root.mkdir()
    files = {
        "stdout": "raw stdout\n",
        "stderr": "raw stderr\n",
        "tool_audit_log": '{"decision":"allow","tool":"pytest"}\n',
        "idea_ledger": '{"id":"I1","status":"closed"}\n',
        "preflight_manifest": '{"immutable_id":"sha256:preflight"}\n',
        "postflight_manifest": '{"immutable_id":"sha256:postflight"}\n',
        "budget_samples": '{"tokens":17,"wall_s":1.0}\n',
        "randomization_record": '{"assignment_id":"A-001","block":"B-01"}\n',
        "exclusion_reason": json.dumps(
            {
                "classification": classification,
                "reasons": [] if classification == "agent_outcome" else [classification],
            }
        )
        + "\n",
        "tool_policy": '{"allowed":["pytest"],"denied":["network"]}\n',
    }
    paths: dict[str, Path] = {}
    for name, text in files.items():
        path = root / f"{name}.jsonl"
        path.write_text(text)
        paths[name] = path
    deliverable = root / "deliverable"
    (deliverable / "graphs").mkdir(parents=True)
    (deliverable / "graphs" / "agent.yaml").write_bytes(b"nodes: []\n")
    (deliverable / "README.md").write_text("candidate\n")
    return RetentionInputs(deliverable_tree=deliverable, **paths)


def _reidentify_manifest(manifest: dict) -> None:
    retained = dict(manifest)
    retained.pop("immutable_id", None)
    raw = json.dumps(
        retained,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    manifest["immutable_id"] = f"sha256:{hashlib.sha256(raw).hexdigest()}"


def test_started_session_archive_is_complete_content_addressed_and_byte_exact(tmp_path: Path):
    """TRT-10: one started session retains every required raw evidence class."""
    inputs = _write_sources(tmp_path / "source")
    archive = tmp_path / "archive"

    created = retain_evidence(
        inputs,
        archive,
        assignment_id="A-001",
        session_id="S-001",
        lifecycle="started",
    )
    verified = verify_retention_archive(archive)

    assert created == verified
    assert verified["schema_version"] == "aisle.treatment-retention.v1"
    assert verified["retention_complete"] is True
    assert verified["assignment_id"] == "A-001"
    assert verified["session_id"] == "S-001"
    assert verified["lifecycle"] == "started"
    assert verified["immutable_id"].startswith("sha256:")
    assert set(verified["artifacts"]) == {
        "budget_samples",
        "deliverable_tree",
        "exclusion_reason",
        "idea_ledger",
        "postflight_manifest",
        "preflight_manifest",
        "randomization_record",
        "stderr",
        "stdout",
        "tool_audit_log",
        "tool_policy",
    }
    assert (archive / "artifacts" / "stdout").read_bytes() == inputs.stdout.read_bytes()
    assert (archive / "artifacts" / "deliverable_tree" / "README.md").read_text() == ("candidate\n")


@pytest.mark.parametrize(
    "missing",
    [
        "stdout",
        "stderr",
        "tool_audit_log",
        "deliverable_tree",
        "idea_ledger",
        "preflight_manifest",
        "postflight_manifest",
        "budget_samples",
        "randomization_record",
        "exclusion_reason",
        "tool_policy",
    ],
)
def test_missing_source_fails_before_any_archive_is_published(tmp_path: Path, missing: str):
    """TRT-10: an incomplete source set fails loudly before cleanup can proceed."""
    inputs = _write_sources(tmp_path / "source")
    target = getattr(inputs, missing)
    if target.is_dir():
        target.rmdir() if not any(target.iterdir()) else None
        if target.exists():
            for child in sorted(target.rglob("*"), reverse=True):
                child.unlink() if child.is_file() else child.rmdir()
            target.rmdir()
    else:
        target.unlink()
    archive = tmp_path / "archive"

    with pytest.raises(RetentionError, match=missing):
        retain_evidence(
            inputs,
            archive,
            assignment_id="A-001",
            session_id="S-001",
            lifecycle="started",
        )

    assert not archive.exists()


def test_symlink_source_is_refused_without_copying_hidden_bytes(tmp_path: Path):
    """TRT-10: archives fail closed on indirect evidence paths."""
    inputs = _write_sources(tmp_path / "source")
    hidden = tmp_path / "hidden.txt"
    hidden.write_text("synthetic-hidden-sentinel\n")
    inputs.stdout.unlink()
    inputs.stdout.symlink_to(hidden)

    with pytest.raises(RetentionError, match="symlink"):
        retain_evidence(
            inputs,
            tmp_path / "archive",
            assignment_id="A-001",
            session_id="S-001",
            lifecycle="started",
        )

    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize("mutation", ["change", "remove", "extra", "manifest"])
def test_verification_detects_archive_corruption(tmp_path: Path, mutation: str):
    """TRT-10: missing, changed, or unmanifested retained bytes block estimation."""
    inputs = _write_sources(tmp_path / "source")
    archive = tmp_path / "archive"
    retain_evidence(
        inputs,
        archive,
        assignment_id="A-001",
        session_id="S-001",
        lifecycle="started",
    )
    if mutation == "change":
        (archive / "artifacts" / "stdout").write_text("changed\n")
    elif mutation == "remove":
        (archive / "artifacts" / "stderr").unlink()
    elif mutation == "extra":
        (archive / "artifacts" / "unmanifested.txt").write_text("extra\n")
    else:
        manifest = json.loads((archive / "retention.json").read_text())
        manifest["session_id"] = "S-tampered"
        (archive / "retention.json").write_text(json.dumps(manifest))

    with pytest.raises(RetentionError):
        require_retention_for_estimate(archive)


def test_malformed_manifest_entries_fail_as_retention_errors(tmp_path: Path):
    """TRT-10: attacker-shaped metadata cannot bypass or crash the estimate gate."""
    inputs = _write_sources(tmp_path / "source")
    archive = tmp_path / "archive"
    retain_evidence(
        inputs,
        archive,
        assignment_id="A-001",
        session_id="S-001",
        lifecycle="started",
    )
    manifest_path = archive / "retention.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"]["stdout"]["entries"] = [1]
    _reidentify_manifest(manifest)
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(RetentionError, match="entry is invalid"):
        verify_retention_archive(archive)


def test_existing_archive_is_never_overwritten(tmp_path: Path):
    """TRT-10: retrying retention cannot silently replace raw observations."""
    inputs = _write_sources(tmp_path / "source")
    archive = tmp_path / "archive"
    retain_evidence(
        inputs,
        archive,
        assignment_id="A-001",
        session_id="S-001",
        lifecycle="started",
    )

    with pytest.raises(RetentionError, match="already exists"):
        retain_evidence(
            inputs,
            archive,
            assignment_id="A-001",
            session_id="S-001",
            lifecycle="started",
        )


@pytest.mark.parametrize(
    ("lifecycle", "session_id", "classification", "message"),
    [
        ("started", None, "agent_outcome", "session_id"),
        ("assigned_not_started", "S-001", "launch_refused", "session_id"),
        ("assigned_not_started", None, "agent_outcome", "exclusion"),
        ("unknown", None, "launch_refused", "lifecycle"),
    ],
)
def test_lifecycle_and_exclusion_identity_fail_closed(
    tmp_path: Path,
    lifecycle: str,
    session_id: str | None,
    classification: str,
    message: str,
):
    """TRT-10: assignments and starts have unambiguous retained lifecycle state."""
    inputs = _write_sources(tmp_path / "source", classification=classification)

    with pytest.raises(RetentionError, match=message):
        retain_evidence(
            inputs,
            tmp_path / "archive",
            assignment_id="A-001",
            session_id=session_id,
            lifecycle=lifecycle,
        )


def test_estimate_gate_accepts_only_complete_started_agent_outcome(tmp_path: Path):
    """TRT-10: retention is mechanically required before an outcome enters an estimate."""
    inputs = _write_sources(tmp_path / "source")
    archive = tmp_path / "archive"
    retain_evidence(
        inputs,
        archive,
        assignment_id="A-001",
        session_id="S-001",
        lifecycle="started",
    )

    assert require_retention_for_estimate(archive)["retention_complete"] is True


@pytest.mark.parametrize("classification", ["infrastructure_exclusion", "synthetic_unscored"])
def test_estimate_gate_rejects_retained_non_outcomes(tmp_path: Path, classification: str):
    """TRT-10: explicit exclusion state is retained and never analyzed as an outcome."""
    inputs = _write_sources(tmp_path / "source", classification=classification)
    archive = tmp_path / "archive"
    retain_evidence(
        inputs,
        archive,
        assignment_id="A-001",
        session_id="S-001",
        lifecycle="started",
    )

    with pytest.raises(RetentionError, match="classification"):
        require_retention_for_estimate(archive)


def test_randomized_unstarted_assignment_is_retained_but_excluded(tmp_path: Path):
    """TRT-10: a randomized launch refusal remains auditable rather than disappearing."""
    inputs = _write_sources(tmp_path / "source", classification="launch_refused")
    archive = tmp_path / "archive"
    record = retain_evidence(
        inputs,
        archive,
        assignment_id="A-001",
        session_id=None,
        lifecycle="assigned_not_started",
    )

    assert verify_retention_archive(archive) == record
    with pytest.raises(RetentionError, match="not a started session"):
        require_retention_for_estimate(archive)


def test_cli_writes_a_synthetic_non_overwriting_capability_audit(tmp_path: Path):
    """TRT-10: the retention gate has reproducible machine-readable audit evidence."""
    output = tmp_path / "audit.json"
    cmd = [
        sys.executable,
        "-m",
        "aisle.harness.treatment_retention",
        "audit",
        "--output",
        str(output),
    ]

    created = subprocess.run(cmd, capture_output=True, text=True, check=False)
    repeated = subprocess.run(cmd, capture_output=True, text=True, check=False)

    assert created.returncode == 0, created.stderr
    report = json.loads(output.read_text())
    assert report["schema_version"] == "aisle.retention-capability.v1"
    assert report["evidence_class"] == "synthetic_unscored_retention_capability"
    assert report["confirmatory_ready"] is False
    assert report["capability_pass"] is True
    assert report["checks_passed"] == report["checks_total"]
    assert report["checks_total"] >= 8
    assert len(report["configuration"]["required_artifacts"]) == 11
    assert report["environment"]["os"]
    assert report["randomization"] == {
        "seed": None,
        "status": "not_applicable_synthetic_capability",
    }
    assert report["session_id"].startswith("retention-capability-")
    assert repeated.returncode != 0
    assert "already exists" in repeated.stderr


def test_primary_capability_artifact_is_bound_to_the_retained_implementation():
    """TRT-10: the primary machine-readable audit identifies its exact implementation."""
    primary = (
        _PROJECT_ROOT
        / "analysis"
        / "treatment-integrity"
        / "retention-capability"
        / "audit-schema-hardened.json"
    )
    report = json.loads(primary.read_text())
    source = _PROJECT_ROOT / "src" / "aisle" / "harness" / "treatment_retention.py"

    assert report["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["capability_pass"] is True
    assert report["confirmatory_ready"] is False


def test_atomic_publication_leaves_no_staging_directory(tmp_path: Path):
    """TRT-10: cleanup authorization follows one atomically published archive."""
    inputs = _write_sources(tmp_path / "source")
    archive = tmp_path / "archive"
    retain_evidence(
        inputs,
        archive,
        assignment_id="A-001",
        session_id="S-001",
        lifecycle="started",
    )

    assert archive.exists()
    assert not [entry for entry in tmp_path.iterdir() if entry.name.startswith(".retention-")]
    assert os.stat(archive / "retention.json").st_size > 0
