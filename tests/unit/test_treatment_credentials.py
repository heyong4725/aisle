"""Acceptance tests for SPEC 420 credential lifecycle evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness.treatment_credentials import (
    CredentialScrubError,
    credential_scrub_guard,
    scrub_credentials,
    verify_credential_scrub_record,
)

pytestmark = pytest.mark.unit

_PROJECT_ROOT = Path(__file__).parents[2]
_SENTINEL = "synthetic-refresh-token-TRT11"


def _seed(home: Path) -> tuple[Path, Path]:
    claude = home / ".claude" / ".credentials.json"
    codex = home / ".codex" / "auth.json"
    claude.parent.mkdir(parents=True)
    codex.parent.mkdir(parents=True)
    claude.write_text(json.dumps({"refreshToken": f"claude-{_SENTINEL}"}))
    codex.write_text(json.dumps({"tokens": {"refresh_token": f"codex-{_SENTINEL}"}}))
    return claude, codex


def _reidentify(record: dict) -> None:
    retained = dict(record)
    retained.pop("immutable_id", None)
    raw = json.dumps(
        retained,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    record["immutable_id"] = f"sha256:{hashlib.sha256(raw).hexdigest()}"


def test_scrub_removes_exact_canonical_files_and_retains_secret_free_proof(tmp_path: Path):
    """TRT-11: canonical Claude and Codex credentials are absent after postflight."""
    home = tmp_path / "home"
    claude, codex = _seed(home)
    output = tmp_path / "credential-scrub.json"

    record = scrub_credentials(home, output)

    assert not claude.exists()
    assert not codex.exists()
    assert verify_credential_scrub_record(home, output) == record
    assert record["schema_version"] == "aisle.credential-scrub.v1"
    assert record["complete"] is True
    assert record["credential_bytes_in_record"] is False
    assert record["canonical_locations"] == [
        ".claude/.credentials.json",
        ".codex/auth.json",
    ]
    assert all(event["after"] == "absent" for event in record["events"])
    assert all(event["action"] == "unlinked" for event in record["events"])
    assert _SENTINEL not in output.read_text()


def test_guard_scrubs_and_writes_proof_after_interrupted_session(tmp_path: Path):
    """TRT-11: BaseException interruption still runs credential postflight."""
    home = tmp_path / "home"
    claude, codex = _seed(home)
    output = tmp_path / "interrupted-scrub.json"

    with pytest.raises(KeyboardInterrupt):
        with credential_scrub_guard(home, output, phase="session"):
            raise KeyboardInterrupt

    assert not claude.exists() and not codex.exists()
    record = verify_credential_scrub_record(home, output)
    assert record["phase"] == "session"
    assert record["trigger"] == "exception"


def test_guard_scrubs_after_aborted_launch_before_session_start(tmp_path: Path):
    """TRT-11: a seeded credential cannot survive an aborted launcher path."""
    home = tmp_path / "home"
    claude, codex = _seed(home)
    output = tmp_path / "aborted-launch-scrub.json"

    with pytest.raises(RuntimeError, match="synthetic launch refusal"):
        with credential_scrub_guard(home, output, phase="auth_probe"):
            raise RuntimeError("synthetic launch refusal")

    assert not claude.exists() and not codex.exists()
    assert verify_credential_scrub_record(home, output)["phase"] == "auth_probe"


def test_cli_refresh_rewrite_at_canonical_location_is_scrubbed(tmp_path: Path):
    """TRT-11: refreshed bytes written during a session are removed, not just seeded bytes."""
    home = tmp_path / "home"
    claude, _ = _seed(home)
    output = tmp_path / "refresh-scrub.json"

    with credential_scrub_guard(home, output, phase="session"):
        claude.write_text(json.dumps({"refreshToken": f"rewritten-{_SENTINEL}"}))

    assert not claude.exists()
    record = verify_credential_scrub_record(home, output)
    assert record["trigger"] == "normal_exit"
    assert _SENTINEL not in json.dumps(record)


def test_scrub_preserves_nonsecret_evidence_and_same_named_decoys(tmp_path: Path):
    """TRT-11: credential cleanup never recursively deletes retained evidence."""
    home = tmp_path / "home"
    _seed(home)
    history = home / ".codex" / "history.jsonl"
    history.write_text("retained tool evidence\n")
    decoy = home / "workspace" / "auth.json"
    decoy.parent.mkdir()
    decoy.write_text("retained deliverable evidence\n")

    scrub_credentials(home, tmp_path / "scrub.json")

    assert history.read_text() == "retained tool evidence\n"
    assert decoy.read_text() == "retained deliverable evidence\n"


def test_symlink_is_unlinked_without_touching_external_target(tmp_path: Path):
    """TRT-11: a canonical symlink cannot redirect scrubbing into retained evidence."""
    home = tmp_path / "home"
    external = tmp_path / "external-evidence.json"
    external.write_text("must survive\n")
    credential = home / ".codex" / "auth.json"
    credential.parent.mkdir(parents=True)
    credential.symlink_to(external)

    record = scrub_credentials(home, tmp_path / "scrub.json")

    assert not credential.exists()
    assert external.read_text() == "must survive\n"
    event = next(event for event in record["events"] if event["agent"] == "codex")
    assert event["before"] == "symlink"
    assert event["after"] == "absent"


def test_symlinked_parent_is_refused_without_deleting_external_credential(tmp_path: Path):
    """TRT-11: a parent-directory link cannot redirect deletion outside session HOME."""
    home = tmp_path / "home"
    home.mkdir()
    external = tmp_path / "external-codex"
    external.mkdir()
    external_credential = external / "auth.json"
    external_credential.write_text(f"external-{_SENTINEL}\n")
    (home / ".codex").symlink_to(external)
    output = tmp_path / "parent-symlink-scrub.json"

    with pytest.raises(CredentialScrubError, match="incomplete"):
        scrub_credentials(home, output)

    assert external_credential.read_text() == f"external-{_SENTINEL}\n"
    assert _SENTINEL not in output.read_text()
    record = json.loads(output.read_text())
    event = next(event for event in record["events"] if event["agent"] == "codex")
    assert event["before"] == "unsafe_parent_symlink"
    assert event["action"] == "refused"


def test_scrub_failure_is_loud_and_retains_incomplete_postflight(tmp_path: Path, monkeypatch):
    """TRT-11: an undeletable canonical credential is an auditable infrastructure failure."""
    import aisle.harness.treatment_credentials as credentials

    home = tmp_path / "home"
    claude, codex = _seed(home)
    output = tmp_path / "failed-scrub.json"
    real_unlink = credentials._unlink

    def fail_codex(path: Path) -> None:
        if path == codex:
            raise PermissionError("synthetic unlink refusal")
        real_unlink(path)

    monkeypatch.setattr(credentials, "_unlink", fail_codex)

    with pytest.raises(CredentialScrubError, match="incomplete"):
        scrub_credentials(home, output)

    record = json.loads(output.read_text())
    assert record["complete"] is False
    assert record["credential_bytes_in_record"] is False
    assert record["classification"] == "infrastructure_exclusion"
    assert not claude.exists()
    assert codex.exists()
    assert _SENTINEL not in output.read_text()
    with pytest.raises(CredentialScrubError):
        verify_credential_scrub_record(home, output)


def test_directory_at_canonical_location_is_not_recursively_deleted(tmp_path: Path):
    """TRT-11: unexpected credential-path shape fails without deleting unknown content."""
    home = tmp_path / "home"
    credential = home / ".codex" / "auth.json"
    credential.mkdir(parents=True)
    evidence = credential / "retained.txt"
    evidence.write_text("unknown non-secret content\n")
    output = tmp_path / "directory-scrub.json"

    with pytest.raises(CredentialScrubError, match="incomplete"):
        scrub_credentials(home, output)

    assert evidence.read_text() == "unknown non-secret content\n"
    record = json.loads(output.read_text())
    event = next(event for event in record["events"] if event["agent"] == "codex")
    assert event["before"] == "directory"
    assert event["action"] == "refused"


def test_missing_credentials_produce_verified_idempotent_absence_proof(tmp_path: Path):
    """TRT-11: no-seed and already-scrubbed paths still produce explicit proof."""
    home = tmp_path / "home"
    home.mkdir()
    output = tmp_path / "scrub.json"

    record = scrub_credentials(home, output)

    assert verify_credential_scrub_record(home, output) == record
    assert all(event["before"] == "absent" for event in record["events"])
    assert all(event["action"] == "already_absent" for event in record["events"])


def test_scrub_record_is_non_overwriting_and_tamper_evident(tmp_path: Path):
    """TRT-11: postflight proof cannot be silently replaced or edited."""
    home = tmp_path / "home"
    home.mkdir()
    output = tmp_path / "scrub.json"
    scrub_credentials(home, output)

    with pytest.raises(CredentialScrubError, match="already exists"):
        scrub_credentials(home, output)
    record = json.loads(output.read_text())
    record["phase"] = "tampered"
    output.write_text(json.dumps(record))
    with pytest.raises(CredentialScrubError, match="immutable"):
        verify_credential_scrub_record(home, output)


def test_proof_path_cannot_recreate_a_canonical_credential(tmp_path: Path):
    """TRT-11: a misconfigured audit sink cannot become a new credential-path occupant."""
    home = tmp_path / "home"
    claude, codex = _seed(home)

    with pytest.raises(CredentialScrubError, match="overlaps canonical"):
        scrub_credentials(home, codex)

    assert not claude.exists()
    assert not codex.exists()


def test_reidentified_forged_event_semantics_are_rejected(tmp_path: Path):
    """TRT-11: a valid content id cannot legitimize a false scrub action."""
    home = tmp_path / "home"
    home.mkdir()
    output = tmp_path / "scrub.json"
    scrub_credentials(home, output)
    record = json.loads(output.read_text())
    record["events"][0]["action"] = "unlinked"
    _reidentify(record)
    output.write_text(json.dumps(record))

    with pytest.raises(CredentialScrubError, match="semantics"):
        verify_credential_scrub_record(home, output)


def test_cli_writes_synthetic_environment_bound_capability_audit(tmp_path: Path):
    """TRT-11: interrupted-path evidence is reproducible and explicitly unscored."""
    output = tmp_path / "audit.json"
    command = [
        sys.executable,
        "-m",
        "aisle.harness.treatment_credentials",
        "audit",
        "--output",
        str(output),
    ]

    created = subprocess.run(command, capture_output=True, text=True, check=False)
    repeated = subprocess.run(command, capture_output=True, text=True, check=False)

    assert created.returncode == 0, created.stderr
    report = json.loads(output.read_text())
    assert report["schema_version"] == "aisle.credential-scrub-capability.v1"
    assert report["evidence_class"] == "synthetic_unscored_credential_scrub_capability"
    assert report["confirmatory_ready"] is False
    assert report["capability_pass"] is True
    assert report["checks_passed"] == report["checks_total"]
    assert report["checks_total"] >= 8
    assert report["environment"]["os"]
    assert report["randomization"]["seed"] is None
    assert _SENTINEL not in output.read_text()
    assert repeated.returncode != 0
    assert "already exists" in repeated.stderr


def test_primary_capability_artifact_is_bound_to_the_implementation():
    """TRT-11: retained credential evidence identifies its exact implementation."""
    primary = (
        _PROJECT_ROOT
        / "analysis"
        / "treatment-integrity"
        / "credential-scrub-capability"
        / "audit.json"
    )
    report = json.loads(primary.read_text())
    source = _PROJECT_ROOT / "src" / "aisle" / "harness" / "treatment_credentials.py"

    assert report["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["capability_pass"] is True
    assert report["confirmatory_ready"] is False
