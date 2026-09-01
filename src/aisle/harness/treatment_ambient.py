"""Declared ambient-state boundary for SPEC 420 treatment sessions.

This module removes operator environment state, creates fresh per-session
HOME/config/cache/temp roots, and owns process spawning with non-standard file
descriptors closed.  Its built-in audit is synthetic and unscored; it is a
capability check, not confirmatory campaign evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "aisle.ambient-baseline.v1"
AUDIT_SCHEMA_VERSION = "aisle.ambient-capability.v1"
EVIDENCE_CLASS = "synthetic_unscored_ambient_capability"
_FORWARDED_VARIABLES = ("PATH", "LANG", "LC_ALL", "LC_CTYPE")
_GENERATED_PATHS = {
    "HOME": ".",
    "CLAUDE_CONFIG_DIR": ".claude",
    "CODEX_HOME": ".codex",
    "XDG_CONFIG_HOME": ".config",
    "XDG_DATA_HOME": ".local/share",
    "XDG_CACHE_HOME": ".cache",
    "XDG_STATE_HOME": ".local/state",
    "TMPDIR": ".tmp",
    "TMP": ".tmp",
    "TEMP": ".tmp",
}
_COMMIT_LENGTHS = (40, 64)
_CONTROLLER_AMENDMENT_KEYS = {"PYTHONPATH"}


class AmbientIsolationError(RuntimeError):
    """The declared ambient baseline or isolated spawn is invalid."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _record_id(record: Mapping[str, Any]) -> str:
    retained = dict(record)
    retained.pop("immutable_id", None)
    return f"sha256:{_sha256_bytes(_canonical_bytes(retained))}"


def _validate_baseline_oid(value: str | None) -> None:
    if value is None:
        return
    if len(value) not in _COMMIT_LENGTHS or any(char not in "0123456789abcdef" for char in value):
        raise AmbientIsolationError("environment baseline must be a full lowercase Git object id")


def _validate_home(home: Path) -> Path:
    home = Path(home)
    if not home.is_absolute():
        raise AmbientIsolationError("isolated HOME must be absolute")
    if home == Path("/"):
        raise AmbientIsolationError("isolated HOME cannot be the filesystem root")
    if home.exists():
        if home.is_symlink() or home.resolve() != home:
            raise AmbientIsolationError("isolated HOME must be canonical and not a symlink")
        try:
            if any(home.iterdir()):
                raise AmbientIsolationError("isolated HOME must start empty")
        except OSError as exc:
            raise AmbientIsolationError(f"cannot inspect isolated HOME: {exc}") from exc
    else:
        parent = home.parent
        if parent.exists() and parent.resolve() != parent:
            raise AmbientIsolationError("isolated HOME parent must be canonical")
    return home


def _create_private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    except OSError as exc:
        raise AmbientIsolationError(
            f"cannot create isolated state directory {path}: {exc}"
        ) from exc
    if not path.is_dir() or path.is_symlink() or path.resolve() != path:
        raise AmbientIsolationError(f"isolated state directory is unsafe: {path}")


