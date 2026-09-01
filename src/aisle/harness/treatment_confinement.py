"""Unscored macOS confinement capability audit for SPEC 420.

The audit is controller-owned and runs synthetic sentinels outside the visible
tree.  It deliberately does not authorize confirmatory collection: Linux,
vendor-network access, credential handling, and Claude/Codex parity remain
separate treatment-integrity gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "aisle.macos-confinement-capability.v2"
EVIDENCE_CLASS = "synthetic_unscored_capability"
SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
SYSTEM_PROFILE = Path("/System/Library/Sandbox/Profiles/system.sb")
_HASH_LENGTH = 64
_REQUIRED_CASE_IDS = {
    "absolute_hidden_read",
    "alternate_worktree_hidden_read",
    "declared_output_write",
    "git_object_hidden_read",
    "hidden_write",
    "parent_traversal_hidden_read",
    "subprocess_hidden_read",
    "subprocess_visible_read",
    "symlink_hidden_read",
    "unrestricted_alternate_worktree_baseline",
    "unrestricted_git_object_baseline",
    "unrestricted_hidden_baseline",
    "visible_git_object_read",
    "visible_read",
}


class ConfinementError(RuntimeError):
    """The external adapter or its retained capability evidence is unusable."""


@dataclass(frozen=True)
class MacOSPolicy:
    """Controller-owned roots and process policy compiled to macOS SBPL."""

    visible_roots: tuple[Path, ...]
    output_roots: tuple[Path, ...]
    runtime_read_roots: tuple[Path, ...]
    allowed_executables: tuple[Path, ...]
    hidden_roots: tuple[Path, ...]
    network_policy: str

    def as_dict(self) -> dict[str, Any]:
        """Return constructor-compatible fields for tests and policy transforms."""
        return {
            "visible_roots": self.visible_roots,
            "output_roots": self.output_roots,
            "runtime_read_roots": self.runtime_read_roots,
            "allowed_executables": self.allowed_executables,
            "hidden_roots": self.hidden_roots,
            "network_policy": self.network_policy,
        }

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "allowed_executables": [str(path) for path in self.allowed_executables],
            "hidden_roots": [str(path) for path in self.hidden_roots],
            "network_policy": self.network_policy,
            "output_roots": [str(path) for path in self.output_roots],
            "runtime_read_roots": [str(path) for path in self.runtime_read_roots],
            "visible_roots": [str(path) for path in self.visible_roots],
        }


@dataclass(frozen=True)
class CompiledProfile:
    text: str
    sha256: str
    policy_id: str


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise ConfinementError(f"cannot hash required adapter input {path}: {exc}") from exc


def _validate_roots(name: str, roots: tuple[Path, ...]) -> None:
    if not roots:
        raise ConfinementError(f"{name} must not be empty")
    rendered = [str(path) for path in roots]
    if len(rendered) != len(set(rendered)):
        raise ConfinementError(f"{name} contains a duplicate")
    for path in roots:
        if not path.is_absolute():
            raise ConfinementError(f"{name} contains a relative path: {path}")
        if path == Path("/"):
            raise ConfinementError(f"{name} cannot authorize the filesystem root")
        if not path.exists():
            raise ConfinementError(f"{name} contains an unresolved path: {path}")
        if path.resolve() != path:
            raise ConfinementError(f"{name} contains a non-canonical path: {path}")


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _validate_policy(policy: MacOSPolicy) -> None:
    if policy.network_policy != "deny-external":
        raise ConfinementError("macOS capability audit requires deny-external network policy")
    for name in (
        "visible_roots",
        "output_roots",
        "runtime_read_roots",
        "allowed_executables",
        "hidden_roots",
    ):
        _validate_roots(name, getattr(policy, name))
    for executable in policy.allowed_executables:
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ConfinementError(f"allowed executable is not runnable: {executable}")

    readable = (*policy.visible_roots, *policy.output_roots, *policy.runtime_read_roots)
    for hidden in policy.hidden_roots:
        for allowed in readable:
            if _contains(allowed, hidden) or _contains(hidden, allowed):
                raise ConfinementError(
                    f"hidden root overlaps an agent-readable root: {hidden} vs {allowed}"
                )


def _sbpl_path(path: Path) -> str:
    return json.dumps(str(path))


def compile_macos_profile(policy: MacOSPolicy) -> CompiledProfile:
    """Compile a default-deny profile without disclosing hidden-root names."""
    _validate_policy(policy)
    lines = [
        "(version 1)",
        "(deny default)",
        '(import "system.sb")',
        "(allow process-fork)",
        "(allow process-exec",
    ]
    lines.extend(f"  (literal {_sbpl_path(path)})" for path in policy.allowed_executables)
    lines.extend([")", "(allow file-read*"])
    lines.extend(
        f"  (subpath {_sbpl_path(path)})"
        for path in (*policy.visible_roots, *policy.output_roots, *policy.runtime_read_roots)
    )
    lines.extend([")", "(allow file-read-metadata file-test-existence"])
    lines.extend(
        f"  (path-ancestors {_sbpl_path(path)})"
        for path in (*policy.visible_roots, *policy.output_roots, *policy.runtime_read_roots)
    )
    lines.extend([")", "(allow file-write*"])
    lines.extend(f"  (subpath {_sbpl_path(path)})" for path in policy.output_roots)
    lines.extend([")", "(allow signal (target self))", ""])
    text = "\n".join(lines)
    return CompiledProfile(
        text=text,
        sha256=_sha256_bytes(text.encode()),
        policy_id=_sha256_bytes(_canonical_bytes(policy.canonical_dict())),
    )


def _case_result(
    case_id: str,
    result: subprocess.CompletedProcess[bytes],
    sentinel: bytes,
    *,
    expected: str,
    extra_pass: bool = True,
) -> dict[str, Any]:
    exposed = sentinel in result.stdout or sentinel in result.stderr
    if expected == "allow":
        denied = False
        passed = result.returncode == 0 and not exposed and extra_pass
    elif expected == "deny":
        denied = result.returncode != 0 and not exposed
        passed = denied and extra_pass
    elif expected == "baseline-exposure":
        denied = False
        passed = result.returncode == 0 and exposed and extra_pass
    else:  # pragma: no cover - controller constant
        raise AssertionError(f"unknown expectation {expected}")
    return {
        "denied": denied,
        "expected": expected,
        "id": case_id,
        "passed": passed,
        "returncode": result.returncode,
        "sentinel_exposed": exposed,
        "stderr_sha256": _sha256_bytes(result.stderr),
        "stdout_sha256": _sha256_bytes(result.stdout),
    }


def _run(
    command: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            env=env,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfinementError(
            f"capability command failed to execute: {command[0]}: {exc}"
        ) from exc


def _wrapped(profile_path: Path, command: list[str]) -> list[str]:
    return [str(SANDBOX_EXEC), "-f", str(profile_path), *command]


def _controller_command(
    command: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    """Run synthetic-fixture setup outside the subject sandbox and fail loudly."""
    result = _run(command, cwd=cwd, env=env)
    if result.returncode != 0:
        raise ConfinementError(
            "controller fixture command failed: "
            f"{command[0]} rc={result.returncode} "
            f"stdout_sha256={_sha256_bytes(result.stdout)} "
            f"stderr_sha256={_sha256_bytes(result.stderr)}"
        )
    return result


def _apple_git_runtime(*, cwd: Path) -> tuple[Path, Path]:
    """Resolve the selected Apple developer tree and its real Git executable."""
    developer_result = _controller_command(["/usr/bin/xcode-select", "-p"], cwd=cwd)
    developer_root = Path(developer_result.stdout.decode().strip()).resolve()
    git_result = _controller_command(["/usr/bin/xcrun", "--find", "git"], cwd=cwd)
    git = Path(git_result.stdout.decode().strip()).resolve()
    if not developer_root.is_dir() or not git.is_file() or not _contains(developer_root, git):
        raise ConfinementError("Apple developer Git runtime is unresolved or inconsistent")
    return git, developer_root


def _initialize_git_fixture(
    repository: Path,
    filename: str,
    content: bytes,
    *,
    git: Path,
    env: dict[str, str],
) -> str:
    """Create one isolated synthetic commit and return the committed blob id."""
    repository.mkdir()
    (repository / filename).write_bytes(content)
    git_command = str(git)
    _controller_command([git_command, "init", "--quiet"], cwd=repository, env=env)
    _controller_command([git_command, "add", "--", filename], cwd=repository, env=env)
    _controller_command(
        [
            git_command,
            "-c",
            "user.name=AISLE synthetic controller",
            "-c",
            "user.email=synthetic-controller@invalid",
            "commit",
            "--quiet",
            "-m",
            "synthetic confinement fixture",
        ],
        cwd=repository,
        env=env,
    )
    result = _controller_command(
        [git_command, "rev-parse", f"HEAD:{filename}"], cwd=repository, env=env
    )
    object_id = result.stdout.decode("ascii").strip()
    if len(object_id) not in (40, 64) or any(char not in "0123456789abcdef" for char in object_id):
        raise ConfinementError("controller fixture returned an invalid git object id")
    return object_id


def _platform_record() -> dict[str, str]:
    mac_version, _, machine = platform.mac_ver()
    try:
        build = subprocess.run(
            ["/usr/bin/sw_vers", "-buildVersion"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        build = "unresolved"
    return {
        "build": build,
        "machine": machine or platform.machine(),
        "macos_version": mac_version,
        "python": platform.python_version(),
    }


def run_macos_capability_audit() -> dict[str, Any]:
    """Run the synthetic deny/allow matrix through the external adapter."""
    if sys.platform != "darwin":
        raise ConfinementError(
            "macOS confinement capability requires macOS; no simulation accepted"
        )
    if not SANDBOX_EXEC.is_file() or not os.access(SANDBOX_EXEC, os.X_OK):
        raise ConfinementError(f"required adapter is unavailable: {SANDBOX_EXEC}")
    if not SYSTEM_PROFILE.is_file():
        raise ConfinementError(f"required imported profile is unavailable: {SYSTEM_PROFILE}")

    with tempfile.TemporaryDirectory(prefix="aisle-confinement-capability-") as temporary:
        root = Path(temporary).resolve()
        visible = root / "visible"
        output = root / "output"
        hidden = root / "hidden"
        for path in (visible, output, hidden):
            path.mkdir()
        visible_sentinel = b"VISIBLE-SYNTHETIC-CAPABILITY\n"
        hidden_sentinel = b"HIDDEN-SYNTHETIC-CAPABILITY-7E4E\n"
        visible_file = visible / "allowed.txt"
        hidden_file = hidden / "secret.txt"
        visible_file.write_bytes(visible_sentinel)
        hidden_file.write_bytes(hidden_sentinel)
        (visible / "hidden-link").symlink_to(hidden, target_is_directory=True)

        git, developer_root = _apple_git_runtime(cwd=root)
        isolated_home = visible / "isolated-home"
        isolated_home.mkdir()
        git_environment = {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(isolated_home),
            "PATH": str(git.parent),
        }
        visible_repository = visible / "repository"
        _initialize_git_fixture(
            visible_repository,
            "allowed.txt",
            visible_sentinel,
            git=git,
            env=git_environment,
        )
        hidden_repository = hidden / "evaluator-repository"
        hidden_object_id = _initialize_git_fixture(
            hidden_repository,
            "secret.txt",
            hidden_sentinel,
            git=git,
            env=git_environment,
        )
        hidden_worktree = hidden / "evaluator-alternate-worktree"
        _controller_command(
            [
                str(git),
                "worktree",
                "add",
                "--quiet",
                "--detach",
                str(hidden_worktree),
                "HEAD",
            ],
            cwd=hidden_repository,
            env=git_environment,
        )

        policy = MacOSPolicy(
            visible_roots=(visible,),
            output_roots=(output,),
            runtime_read_roots=(
                Path("/Library/Apple").resolve(),
                Path("/System").resolve(),
                Path("/bin").resolve(),
                Path("/usr/bin").resolve(),
                Path("/usr/lib").resolve(),
                Path("/usr/share").resolve(),
                developer_root,
            ),
            allowed_executables=(
                Path("/bin/bash").resolve(),
                Path("/bin/cat").resolve(),
                git,
            ),
            hidden_roots=(hidden,),
            network_policy="deny-external",
        )
        compiled = compile_macos_profile(policy)
        profile_path = root / "controller-profile.sb"
        profile_path.write_text(compiled.text, encoding="utf-8")

        cases: list[dict[str, Any]] = []
        baseline = _run(["/bin/cat", str(hidden_file)], cwd=visible)
        cases.append(
            _case_result(
                "unrestricted_hidden_baseline",
                baseline,
                hidden_sentinel,
                expected="baseline-exposure",
            )
        )
        unrestricted_worktree = _run(
            [str(git), "-C", str(hidden_worktree), "show", "HEAD:secret.txt"],
            cwd=visible,
            env=git_environment,
        )
        cases.append(
            _case_result(
                "unrestricted_alternate_worktree_baseline",
                unrestricted_worktree,
                hidden_sentinel,
                expected="baseline-exposure",
            )
        )
        unrestricted_object = _run(
            [
                str(git),
                "--git-dir",
                str(hidden_repository / ".git"),
                "cat-file",
                "blob",
                hidden_object_id,
            ],
            cwd=visible,
            env=git_environment,
        )
        cases.append(
            _case_result(
                "unrestricted_git_object_baseline",
                unrestricted_object,
                hidden_sentinel,
                expected="baseline-exposure",
            )
        )
        visible_read = _run(_wrapped(profile_path, ["/bin/cat", str(visible_file)]), cwd=visible)
        cases.append(
            _case_result(
                "visible_read",
                visible_read,
                hidden_sentinel,
                expected="allow",
                extra_pass=visible_read.stdout == visible_sentinel,
            )
        )
        visible_shell = _run(
            _wrapped(
                profile_path, ["/bin/bash", "-c", f"/bin/cat {shlex.quote(str(visible_file))}"]
            ),
            cwd=visible,
        )
        cases.append(
            _case_result(
                "subprocess_visible_read",
                visible_shell,
                hidden_sentinel,
                expected="allow",
                extra_pass=visible_shell.stdout == visible_sentinel,
            )
        )
        visible_git_object = _run(
            _wrapped(
                profile_path,
                [
                    str(git),
                    "-C",
                    str(visible_repository),
                    "show",
                    "HEAD:allowed.txt",
                ],
            ),
            cwd=visible,
            env=git_environment,
        )
        cases.append(
            _case_result(
                "visible_git_object_read",
                visible_git_object,
                hidden_sentinel,
                expected="allow",
                extra_pass=visible_git_object.stdout == visible_sentinel,
            )
        )

        denied_reads = {
            "absolute_hidden_read": str(hidden_file),
            "parent_traversal_hidden_read": str(visible / ".." / "hidden" / "secret.txt"),
            "symlink_hidden_read": str(visible / "hidden-link" / "secret.txt"),
        }
        for case_id, path in denied_reads.items():
            result = _run(_wrapped(profile_path, ["/bin/cat", path]), cwd=visible)
            cases.append(_case_result(case_id, result, hidden_sentinel, expected="deny"))

        shell_hidden = _run(
            _wrapped(
                profile_path, ["/bin/bash", "-c", f"/bin/cat {shlex.quote(str(hidden_file))}"]
            ),
            cwd=visible,
        )
        cases.append(
            _case_result("subprocess_hidden_read", shell_hidden, hidden_sentinel, expected="deny")
        )

        alternate_worktree_hidden = _run(
            _wrapped(
                profile_path,
                [
                    str(git),
                    "-C",
                    str(hidden_worktree),
                    "show",
                    "HEAD:secret.txt",
                ],
            ),
            cwd=visible,
            env=git_environment,
        )
        cases.append(
            _case_result(
                "alternate_worktree_hidden_read",
                alternate_worktree_hidden,
                hidden_sentinel,
                expected="deny",
            )
        )
        git_object_hidden = _run(
            _wrapped(
                profile_path,
                [
                    str(git),
                    "--git-dir",
                    str(hidden_repository / ".git"),
                    "cat-file",
                    "blob",
                    hidden_object_id,
                ],
            ),
            cwd=visible,
            env=git_environment,
        )
        cases.append(
            _case_result(
                "git_object_hidden_read",
                git_object_hidden,
                hidden_sentinel,
                expected="deny",
            )
        )

        output_file = output / "declared.txt"
        output_write = _run(
            _wrapped(
                profile_path,
                ["/bin/bash", "-c", f"printf declared-output > {shlex.quote(str(output_file))}"],
            ),
            cwd=visible,
        )
        cases.append(
            _case_result(
                "declared_output_write",
                output_write,
                hidden_sentinel,
                expected="allow",
                extra_pass=output_file.read_text(encoding="utf-8") == "declared-output"
                if output_file.exists()
                else False,
            )
        )
        forbidden_write = _run(
            _wrapped(
                profile_path,
                [
                    "/bin/bash",
                    "-c",
                    f"printf forbidden-output > {shlex.quote(str(hidden / 'forbidden.txt'))}",
                ],
            ),
            cwd=visible,
        )
        cases.append(
            _case_result("hidden_write", forbidden_write, hidden_sentinel, expected="deny")
        )

        denial_cases = [row for row in cases if row["expected"] == "deny"]
        allow_cases = [row for row in cases if row["expected"] == "allow"]
        baseline_cases = [row for row in cases if row["expected"] == "baseline-exposure"]
        denial_passes = sum(bool(row["passed"]) for row in denial_cases)
        false_alarms = sum(not bool(row["passed"]) for row in allow_cases)
        capability_pass = all(bool(row["passed"]) for row in cases)
        recorded_at = datetime.now(UTC)
        return {
            "adapter": {
                "compiled_profile_sha256": compiled.sha256,
                "imported_system_profile": str(SYSTEM_PROFILE),
                "imported_system_profile_sha256": _sha256_file(SYSTEM_PROFILE),
                "path": str(SANDBOX_EXEC),
                "policy_id": compiled.policy_id,
                "sha256": _sha256_file(SANDBOX_EXEC),
            },
            "capability_pass": capability_pass,
            "cases": cases,
            "confirmatory_ready": False,
            "evidence_class": EVIDENCE_CLASS,
            "limitations": [
                "macOS-only capability; no Linux adapter evaluated",
                "synthetic filesystem sentinels; no benchmark fault identities",
                "no vendor network or credential path evaluated",
                "no Claude/Codex end-to-end parity evaluated",
                "Git surfaces cover the system Git CLI only, not every future allowed tool",
                "Apple system.sb is a private interface and is hashed per audit",
            ],
            "platform": _platform_record(),
            "policy": policy.canonical_dict(),
            "ephemeral_profile_path": str(profile_path),
            "recorded_at": recorded_at.isoformat(),
            "schema_version": SCHEMA_VERSION,
            "session_id": (
                f"macos-capability-{recorded_at.strftime('%Y%m%dT%H%M%S%fZ')}-"
                f"{compiled.sha256[:12]}"
            ),
            "summary": {
                "baseline_tests": len(baseline_cases),
                "capability_pass": capability_pass,
                "declared_allow_tests": len(allow_cases),
                "denial_detection_rate": denial_passes / len(denial_cases),
                "denial_tests": len(denial_cases),
                "false_alarm_rate": false_alarms / len(allow_cases),
            },
        }


def write_macos_capability_audit(output: Path) -> dict[str, Any]:
    """Retain one unscored audit without overwriting earlier evidence."""
    output = Path(output)
    if output.exists():
        raise ConfinementError(f"capability audit already exists: {output}")
    report = run_macos_capability_audit()
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    except FileExistsError as exc:
        raise ConfinementError(f"capability audit already exists: {output}") from exc
    return report


def wrap_verified_command(
    command: list[str],
    compiled: CompiledProfile,
    profile_path: Path,
    attestation: dict[str, Any],
    *,
    purpose: str = "unscored_capability",
) -> list[str]:
    """Bind an unscored command to the exact externally verified adapter."""
    if purpose != "unscored_capability":
        raise ConfinementError(
            "this macOS capability attestation cannot authorize confirmatory work"
        )
    if not command or not all(isinstance(item, str) and item for item in command):
        raise ConfinementError("command must be a non-empty argv list")
    if attestation.get("schema_version") != SCHEMA_VERSION:
        raise ConfinementError("adapter attestation schema is unsupported")
    if attestation.get("evidence_class") != EVIDENCE_CLASS:
        raise ConfinementError("adapter attestation evidence class is unsupported")
    if attestation.get("confirmatory_ready") is not False:
        raise ConfinementError("capability attestation has an ambiguous confirmatory marker")
    if not attestation.get("capability_pass"):
        raise ConfinementError("adapter capability did not pass")
    adapter = attestation.get("adapter")
    if not isinstance(adapter, dict):
        raise ConfinementError("adapter attestation is absent")
    if adapter.get("compiled_profile_sha256") != compiled.sha256:
        raise ConfinementError("adapter profile hash does not match compiled profile")
    if adapter.get("policy_id") != compiled.policy_id:
        raise ConfinementError("adapter policy id does not match compiled policy")
    cases = attestation.get("cases")
    if (
        not isinstance(cases, list)
        or any(not isinstance(row, dict) for row in cases)
        or {row.get("id") for row in cases} != _REQUIRED_CASE_IDS
        or any(not row.get("passed") for row in cases)
    ):
        raise ConfinementError("adapter attestation contains a failed case")

    profile_path = Path(profile_path)
    if _sha256_file(profile_path) != compiled.sha256:
        raise ConfinementError("retained profile hash does not match compiled profile")
    adapter_path = Path(str(adapter.get("path", "")))
    if not adapter_path.is_file() or not os.access(adapter_path, os.X_OK):
        raise ConfinementError("attested adapter executable is unavailable")
    if adapter.get("sha256") != _sha256_file(adapter_path):
        raise ConfinementError("attested adapter binary hash has drifted")
    system_profile = Path(str(adapter.get("imported_system_profile", "")))
    if adapter.get("imported_system_profile_sha256") != _sha256_file(system_profile):
        raise ConfinementError("attested imported system profile hash has drifted")
    return [str(adapter_path), "-f", str(profile_path), *command]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the unscored SPEC 420 confinement audit")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit-macos")
    audit.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = write_macos_capability_audit(args.output)
    except ConfinementError as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2
    if not report["capability_pass"]:
        print(
            json.dumps(
                {"error": "confinement capability cases failed", "ok": False}, sort_keys=True
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "capability_pass": report["capability_pass"],
                "confirmatory_ready": report["confirmatory_ready"],
                "evidence_class": report["evidence_class"],
                "ok": True,
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess test
    raise SystemExit(main())
