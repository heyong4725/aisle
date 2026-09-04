"""Credential scrubbing and secret-free postflight proof for SPEC 420 TRT-11."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "aisle.credential-scrub.v1"
AUDIT_SCHEMA_VERSION = "aisle.credential-scrub-capability.v1"
EVIDENCE_CLASS = "synthetic_unscored_credential_scrub_capability"
CANONICAL_CREDENTIALS = {
    "claude": Path(".claude/.credentials.json"),
    "codex": Path(".codex/auth.json"),
}
_PHASES = frozenset({"auth_probe", "postflight", "rotation", "session"})
_TRIGGERS = frozenset({"exception", "manual", "normal_exit"})


class CredentialScrubError(RuntimeError):
    """Credential postflight could not prove canonical locations absent."""


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


def _home_identity(home: Path) -> str:
    return hashlib.sha256(str(home.resolve(strict=False)).encode()).hexdigest()


def _path_state(path: Path) -> str:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "unreadable"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    return "special"


def _unlink(path: Path) -> None:
    """Narrow seam used by mutation tests for unlink failures."""
    path.unlink()


def _unsafe_parent_state(home: Path, relative: Path) -> str | None:
    home_state = _path_state(home)
    if home_state not in {"absent", "directory"}:
        return f"unsafe_home_{home_state}"
    current = home
    for part in relative.parts[:-1]:
        current /= part
        state = _path_state(current)
        if state == "absent":
            return None
        if state != "directory":
            return f"unsafe_parent_{state}"
    return None


def _scrub_event(home: Path, agent: str, relative: Path) -> dict[str, str]:
    path = home / relative
    unsafe_parent = _unsafe_parent_state(home, relative)
    if unsafe_parent is not None:
        return {
            "action": "refused",
            "after": unsafe_parent,
            "agent": agent,
            "before": unsafe_parent,
            "error_class": unsafe_parent,
            "relative_path": relative.as_posix(),
        }
    before = _path_state(path)
    event = {
        "action": "already_absent",
        "after": before,
        "agent": agent,
        "before": before,
        "relative_path": relative.as_posix(),
    }
    if before == "absent":
        return event
    if before not in {"file", "symlink"}:
        event["action"] = "refused"
        event["error_class"] = f"unexpected_{before}"
        return event
    try:
        _unlink(path)
    except OSError as exc:
        event["action"] = "unlink_failed"
        event["error_class"] = type(exc).__name__
    else:
        event["action"] = "unlinked"
    event["after"] = _path_state(path)
    if event["after"] != "absent" and "error_class" not in event:
        event["error_class"] = "credential_still_present"
    return event


def scrub_credentials(
    home: Path,
    output: Path,
    *,
    phase: str = "postflight",
    trigger: str = "manual",
) -> dict[str, Any]:
    """Scrub exact canonical locations and retain content-addressed proof.

    Cleanup is attempted before the non-overwriting proof is published. Thus a
    stale output path cannot prevent credential deletion, but it still fails
    loudly because the current cleanup cannot be audited.
    """
    home = Path(home)
    output = Path(output)
    if phase not in _PHASES:
        raise CredentialScrubError(f"credential scrub phase is invalid: {phase!r}")
    if trigger not in _TRIGGERS:
        raise CredentialScrubError(f"credential scrub trigger is invalid: {trigger!r}")

    events = [
        _scrub_event(home, agent, relative)
        for agent, relative in sorted(CANONICAL_CREDENTIALS.items())
    ]
    complete = all(event["after"] == "absent" for event in events)
    record: dict[str, Any] = {
        "canonical_credentials_absent": complete,
        "canonical_locations": sorted(path.as_posix() for path in CANONICAL_CREDENTIALS.values()),
        "classification": "scrubbed" if complete else "infrastructure_exclusion",
        "complete": complete,
        "credential_bytes_in_record": False,
        "events": events,
        "home_identity_sha256": _home_identity(home),
        "phase": phase,
        "schema_version": SCHEMA_VERSION,
        "trigger": trigger,
    }
    record["immutable_id"] = _content_id(record)

    resolved_output = output.resolve(strict=False)
    for relative in CANONICAL_CREDENTIALS.values():
        credential = (home / relative).resolve(strict=False)
        if resolved_output == credential or resolved_output.is_relative_to(credential):
            raise CredentialScrubError(
                f"credential scrub record path overlaps canonical credential: {relative}"
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise CredentialScrubError(f"credential scrub record already exists: {output}") from exc
    except OSError as exc:
        raise CredentialScrubError(f"credential scrub record could not be retained: {exc}") from exc
    if not complete:
        raise CredentialScrubError(f"credential scrub incomplete; retained proof: {output}")
    return record


@contextmanager
def credential_scrub_guard(home: Path, output: Path, *, phase: str) -> Iterator[None]:
    """Guarantee scrub proof after normal exit or any interrupted body."""
    trigger = "normal_exit"
    try:
        yield
    except BaseException:
        trigger = "exception"
        raise
    finally:
        scrub_credentials(home, output, phase=phase, trigger=trigger)


def verify_credential_scrub_record(home: Path, record_path: Path) -> dict[str, Any]:
    """Verify immutable proof and recheck both canonical locations."""
    record_path = Path(record_path)
    try:
        record = json.loads(record_path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CredentialScrubError("credential scrub record is missing or invalid") from exc
    expected_fields = {
        "canonical_credentials_absent",
        "canonical_locations",
        "classification",
        "complete",
        "credential_bytes_in_record",
        "events",
        "home_identity_sha256",
        "immutable_id",
        "phase",
        "schema_version",
        "trigger",
    }
    if not isinstance(record, dict) or set(record) != expected_fields:
        raise CredentialScrubError("credential scrub record fields are invalid")
    retained = dict(record)
    immutable_id = retained.pop("immutable_id")
    try:
        expected_id = _content_id(retained)
    except (TypeError, ValueError) as exc:
        raise CredentialScrubError("credential scrub record is not canonical JSON") from exc
    if not isinstance(immutable_id, str) or immutable_id != expected_id:
        raise CredentialScrubError("credential scrub record immutable identity is invalid")
    expected_locations = sorted(path.as_posix() for path in CANONICAL_CREDENTIALS.values())
    if (
        record["schema_version"] != SCHEMA_VERSION
        or record["home_identity_sha256"] != _home_identity(Path(home))
        or record["canonical_locations"] != expected_locations
        or record["credential_bytes_in_record"] is not False
    ):
        raise CredentialScrubError("credential scrub record identity or policy is invalid")
    events = record["events"]
    if not isinstance(events, list) or len(events) != len(CANONICAL_CREDENTIALS):
        raise CredentialScrubError("credential scrub events are incomplete")
    expected_pairs = {(agent, path.as_posix()) for agent, path in CANONICAL_CREDENTIALS.items()}
    actual_pairs: set[tuple[str, str]] = set()
    for event in events:
        if not isinstance(event, dict) or set(event) != {
            "action",
            "after",
            "agent",
            "before",
            "relative_path",
        }:
            raise CredentialScrubError("credential scrub event is invalid")
        agent = event.get("agent")
        relative = event.get("relative_path")
        if not isinstance(agent, str) or not isinstance(relative, str):
            raise CredentialScrubError("credential scrub event identity is invalid")
        actual_pairs.add((agent, relative))
        if event.get("after") != "absent":
            raise CredentialScrubError("credential scrub record proves incomplete cleanup")
        action = event.get("action")
        before = event.get("before")
        if not (
            (action == "already_absent" and before == "absent")
            or (action == "unlinked" and before in {"file", "symlink"})
        ):
            raise CredentialScrubError("credential scrub event semantics are invalid")
    if actual_pairs != expected_pairs:
        raise CredentialScrubError("credential scrub event locations are invalid")
    if (
        record["complete"] is not True
        or record["canonical_credentials_absent"] is not True
        or record["classification"] != "scrubbed"
    ):
        raise CredentialScrubError("credential scrub record is an infrastructure exclusion")
    if record["phase"] not in _PHASES or record["trigger"] not in _TRIGGERS:
        raise CredentialScrubError("credential scrub phase or trigger is invalid")
    for relative in CANONICAL_CREDENTIALS.values():
        if _path_state(Path(home) / relative) != "absent":
            raise CredentialScrubError(f"canonical credential is present after proof: {relative}")
    return record


def _seed_fixture(home: Path, sentinel: str) -> None:
    for agent, relative in CANONICAL_CREDENTIALS.items():
        path = home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"refresh_token": f"{agent}-{sentinel}"}))


def _raises_scrub(call) -> bool:
    try:
        call()
    except CredentialScrubError:
        return True
    return False


def run_credential_scrub_capability_audit() -> dict[str, Any]:
    """Exercise credential lifecycle controls without real credential bytes."""
    sentinel = "synthetic-refresh-token-TRT11"
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="aisle-credential-audit-") as directory:
        root = Path(directory)

        normal_home = root / "normal-home"
        _seed_fixture(normal_home, sentinel)
        normal_output = root / "normal.json"
        normal = scrub_credentials(normal_home, normal_output, phase="session")
        checks["normal_scrub_verified"] = (
            verify_credential_scrub_record(normal_home, normal_output) == normal
        )
        checks["both_canonical_locations_absent"] = all(
            _path_state(normal_home / relative) == "absent"
            for relative in CANONICAL_CREDENTIALS.values()
        )
        checks["credential_bytes_absent_from_proof"] = sentinel not in normal_output.read_text()

        interrupted_home = root / "interrupted-home"
        _seed_fixture(interrupted_home, sentinel)
        interrupted_output = root / "interrupted.json"
        try:
            with credential_scrub_guard(interrupted_home, interrupted_output, phase="session"):
                raise KeyboardInterrupt
        except KeyboardInterrupt:
            pass
        interrupted = verify_credential_scrub_record(interrupted_home, interrupted_output)
        checks["interrupted_session_scrubbed"] = interrupted["trigger"] == "exception"

        aborted_home = root / "aborted-home"
        _seed_fixture(aborted_home, sentinel)
        aborted_output = root / "aborted.json"
        try:
            with credential_scrub_guard(aborted_home, aborted_output, phase="auth_probe"):
                raise RuntimeError("synthetic launch refusal")
        except RuntimeError:
            pass
        aborted = verify_credential_scrub_record(aborted_home, aborted_output)
        checks["aborted_launch_scrubbed"] = aborted["phase"] == "auth_probe"

        evidence_home = root / "evidence-home"
        _seed_fixture(evidence_home, sentinel)
        evidence = evidence_home / "workspace" / "auth.json"
        evidence.parent.mkdir()
        evidence.write_text("retained evidence")
        scrub_credentials(evidence_home, root / "evidence.json")
        checks["nonsecret_evidence_preserved"] = evidence.read_text() == "retained evidence"

        external = root / "external.json"
        external.write_text("retained external evidence")
        symlink_home = root / "symlink-home"
        symlink = symlink_home / CANONICAL_CREDENTIALS["codex"]
        symlink.parent.mkdir(parents=True)
        symlink.symlink_to(external)
        scrub_credentials(symlink_home, root / "symlink.json")
        checks["symlink_target_preserved"] = (
            _path_state(symlink) == "absent"
            and external.read_text() == "retained external evidence"
        )

        parent_external = root / "parent-external"
        parent_external.mkdir()
        parent_credential = parent_external / "auth.json"
        parent_credential.write_text(f"external-{sentinel}")
        parent_home = root / "parent-symlink-home"
        parent_home.mkdir()
        (parent_home / ".codex").symlink_to(parent_external)
        parent_output = root / "parent-symlink.json"
        checks["parent_symlink_refused_without_external_delete"] = (
            _raises_scrub(lambda: scrub_credentials(parent_home, parent_output))
            and parent_credential.read_text() == f"external-{sentinel}"
        )

        malformed_home = root / "malformed-home"
        malformed = malformed_home / CANONICAL_CREDENTIALS["codex"]
        malformed.mkdir(parents=True)
        (malformed / "evidence.txt").write_text("retained unknown content")
        malformed_output = root / "malformed.json"
        checks["unexpected_path_shape_excluded"] = _raises_scrub(
            lambda: scrub_credentials(malformed_home, malformed_output)
        ) and json.loads(malformed_output.read_text())["classification"] == (
            "infrastructure_exclusion"
        )

        absent_home = root / "absent-home"
        absent_home.mkdir()
        absent_output = root / "absent.json"
        scrub_credentials(absent_home, absent_output)
        checks["already_absent_proof_verified"] = bool(
            verify_credential_scrub_record(absent_home, absent_output)
        )

        checks["record_overwrite_refused"] = _raises_scrub(
            lambda: scrub_credentials(absent_home, absent_output)
        )

    recorded_at = datetime.now(UTC)
    passed = sum(checks.values())
    return {
        "capability_pass": passed == len(checks),
        "checks": checks,
        "checks_passed": passed,
        "checks_total": len(checks),
        "configuration": {
            "canonical_locations": sorted(
                path.as_posix() for path in CANONICAL_CREDENTIALS.values()
            ),
            "scrub_schema": SCHEMA_VERSION,
        },
        "confirmatory_ready": False,
        "environment": {
            "machine": platform.machine(),
            "os": platform.system(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        },
        "evidence_class": EVIDENCE_CLASS,
        "limitations": [
            "synthetic credential sentinels only",
            "not integrated into every Claude or Codex launcher path",
            "does not exercise a real vendor refresh token",
        ],
        "randomization": {"seed": None, "status": "not_applicable_synthetic_capability"},
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "schema_version": AUDIT_SCHEMA_VERSION,
        "session_id": f"credential-scrub-capability-{recorded_at.strftime('%Y%m%dT%H%M%S%fZ')}",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }


def write_credential_scrub_capability_audit(output: Path) -> dict[str, Any]:
    """Write one non-overwriting synthetic credential-scrub audit."""
    output = Path(output)
    if output.exists():
        raise CredentialScrubError(f"credential scrub capability audit already exists: {output}")
    report = run_credential_scrub_capability_audit()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise CredentialScrubError(
            f"credential scrub capability audit already exists: {output}"
        ) from exc
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="run the synthetic credential-scrub audit")
    audit.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = write_credential_scrub_capability_audit(args.output)
    except CredentialScrubError as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"ok": report["capability_pass"], "output": str(args.output)}))
    return 0 if report["capability_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
