"""Observe a Python-only worker policy using disposable, unscored fixtures."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from aisle.harness.treatment_ambient import verify_declared_environment
from aisle.harness.treatment_confinement import (
    _REQUIRED_CASE_IDS,
    EVIDENCE_CLASS,
    SANDBOX_EXEC,
    SCHEMA_VERSION,
    SYSTEM_PROFILE,
    _apple_git_runtime,
    compile_macos_profile,
)
from aisle.harness.typed_snapshot import _read
from aisle.harness.worker_authority_probe import operation_case, operation_command
from aisle.harness.worker_network_probe import _capture, probe_worker_network


def audit_worker_capability(
    *, policy, profile_path, python, environment, environment_record, output
):
    """Retain all required actual-profile observations; never authorize confirmatory work."""
    if sys.platform != "darwin":
        raise ValueError("worker capability requires actual macOS sandbox-exec")
    compiled = compile_macos_profile(policy)
    python = Path(python).resolve(strict=True)
    profile_path, output = Path(profile_path).absolute(), Path(output).absolute()
    if set(policy.allowed_executables) != {python}:
        raise ValueError("worker capability requires a Python-only executable grant")
    if hashlib.sha256(_read(profile_path.parent, profile_path.name)).hexdigest() != compiled.sha256:
        raise ValueError("worker capability profile differs from policy")
    readable = (*policy.visible_roots, *policy.output_roots, *policy.runtime_read_roots)
    if (
        output.resolve() != output
        or any(output.is_relative_to(p) or p.is_relative_to(output) for p in readable)
        or not any(output.is_relative_to(p) for p in policy.hidden_roots)
    ):
        raise ValueError("capability evidence must be private and disjoint from worker authority")
    if not policy.visible_roots or not policy.output_roots:
        raise ValueError("worker capability requires visible and writable fixture roots")
    environment, environment_record = dict(environment), copy.deepcopy(environment_record)
    verify_declared_environment(environment, environment_record)
    identity_paths = (
        python,
        SANDBOX_EXEC,
        SYSTEM_PROFILE,
        Path("/usr/bin/true"),
        profile_path,
        Path(__file__),
        Path(__file__).with_name("worker_authority_probe.py"),
        Path(__file__).with_name("worker_network_probe.py"),
    )
    identities = {
        str(p): hashlib.sha256(_read(p.parent, p.name)).hexdigest() for p in identity_paths
    }
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": SCHEMA_VERSION,
        "evidence_class": EVIDENCE_CLASS,
        "confirmatory_ready": False,
        "capability_pass": False,
        "policy": policy.canonical_dict(),
        "identities": identities,
        "environment_record": environment_record,
        "cases": [],
        "controls": [],
        "setup": [],
        "cleanup_errors": [],
        "error": None,
        "limitations": [
            "Unscored synthetic fixtures; no study or participant parity evidence",
            "Loose Git blobs, selected filesystem routes, TCP/Unix sockets and one executable",
            "No exhaustive IPC, descendant-resource or runtime closure proof",
        ],
        "adapter": {
            "path": str(SANDBOX_EXEC),
            "sha256": identities[str(SANDBOX_EXEC)],
            "compiled_profile_sha256": compiled.sha256,
            "policy_id": compiled.policy_id,
            "imported_system_profile": str(SYSTEM_PROFILE),
            "imported_system_profile_sha256": identities[str(SYSTEM_PROFILE)],
        },
    }
    owned = []
    try:
        for parent in (policy.visible_roots[0], policy.output_roots[0]):
            path = Path(tempfile.mkdtemp(prefix="aisle-capability-", dir=parent))
            stat = path.stat()
            owned.append((path, (stat.st_dev, stat.st_ino)))
        visible, writable = (item[0] for item in owned)
        hidden = output / "fixtures"
        hidden.mkdir()
        setup_root = output / "setup"
        setup_root.mkdir()
        git, _ = _apple_git_runtime(cwd=hidden)
        identities[str(git)] = hashlib.sha256(_read(git.parent, git.name)).hexdigest()
        git_home = hidden / "git-home"
        git_home.mkdir()
        git_env = {
            "HOME": str(git_home),
            "PATH": str(git.parent),
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        }

        def setup(argv, cwd):
            index = str(len(report["setup"]))
            destination = setup_root / index
            destination.mkdir()
            (destination / "invocation.json").write_text(
                json.dumps(
                    {
                        "argv": argv,
                        "cwd": str(cwd),
                        "environment": git_env,
                    }
                )
            )
            result = subprocess.run(
                argv,
                cwd=cwd,
                env=git_env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
            )
            (destination / "stdout").write_bytes(result.stdout)
            (destination / "stderr").write_bytes(result.stderr)
            (destination / "process.json").write_text(json.dumps({"returncode": result.returncode}))
            report["setup"].append({"capture": f"setup/{index}", "returncode": result.returncode})
            if result.returncode:
                raise RuntimeError("capability Git fixture setup failed")
            return result.stdout.decode().strip()

        def repository(path, sentinel):
            path.mkdir()
            (path / "data").write_bytes(sentinel)
            setup([str(git), "init", "--quiet", "--template="], path)
            setup([str(git), "add", "--", "data"], path)
            setup(
                [
                    str(git),
                    "-c",
                    "user.name=AISLE synthetic controller",
                    "-c",
                    "user.email=synthetic-controller@invalid",
                    "commit",
                    "--quiet",
                    "-m",
                    "synthetic worker capability fixture",
                ],
                path,
            )
            oid = setup([str(git), "rev-parse", "HEAD:data"], path)
            if len(oid) not in (40, 64) or any(c not in "0123456789abcdef" for c in oid):
                raise ValueError("invalid fixture object identity")
            return path / ".git/objects" / oid[:2] / oid[2:]

        secret, public = secrets.token_bytes(32), secrets.token_bytes(32)
        hidden_file, visible_file = hidden / "data", visible / "data"
        hidden_file.write_bytes(secret)
        visible_file.write_bytes(public)
        visible_blob = repository(visible / "repository", public)
        hidden_repository = hidden / "repository"
        hidden_blob = repository(hidden_repository, secret)
        alternate = hidden / "alternate"
        setup(
            [str(git), "worktree", "add", "--quiet", "--detach", str(alternate), "HEAD"],
            hidden_repository,
        )
        link = visible / "hidden-link"
        link.symlink_to(hidden_file)
        traversal = visible / os.path.relpath(hidden_file, visible)
        (output / "observations").mkdir()

        def observe(name, operation, target, sentinel, *, confined, allowed, control=False):
            capture = f"observations/{'control-' if control else ''}{name}"
            command = operation_command(python, operation, target, sentinel)
            argv = ([str(SANDBOX_EXEC), "-f", str(profile_path)] if confined else []) + command
            result = _capture(argv, visible, environment, environment_record, output / capture)
            case = operation_case(result, operation, sentinel, allowed=allowed)
            case.update(id=name, capture=capture)
            if operation == "write":
                if allowed:
                    case["passed"] = (
                        case["passed"] and target.is_file() and target.read_bytes() == sentinel
                    )
                    if target.is_file() and not target.is_symlink():
                        target.unlink()
                else:
                    case["passed"] = (
                        case["passed"] and not target.exists() and not target.is_symlink()
                    )
            report["controls" if control else "cases"].append(case)

        cases = [
            ("unrestricted_hidden_baseline", "read", hidden_file, secret, False, True),
            ("unrestricted_git_object_baseline", "git_blob", hidden_blob, secret, False, True),
            (
                "unrestricted_alternate_worktree_baseline",
                "read",
                alternate / "data",
                secret,
                False,
                True,
            ),
            ("unrestricted_exec_baseline", "exec", Path("/usr/bin/true"), public, False, True),
            ("visible_read", "read", visible_file, public, True, True),
            ("visible_git_object_read", "git_blob", visible_blob, public, True, True),
            ("subprocess_visible_read", "subprocess_read", visible_file, public, True, True),
            ("declared_output_write", "write", writable / "written", public, True, True),
            ("absolute_hidden_read", "read", hidden_file, secret, True, False),
            ("git_object_hidden_read", "git_blob", hidden_blob, secret, True, False),
            ("alternate_worktree_hidden_read", "read", alternate / "data", secret, True, False),
            ("parent_traversal_hidden_read", "read", traversal, secret, True, False),
            ("symlink_hidden_read", "read", link, secret, True, False),
            ("subprocess_hidden_read", "subprocess_read", hidden_file, secret, True, False),
            ("hidden_write", "write", hidden / "written", secret, True, False),
            ("unlisted_executable", "exec", Path("/usr/bin/true"), public, True, False),
        ]
        for name, operation, target, sentinel, confined, allowed in cases:
            if not allowed:
                observe(
                    name, operation, target, sentinel, confined=False, allowed=True, control=True
                )
            observe(name, operation, target, sentinel, confined=confined, allowed=allowed)
        for transport in ("tcp", "unix"):
            directory = "network" if transport == "tcp" else "unix-network"
            network = probe_worker_network(
                policy=policy,
                profile_path=profile_path,
                python=python,
                environment=environment,
                environment_record=environment_record,
                cwd=visible,
                output=output / directory,
                sentinel=secrets.token_bytes(32),
                transport=transport,
            )
            captures = (
                ("baseline", "confined")
                if transport == "tcp"
                else ("baseline", "confined", "network-control")
            )
            for case, capture in zip(network["cases"], captures, strict=False):
                report["cases"].append({**case, "capture": f"{directory}/{capture}"})
            if not network["ok"]:
                raise RuntimeError(f"worker {transport} network capability failed")
        for name, digest in identities.items():
            path = Path(name)
            if hashlib.sha256(_read(path.parent, path.name)).hexdigest() != digest:
                raise ValueError("worker capability identity changed during audit")
    except BaseException as exc:
        report["error"] = str(exc) or type(exc).__name__
        if not isinstance(exc, Exception):
            raise
    finally:
        for path, identity in reversed(owned):
            try:
                stat = path.lstat()
                if path.resolve() != path or (stat.st_dev, stat.st_ino) != identity:
                    raise ValueError("owned capability fixture root was replaced")
                shutil.rmtree(path)
            except Exception as exc:
                report["cleanup_errors"].append(str(exc) or type(exc).__name__)
        report["capability_pass"] = (
            report["error"] is None
            and not report["cleanup_errors"]
            and len(report["cases"]) == len(_REQUIRED_CASE_IDS)
            and {row["id"] for row in report["cases"]} == _REQUIRED_CASE_IDS
            and all(row["passed"] for row in (*report["cases"], *report["controls"]))
        )
        (output / "report.json").write_text(json.dumps(report, allow_nan=False))
    return report
