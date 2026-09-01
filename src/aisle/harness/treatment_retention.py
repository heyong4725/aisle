"""Fail-closed evidence retention for SPEC 420 TRT-10.

This module publishes a complete assignment/session archive only after every
required source has been copied, content-addressed, and independently verified.
The published archive is the cleanup barrier: callers must not remove a source
worktree until :func:`verify_retention_archive` succeeds.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "aisle.treatment-retention.v1"
AUDIT_SCHEMA_VERSION = "aisle.retention-capability.v1"
EVIDENCE_CLASS = "synthetic_unscored_retention_capability"
_LIFECYCLES = frozenset({"assigned_not_started", "started"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class RetentionError(RuntimeError):
    """Evidence cannot be retained or verified without ambiguity."""


@dataclass(frozen=True)
class RetentionInputs:
    """Exact sources required for every randomized assignment."""

    stdout: Path
    stderr: Path
    tool_audit_log: Path
    deliverable_tree: Path
    idea_ledger: Path
    preflight_manifest: Path
    postflight_manifest: Path
    budget_samples: Path
    randomization_record: Path
    exclusion_reason: Path
    tool_policy: Path


_REQUIRED_ARTIFACTS = frozenset(field.name for field in dataclasses.fields(RetentionInputs))


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _content_id(value: dict[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _validate_identifier(value: str | None, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise RetentionError(f"{label} is absent or invalid")
    return value


def _read_exclusion_record(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RetentionError("exclusion_reason is unreadable or invalid JSON") from exc
    if not isinstance(value, dict) or set(value) != {"classification", "reasons"}:
        raise RetentionError("exclusion_reason must contain only classification and reasons")
    classification = value.get("classification")
    reasons = value.get("reasons")
    if not isinstance(classification, str) or not classification.strip():
        raise RetentionError("exclusion_reason classification is absent")
    if not isinstance(reasons, list) or any(
        not isinstance(reason, str) or not reason.strip() for reason in reasons
    ):
        raise RetentionError("exclusion_reason reasons must be a string list")
    if classification == "agent_outcome" and reasons:
        raise RetentionError("agent_outcome exclusion reasons must be empty")
    if classification != "agent_outcome" and not reasons:
        raise RetentionError("excluded classification requires an exclusion reason")
    return value


def _source_kind(path: Path, artifact: str) -> str:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise RetentionError(f"{artifact} source is missing or unreadable") from exc
    if stat.S_ISLNK(mode):
        raise RetentionError(f"{artifact} source is a symlink")
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    raise RetentionError(f"{artifact} source is not a regular file or directory")


def _safe_entries(root: Path, archive_prefix: Path, artifact: str) -> list[dict[str, Any]]:
    """Describe a source without following links or accepting special files."""
    kind = _source_kind(root, artifact)
    if kind == "file":
        raw = root.read_bytes()
        return [
            {
                "kind": "file",
                "path": archive_prefix.as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
            }
        ]

    entries: list[dict[str, Any]] = [{"kind": "directory", "path": archive_prefix.as_posix()}]
    for parent, directory_names, file_names in os.walk(root, followlinks=False):
        parent_path = Path(parent)
        for name in sorted(directory_names):
            source = parent_path / name
            if _source_kind(source, artifact) != "directory":
                raise RetentionError(f"{artifact} contains a symlink or special directory")
            relative = source.relative_to(root)
            entries.append({"kind": "directory", "path": (archive_prefix / relative).as_posix()})
        for name in sorted(file_names):
            source = parent_path / name
            if _source_kind(source, artifact) != "file":
                raise RetentionError(f"{artifact} contains a symlink or special file")
            raw = source.read_bytes()
            relative = source.relative_to(root)
            entries.append(
                {
                    "kind": "file",
                    "path": (archive_prefix / relative).as_posix(),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size_bytes": len(raw),
                }
            )
    return sorted(entries, key=lambda entry: (entry["path"], entry["kind"]))


def _copy_source(source: Path, destination: Path, artifact: str) -> None:
    kind = _source_kind(source, artifact)
    if kind == "file":
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        return
    destination.mkdir(parents=True)
    for parent, directory_names, file_names in os.walk(source, followlinks=False):
        parent_path = Path(parent)
        relative = parent_path.relative_to(source)
        for name in sorted(directory_names):
            child = parent_path / name
            if _source_kind(child, artifact) != "directory":
                raise RetentionError(f"{artifact} contains a symlink or special directory")
            (destination / relative / name).mkdir()
        for name in sorted(file_names):
            child = parent_path / name
            if _source_kind(child, artifact) != "file":
                raise RetentionError(f"{artifact} contains a symlink or special file")
            (destination / relative / name).write_bytes(child.read_bytes())


def _manifest_for(
    inputs: RetentionInputs,
    assignment_id: str,
    session_id: str | None,
    lifecycle: str,
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for field in dataclasses.fields(inputs):
        name = field.name
        source = Path(getattr(inputs, name))
        prefix = Path("artifacts") / name
        artifacts[name] = {
            "source_kind": _source_kind(source, name),
            "entries": _safe_entries(source, prefix, name),
        }
    manifest: dict[str, Any] = {
        "artifacts": artifacts,
        "assignment_id": assignment_id,
        "lifecycle": lifecycle,
        "retention_complete": True,
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
    }
    manifest["immutable_id"] = _content_id(manifest)
    return manifest


def retain_evidence(
    inputs: RetentionInputs,
    archive: Path,
    *,
    assignment_id: str,
    session_id: str | None,
    lifecycle: str,
) -> dict[str, Any]:
    """Copy, verify, and atomically publish one complete retention archive."""
    archive = Path(archive)
    if archive.exists():
        raise RetentionError(f"retention archive already exists: {archive}")
    assignment_id = _validate_identifier(assignment_id, "assignment_id")
    if lifecycle not in _LIFECYCLES:
        raise RetentionError(f"lifecycle is invalid: {lifecycle!r}")
    if lifecycle == "started":
        session_id = _validate_identifier(session_id, "session_id")
    elif session_id is not None:
        raise RetentionError("session_id must be absent when assignment was not started")

    paths = {field.name: Path(getattr(inputs, field.name)) for field in dataclasses.fields(inputs)}
    for name, path in paths.items():
        _source_kind(path, name)
    resolved = [path.resolve(strict=True) for path in paths.values()]
    if len(set(resolved)) != len(resolved):
        raise RetentionError("required evidence sources must be distinct")
    exclusion = _read_exclusion_record(paths["exclusion_reason"])
    if lifecycle == "assigned_not_started" and exclusion["classification"] == "agent_outcome":
        raise RetentionError("assigned_not_started requires an exclusion classification")

    manifest = _manifest_for(inputs, assignment_id, session_id, lifecycle)
    archive.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".retention-", dir=archive.parent))
    try:
        for name, source in paths.items():
            _copy_source(source, staging / "artifacts" / name, name)
        (staging / "retention.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        verify_retention_archive(staging)
        if archive.exists():
            raise RetentionError(f"retention archive already exists: {archive}")
        staging.rename(archive)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def _validate_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise RetentionError("retention manifest is not a mapping")
    if set(manifest) != {
        "artifacts",
        "assignment_id",
        "immutable_id",
        "lifecycle",
        "retention_complete",
        "schema_version",
        "session_id",
    }:
        raise RetentionError("retention manifest fields are invalid")
    retained = dict(manifest)
    immutable_id = retained.pop("immutable_id")
    try:
        expected_id = _content_id(retained)
    except (TypeError, ValueError) as exc:
        raise RetentionError("retention manifest is not canonical JSON") from exc
    if not isinstance(immutable_id, str) or immutable_id != expected_id:
        raise RetentionError("retention manifest immutable identity is invalid")
    if retained["schema_version"] != SCHEMA_VERSION or retained["retention_complete"] is not True:
        raise RetentionError("retention manifest schema or completion state is invalid")
    _validate_identifier(retained["assignment_id"], "assignment_id")
    lifecycle = retained["lifecycle"]
    if lifecycle not in _LIFECYCLES:
        raise RetentionError("retention manifest lifecycle is invalid")
    if lifecycle == "started":
        _validate_identifier(retained["session_id"], "session_id")
    elif retained["session_id"] is not None:
        raise RetentionError("unstarted retention manifest has a session_id")
    if not isinstance(retained["artifacts"], dict) or set(retained["artifacts"]) != set(
        _REQUIRED_ARTIFACTS
    ):
        raise RetentionError("retention manifest required artifacts are incomplete")
    return manifest


def _archive_entries(archive: Path) -> dict[str, dict[str, Any]]:
    artifacts = archive / "artifacts"
    if _source_kind(artifacts, "artifacts") != "directory":
        raise RetentionError("retained artifacts root is invalid")
    entries: dict[str, dict[str, Any]] = {}
    for parent, directory_names, file_names in os.walk(artifacts, followlinks=False):
        parent_path = Path(parent)
        for name in sorted(directory_names):
            path = parent_path / name
            if _source_kind(path, "archive") != "directory":
                raise RetentionError("retention archive contains a symlink or special directory")
            relative = path.relative_to(archive).as_posix()
            entries[relative] = {"kind": "directory", "path": relative}
        for name in sorted(file_names):
            path = parent_path / name
            if _source_kind(path, "archive") != "file":
                raise RetentionError("retention archive contains a symlink or special file")
            raw = path.read_bytes()
            relative = path.relative_to(archive).as_posix()
            entries[relative] = {
                "kind": "file",
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size_bytes": len(raw),
            }
    return entries


def verify_retention_archive(archive: Path) -> dict[str, Any]:
    """Recompute every retained byte and reject missing or unmanifested evidence."""
    archive = Path(archive)
    if _source_kind(archive, "retention archive") != "directory":
        raise RetentionError("retention archive root is invalid")
    top_level = {entry.name for entry in archive.iterdir()}
    if top_level != {"artifacts", "retention.json"}:
        raise RetentionError(f"retention archive top-level entries are invalid: {top_level}")
    if _source_kind(archive / "retention.json", "retention manifest") != "file":
        raise RetentionError("retention manifest is not a regular file")
    try:
        manifest = json.loads((archive / "retention.json").read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RetentionError("retention manifest is missing, unreadable, or invalid") from exc
    _validate_manifest(manifest)

    expected: dict[str, dict[str, Any]] = {}
    for name, artifact in manifest["artifacts"].items():
        if not isinstance(artifact, dict) or set(artifact) != {"entries", "source_kind"}:
            raise RetentionError(f"retention artifact metadata is invalid: {name}")
        if artifact["source_kind"] not in {"file", "directory"} or not isinstance(
            artifact["entries"], list
        ):
            raise RetentionError(f"retention artifact type is invalid: {name}")
        artifact_root = f"artifacts/{name}"
        if any(not isinstance(entry, dict) for entry in artifact["entries"]):
            raise RetentionError(f"retention entry is invalid: {name}")
        root_entries = [
            entry for entry in artifact["entries"] if entry.get("path") == artifact_root
        ]
        if len(root_entries) != 1 or root_entries[0].get("kind") != artifact["source_kind"]:
            raise RetentionError(f"retention artifact root is invalid: {name}")
        for entry in artifact["entries"]:
            if entry.get("kind") not in {"file", "directory"}:
                raise RetentionError(f"retention entry is invalid: {name}")
            path = entry.get("path")
            if not isinstance(path, str):
                raise RetentionError(f"retention entry path is invalid: {name}")
            relative = Path(path)
            if relative.is_absolute() or ".." in relative.parts:
                raise RetentionError(f"retention entry escapes archive: {name}")
            if path != artifact_root and not path.startswith(f"{artifact_root}/"):
                raise RetentionError(f"retention entry crosses artifact boundary: {name}")
            if path in expected:
                raise RetentionError(f"retention entry is duplicated: {path}")
            expected[path] = entry
    actual = _archive_entries(archive)
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(
            path for path in set(actual) & set(expected) if actual[path] != expected[path]
        )
        raise RetentionError(
            f"retention archive mismatch: missing={missing}, extra={extra}, changed={changed}"
        )

    exclusion_path = archive / "artifacts" / "exclusion_reason"
    exclusion = _read_exclusion_record(exclusion_path)
    if manifest["lifecycle"] == "assigned_not_started" and exclusion["classification"] == (
        "agent_outcome"
    ):
        raise RetentionError("unstarted assignment lacks an exclusion classification")
    return manifest


def require_retention_for_estimate(archive: Path) -> dict[str, Any]:
    """Fail unless retained evidence is complete and represents an agent outcome."""
    manifest = verify_retention_archive(archive)
    if manifest["lifecycle"] != "started":
        raise RetentionError("retained assignment is not a started session")
    exclusion = _read_exclusion_record(Path(archive) / "artifacts" / "exclusion_reason")
    if exclusion["classification"] != "agent_outcome":
        raise RetentionError(
            f"retained session classification is not agent_outcome: {exclusion['classification']}"
        )
    return manifest


def _audit_inputs(root: Path, classification: str) -> RetentionInputs:
    root.mkdir()
    text = {
        "stdout": "synthetic stdout\n",
        "stderr": "synthetic stderr\n",
        "tool_audit_log": '{"decision":"allow","tool":"fixture"}\n',
        "idea_ledger": '{"id":"I1","status":"closed"}\n',
        "preflight_manifest": '{"immutable_id":"sha256:synthetic-preflight"}\n',
        "postflight_manifest": '{"immutable_id":"sha256:synthetic-postflight"}\n',
        "budget_samples": '{"tokens":0,"wall_s":0.0}\n',
        "randomization_record": '{"assignment_id":"A-001","block":"B-01"}\n',
        "exclusion_reason": json.dumps(
            {"classification": classification, "reasons": [classification]}
        )
        + "\n",
        "tool_policy": '{"allowed":["fixture"],"denied":["network"]}\n',
    }
    paths: dict[str, Path] = {}
    for name, value in text.items():
        path = root / name
        path.write_text(value)
        paths[name] = path
    deliverable = root / "deliverable_tree"
    deliverable.mkdir()
    (deliverable / "candidate.txt").write_text("synthetic candidate\n")
    return RetentionInputs(deliverable_tree=deliverable, **paths)


def _raises_retention(call) -> bool:
    try:
        call()
    except RetentionError:
        return True
    return False


def run_retention_capability_audit() -> dict[str, Any]:
    """Exercise positive, negative, and estimate-gate TRT-10 controls."""
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="aisle-retention-audit-") as directory:
        root = Path(directory)
        started_inputs = _audit_inputs(root / "started-source", "synthetic_unscored")
        started_archive = root / "started-archive"
        started = retain_evidence(
            started_inputs,
            started_archive,
            assignment_id="A-001",
            session_id="S-001",
            lifecycle="started",
        )
        checks["all_required_artifacts_retained"] = set(started["artifacts"]) == set(
            _REQUIRED_ARTIFACTS
        )
        checks["published_archive_verifies"] = verify_retention_archive(started_archive) == started
        checks["synthetic_outcome_rejected_from_estimate"] = _raises_retention(
            lambda: require_retention_for_estimate(started_archive)
        )
        checks["overwrite_refused"] = _raises_retention(
            lambda: retain_evidence(
                started_inputs,
                started_archive,
                assignment_id="A-001",
                session_id="S-001",
                lifecycle="started",
            )
        )

        corrupt_archive = root / "corrupt-archive"
        shutil.copytree(started_archive, corrupt_archive)
        (corrupt_archive / "artifacts" / "stdout").write_text("mutated\n")
        checks["corruption_detected"] = _raises_retention(
            lambda: verify_retention_archive(corrupt_archive)
        )

        missing_inputs = _audit_inputs(root / "missing-source", "synthetic_unscored")
        missing_inputs.stderr.unlink()
        checks["missing_source_refused_before_publish"] = (
            _raises_retention(
                lambda: retain_evidence(
                    missing_inputs,
                    root / "missing-archive",
                    assignment_id="A-002",
                    session_id="S-002",
                    lifecycle="started",
                )
            )
            and not (root / "missing-archive").exists()
        )

        unstarted_inputs = _audit_inputs(root / "unstarted-source", "launch_refused")
        unstarted_archive = root / "unstarted-archive"
        retain_evidence(
            unstarted_inputs,
            unstarted_archive,
            assignment_id="A-003",
            session_id=None,
            lifecycle="assigned_not_started",
        )
        checks["unstarted_assignment_retained"] = (
            verify_retention_archive(unstarted_archive)["lifecycle"] == "assigned_not_started"
        )
        checks["unstarted_assignment_rejected_from_estimate"] = _raises_retention(
            lambda: require_retention_for_estimate(unstarted_archive)
        )
        checks["atomic_publication_left_no_staging"] = not list(root.glob(".retention-*"))

    recorded_at = datetime.now(UTC)
    passed = sum(checks.values())
    return {
        "capability_pass": passed == len(checks),
        "checks": checks,
        "checks_passed": passed,
        "checks_total": len(checks),
        "configuration": {
            "archive_schema": SCHEMA_VERSION,
            "lifecycles": sorted(_LIFECYCLES),
            "required_artifacts": sorted(_REQUIRED_ARTIFACTS),
        },
        "confirmatory_ready": False,
        "evidence_class": EVIDENCE_CLASS,
        "environment": {
            "machine": platform.machine(),
            "os": platform.system(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        },
        "limitations": [
            "synthetic fixtures only",
            "not integrated into Claude or Codex launchers",
            "does not authorize cleanup or confirmatory estimation",
        ],
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "randomization": {"seed": None, "status": "not_applicable_synthetic_capability"},
        "schema_version": AUDIT_SCHEMA_VERSION,
        "session_id": f"retention-capability-{recorded_at.strftime('%Y%m%dT%H%M%S%fZ')}",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def write_retention_capability_audit(output: Path) -> dict[str, Any]:
    """Write one non-overwriting synthetic capability audit."""
    output = Path(output)
    if output.exists():
        raise RetentionError(f"retention capability audit already exists: {output}")
    report = run_retention_capability_audit()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise RetentionError(f"retention capability audit already exists: {output}") from exc
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="run the synthetic retention capability audit")
    audit.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = write_retention_capability_audit(args.output)
    except RetentionError as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"ok": report["capability_pass"], "output": str(args.output)}))
    return 0 if report["capability_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