def build_declared_environment(
    home: Path,
    *,
    source_env: Mapping[str, str] | None = None,
    env_baseline_oid: str | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Build the complete child environment from a narrow declared baseline.

    Only the executable search path and locale are forwarded.  Agent homes,
    XDG roots, and temporary roots are generated under a fresh private HOME.
    Credentials are deliberately absent here and may only be seeded later into
    the canonical credential files by the controller.
    """

    _validate_baseline_oid(env_baseline_oid)
    home = _validate_home(home)
    source = dict(os.environ if source_env is None else source_env)
    environment: dict[str, str] = {}
    for name in _FORWARDED_VARIABLES:
        value = source.get(name)
        if value is not None:
            if not isinstance(value, str) or "\x00" in value:
                raise AmbientIsolationError(f"declared environment variable {name} is invalid")
            environment[name] = value
    environment.setdefault("PATH", os.defpath)
    # CPython's locale coercion and macOS launch services otherwise add these
    # after exec, making the observed child environment differ from the
    # controller record.  Fix them to declared, non-operator values.
    environment.setdefault("LANG", "C.UTF-8")
    environment.setdefault("LC_CTYPE", "C.UTF-8")
    if sys.platform == "darwin":
        environment["__CF_USER_TEXT_ENCODING"] = f"0x{os.getuid():X}:0x0:0x0"

    for name, relative in _GENERATED_PATHS.items():
        path = home if relative == "." else home / relative
        _create_private_directory(path)
        environment[name] = str(path)
    if env_baseline_oid is not None:
        environment["AISLE_ENV_BASELINE"] = env_baseline_oid

    endpoint_keys = sorted(
        name
        for name in source
        if name.endswith(("_SOCK", "_SOCKET"))
        or name in {"DBUS_SESSION_BUS_ADDRESS", "DOCKER_HOST", "SSH_AUTH_SOCK"}
    )
    record: dict[str, Any] = {
        "environment_keys": sorted(environment),
        "environment_sha256": _sha256_bytes(_canonical_bytes(environment)),
        "forwarded_variables": sorted(name for name in _FORWARDED_VARIABLES if name in environment),
        "home": str(home),
        "inherited_fd_policy": "close-all-nonstandard",
        "local_endpoint_policy": "no-ambient-endpoint-variables",
        "removed_endpoint_key_count": len(endpoint_keys),
        "removed_source_key_count": len(set(source) - set(environment)),
        "schema_version": SCHEMA_VERSION,
        "state_directories": sorted(set(environment[name] for name in _GENERATED_PATHS)),
    }
    if env_baseline_oid is not None:
        record["env_baseline_oid"] = env_baseline_oid
    record["immutable_id"] = _record_id(record)
    return environment, record


def verify_declared_environment(
    environment: Mapping[str, str], environment_record: Mapping[str, Any]
) -> None:
    """Fail closed if the launch environment differs from its controller record."""

    if environment_record.get("schema_version") != SCHEMA_VERSION:
        raise AmbientIsolationError("ambient environment record schema is unsupported")
    if environment_record.get("immutable_id") != _record_id(environment_record):
        raise AmbientIsolationError("ambient environment record identity is invalid")
    if sorted(environment) != environment_record.get("environment_keys"):
        raise AmbientIsolationError("ambient environment drift: keys differ from the record")
    digest = _sha256_bytes(_canonical_bytes(dict(environment)))
    if digest != environment_record.get("environment_sha256"):
        raise AmbientIsolationError("ambient environment drift: content differs from the record")
    if environment_record.get("inherited_fd_policy") != "close-all-nonstandard":
        raise AmbientIsolationError("ambient file-descriptor policy is unresolved")


def amend_declared_environment(
    environment: dict[str, str],
    environment_record: dict[str, Any],
    *,
    added_keys: Sequence[str],
    reason: str,
) -> None:
    """Attest a narrow controller-owned addition made before session spawn."""

    keys = tuple(added_keys)
    if (
        not keys
        or len(keys) != len(set(keys))
        or not all(isinstance(key, str) and key for key in keys)
    ):
        raise AmbientIsolationError("controller amendment keys must be unique and non-empty")
    expected = set(environment_record.get("environment_keys", ()))
    additions = set(keys)
    current = set(environment)
    if current != expected | additions or expected & additions:
        raise AmbientIsolationError("unnamed environment drift accompanied controller amendment")
    if not additions <= _CONTROLLER_AMENDMENT_KEYS:
        raise AmbientIsolationError("controller amendment contains an unsupported environment key")
    if not isinstance(reason, str) or not reason.strip():
        raise AmbientIsolationError("controller amendment reason is required")
    original = {name: value for name, value in environment.items() if name not in additions}
    verify_declared_environment(original, environment_record)
    amendments = list(environment_record.get("controller_amendments", ()))
    amendments.append({"added_keys": sorted(additions), "reason": reason})
    environment_record["controller_amendments"] = amendments
    environment_record["environment_keys"] = sorted(environment)
    environment_record["environment_sha256"] = _sha256_bytes(_canonical_bytes(environment))
    environment_record["immutable_id"] = _record_id(environment_record)


def spawn_isolated_process(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    environment_record: Mapping[str, Any],
    **stream_options: Any,
) -> subprocess.Popen:
    """Spawn one session with exact environment identity and closed extra FDs."""

    if not command or not all(isinstance(part, str) and part for part in command):
        raise AmbientIsolationError("isolated command must be a non-empty argv")
    forbidden = {"close_fds", "cwd", "env", "pass_fds", "start_new_session"}
    supplied = forbidden & set(stream_options)
    if supplied:
        raise AmbientIsolationError(
            "isolated spawn options cannot override " + ", ".join(sorted(supplied))
        )
    verify_declared_environment(environment, environment_record)
    try:
        return subprocess.Popen(
            list(command),
            cwd=Path(cwd),
            env=dict(environment),
            close_fds=True,
            start_new_session=True,
            **stream_options,
        )
    except OSError as exc:
        raise AmbientIsolationError(f"isolated process failed to start: {exc}") from exc


def _case(case_id: str, passed: bool, *, false_alarm: bool = False) -> dict[str, Any]:
    return {"false_alarm_control": false_alarm, "id": case_id, "passed": bool(passed)}


def run_ambient_capability_audit() -> dict[str, Any]:
    """Exercise the ambient boundary with synthetic operator state and one real child."""

    with tempfile.TemporaryDirectory(prefix="aisle-ambient-capability-") as temporary:
        root = Path(temporary).resolve()
        operator = root / "operator"
        operator.mkdir()
        source = {
            "HOME": str(operator),
            "PATH": os.defpath,
            "LANG": "C",
            "CLAUDE_CONFIG_DIR": str(operator / ".claude"),
            "CODEX_HOME": str(operator / ".codex"),
            "XDG_CONFIG_HOME": str(operator / ".config"),
            "XDG_DATA_HOME": str(operator / ".local" / "share"),
            "XDG_CACHE_HOME": str(operator / ".cache"),
            "XDG_STATE_HOME": str(operator / ".local" / "state"),
            "TMPDIR": str(operator / "tmp"),
            "AISLE_UNRELATED_SENTINEL": "SYNTHETIC-AMBIENT-SENTINEL",
            "ANTHROPIC_API_KEY": "SYNTHETIC-CREDENTIAL",
            "OPENAI_API_KEY": "SYNTHETIC-CREDENTIAL",
            "SSH_AUTH_SOCK": str(operator / "agent.sock"),
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={operator / 'bus.sock'}",
        }
        home = root / "session" / "agent_home"
        environment, environment_record = build_declared_environment(
            home, source_env=source, env_baseline_oid="a" * 40
        )
        read_fd, write_fd = os.pipe()
        try:
            os.set_inheritable(read_fd, True)
            script = (
                "import json, os, sys; fd=int(sys.argv[1]); "
                "\ntry:\n os.fstat(fd); opened=True"
                "\nexcept OSError:\n opened=False"
                "\nprint(json.dumps({'fd_open': opened, 'env': dict(os.environ)}, sort_keys=True))"
            )
            process = spawn_isolated_process(
                [sys.executable, "-c", script, str(read_fd)],
                cwd=root,
                environment=environment,
                environment_record=environment_record,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout, stderr = process.communicate(timeout=15)
        except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
            raise AmbientIsolationError(f"synthetic ambient child failed: {exc}") from exc
        finally:
            os.close(read_fd)
            os.close(write_fd)
        if process.returncode != 0:
            raise AmbientIsolationError(
                "synthetic ambient child returned "
                f"{process.returncode}; stderr_sha256={_sha256_bytes(stderr.encode())}"
            )
        try:
            observed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise AmbientIsolationError("synthetic ambient child returned invalid JSON") from exc
        observed_env = observed.get("env")
        if not isinstance(observed_env, dict):
            raise AmbientIsolationError("synthetic ambient child omitted its environment")

        generated_paths = {name: environment[name] for name in _GENERATED_PATHS}
        cases = [
            _case("home_rebound", observed_env.get("HOME") == str(home)),
            _case(
                "agent_config_homes_rebound",
                observed_env.get("CLAUDE_CONFIG_DIR") == str(home / ".claude")
                and observed_env.get("CODEX_HOME") == str(home / ".codex"),
            ),
            _case(
                "xdg_and_tmp_rebound",
                all(observed_env.get(name) == value for name, value in generated_paths.items()),
            ),
            _case(
                "unrelated_environment_removed",
                "AISLE_UNRELATED_SENTINEL" not in observed_env,
            ),
            _case(
                "credential_environment_removed",
                "ANTHROPIC_API_KEY" not in observed_env and "OPENAI_API_KEY" not in observed_env,
            ),
            _case(
                "socket_endpoint_environment_removed",
                "SSH_AUTH_SOCK" not in observed_env
                and "DBUS_SESSION_BUS_ADDRESS" not in observed_env,
            ),
            _case("inherited_fd_closed", observed.get("fd_open") is False),
            _case(
                "declared_path_forwarded",
                observed_env.get("PATH") == source["PATH"],
                false_alarm=True,
            ),
        ]
        detection = [row for row in cases if not row["false_alarm_control"]]
        false_alarm = [row for row in cases if row["false_alarm_control"]]
        capability_pass = all(row["passed"] for row in cases)
        recorded_at = datetime.now(UTC)
        return {
            "capability_pass": capability_pass,
            "cases": cases,
            "confirmatory_ready": False,
            "environment_record": environment_record,
            "evidence_class": EVIDENCE_CLASS,
            "implementation_sha256": _sha256_bytes(Path(__file__).read_bytes()),
            "limitations": [
                "synthetic process only; no Claude or Codex vendor session",
                "environment endpoint removal does not replace filesystem/network confinement",
                "credential-file seeding and post-session scrubbing are audited separately",
            ],
            "platform": {
                "machine": platform.machine(),
                "python": platform.python_version(),
                "release": platform.release(),
                "system": platform.system(),
            },
            "randomization_seed": None,
            "randomization_status": "not-applicable-synthetic-capability",
            "recorded_at": recorded_at.isoformat(),
            "schema_version": AUDIT_SCHEMA_VERSION,
            "session_id": f"ambient-capability-{recorded_at.strftime('%Y%m%dT%H%M%S%fZ')}",
            "summary": {
                "checks": len(cases),
                "detection_rate": sum(row["passed"] for row in detection) / len(detection),
                "false_alarm_checks": len(false_alarm),
                "false_alarm_rate": sum(not row["passed"] for row in false_alarm)
                / len(false_alarm),
            },
        }


def write_ambient_capability_audit(output: Path) -> dict[str, Any]:
    """Retain one non-overwriting synthetic TRT-7 capability record."""

    output = Path(output)
    if output.exists():
        raise AmbientIsolationError(f"ambient capability audit already exists: {output}")
    report = run_ambient_capability_audit()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise AmbientIsolationError(f"ambient capability audit already exists: {output}") from exc
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the synthetic TRT-7 ambient audit")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit-synthetic")
    audit.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = write_ambient_capability_audit(args.output)
    except AmbientIsolationError as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "capability_pass": report["capability_pass"],
                "confirmatory_ready": report["confirmatory_ready"],
                "evidence_class": report["evidence_class"],
                "ok": report["capability_pass"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["capability_pass"] else 3


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess test
    raise SystemExit(main())
