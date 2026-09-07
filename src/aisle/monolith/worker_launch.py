"""Unscored worker launch using an exact code bundle and verified adapter inputs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path

from aisle.harness.matched_runtime import verify_runtime
from aisle.harness.treatment_ambient import spawn_isolated_process, verify_declared_environment
from aisle.harness.treatment_confinement import compile_macos_profile, wrap_verified_command
from aisle.monolith.supervisor import WorkerFailure, WorkerSupervisor

BUNDLE_FILES = (
    "aisle/__init__.py",
    "aisle/monolith/confinement.py",
    "aisle/monolith/primitive_api.py",
    "aisle/monolith/proxy.py",
    "aisle/monolith/wire.py",
    "aisle/monolith/worker.py",
)
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP = (
    "import sys; sys.path[0:0] = sys.argv[1:]; "
    "from aisle.monolith.worker import serve; "
    "raise SystemExit(serve(sys.stdin.buffer, sys.stdout.buffer, sys.stderr))"
)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _manifest():
    record = {
        "schema_version": "aisle.monolith.worker-bundle.v1",
        "files": {
            name: {
                "sha256": hashlib.sha256((_SOURCE_ROOT / name).read_bytes()).hexdigest(),
                "mode": 0o444,
            }
            for name in BUNDLE_FILES
        },
    }
    return {**record, "immutable_id": _digest(record)}


def build_worker_bundle(output):
    """Copy only worker-side implementations into a new standalone package tree."""
    output = Path(output).absolute()
    if output.resolve() != output:
        raise WorkerFailure("worker bundle path is redirected")
    manifest = _manifest()
    output.mkdir(parents=True, exist_ok=False)
    for name in BUNDLE_FILES:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write((_SOURCE_ROOT / name).read_bytes())
        target.chmod(0o444)
    verify_worker_bundle(output, manifest)
    return manifest


def verify_worker_bundle(bundle, manifest):
    """Bind the closed file inventory to both the manifest and this controller revision."""
    bundle = Path(bundle).absolute()
    if bundle.resolve() != bundle or not bundle.is_dir():
        raise WorkerFailure("worker bundle root is redirected or missing")
    if manifest != _manifest():
        raise WorkerFailure("worker bundle identity differs from controller sources")
    observed = {}
    for path in bundle.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise WorkerFailure("worker bundle contains a redirected or special entry")
        if stat.S_ISREG(mode):
            observed[path.relative_to(bundle).as_posix()] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mode": stat.S_IMODE(mode),
            }
    if observed != manifest["files"]:
        raise WorkerFailure("worker bundle files or modes have drifted")


def _json(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")


def verify_worker_launch(
    *,
    bundle,
    bundle_manifest,
    policy,
    python,
    python_sha256,
    runtime_record,
    source_roots,
    environment,
    environment_record,
):
    """Verify the matched worker's immutable inputs and exact read/write/execute grants."""
    bundle, python = (Path(p).absolute() for p in (bundle, python))
    verify_worker_bundle(bundle, bundle_manifest)
    compiled = compile_macos_profile(policy)
    verify_runtime(runtime_record)
    runtime_roots = {Path(p) for p in runtime_record["trees"]}
    if set(policy.runtime_read_roots) - {Path("/System"), Path("/usr/lib")} != runtime_roots:
        raise WorkerFailure("worker runtime grants differ from bound trees")
    if not any(python.resolve().is_relative_to(p) for p in runtime_roots):
        raise WorkerFailure("worker interpreter is outside bound runtime")
    if set(policy.visible_roots) != {bundle}:
        raise WorkerFailure("worker visible roots must be exactly the worker bundle")
    if policy.network_policy != "deny-external":
        raise WorkerFailure("worker external networking must be denied")
    if any(bundle.is_relative_to(p) or p.is_relative_to(bundle) for p in policy.output_roots):
        raise WorkerFailure("worker bundle has write authority")
    verify_declared_environment(environment, environment_record)
    home = Path(environment_record["home"])
    if set(policy.output_roots) != {home} or any(
        not Path(p).is_relative_to(home) for p in environment_record["state_directories"]
    ):
        raise WorkerFailure("worker writes must remain in its private HOME")
    if any(
        read.is_relative_to(home) or home.is_relative_to(read) for read in (*runtime_roots, bundle)
    ):
        raise WorkerFailure("worker immutable inputs overlap write authority")
    if not source_roots or any(
        Path(root).resolve() != Path(root).absolute()
        or not any(Path(root).is_relative_to(hidden) for hidden in policy.hidden_roots)
        for root in source_roots
    ):
        raise WorkerFailure("worker source roots require canonical hidden bindings")
    if set(policy.allowed_executables) != {python.resolve()} or not os.access(python, os.X_OK):
        raise WorkerFailure("worker requires exactly its bound interpreter")
    if hashlib.sha256(python.read_bytes()).hexdigest() != python_sha256:
        raise WorkerFailure("worker interpreter identity has drifted")
    return compiled


@contextmanager
def launch_worker(
    *,
    bundle,
    bundle_manifest,
    policy,
    profile_path,
    attestation,
    python,
    python_sha256,
    runtime_record,
    source_roots,
    environment,
    environment_record,
    output,
    primitives,
    timeout_s,
    max_primitive_calls=100000,
    max_handles=1024,
):
    """Launch and supervise one unscored worker with exact declared inputs.

    The caller must bind these inputs into session admission. Adapter capability
    evidence alone does not establish external review or study readiness.
    """
    bundle, output = Path(bundle).absolute(), Path(output).absolute()
    python = Path(python).absolute()  # Preserve a venv entry point's symlink spelling.
    if output.resolve() != output:
        raise WorkerFailure("worker evidence path is redirected")
    output.mkdir(parents=True, exist_ok=False)
    stage = "preflight"
    try:
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise WorkerFailure("worker timeout must be finite and positive")
        if any(type(n) is not int or n <= 0 for n in (max_primitive_calls, max_handles)):
            raise WorkerFailure("worker primitive budgets must be positive integers")
        inputs = dict(
            bundle=bundle,
            bundle_manifest=bundle_manifest,
            policy=policy,
            python=python,
            python_sha256=python_sha256,
            runtime_record=runtime_record,
            source_roots=source_roots,
            environment=environment,
            environment_record=environment_record,
        )
        compiled = verify_worker_launch(**inputs)
        readable = (*policy.visible_roots, *policy.runtime_read_roots, *policy.output_roots)
        if any(output.is_relative_to(p) or p.is_relative_to(output) for p in readable):
            raise WorkerFailure("worker evidence overlaps worker-readable authority")
        if not any(output.is_relative_to(p) for p in policy.hidden_roots):
            raise WorkerFailure("worker evidence has no hidden-root binding")
        retained_profile = output / "profile.sb"
        with retained_profile.open("xb") as stream:
            stream.write(Path(profile_path).read_bytes())
        command = [
            str(python),
            "-I",
            "-B",
            "-c",
            _BOOTSTRAP,
            str(bundle),
            *sorted(runtime_record["trees"]),
        ]
        wrapped = wrap_verified_command(command, compiled, retained_profile, attestation)
        _json(
            output / "launch.json",
            {
                "schema_version": "aisle.monolith.worker-launch.v1",
                "argv": command,
                "wrapped_argv": wrapped,
                "cwd": str(bundle),
                "bundle_id": bundle_manifest["immutable_id"],
                "runtime_id": runtime_record["immutable_id"],
                "python_sha256": python_sha256,
                "policy_id": compiled.policy_id,
                "profile_sha256": compiled.sha256,
                "environment_record": environment_record,
                "confirmatory_ready": False,
            },
        )
        _json(output / "runtime.json", runtime_record)
        _json(output / "bundle.json", bundle_manifest)
        _json(output / "capability.json", attestation)
        # Recheck after retaining inputs and immediately before process creation.
        verify_worker_launch(**inputs)
        wrapped = wrap_verified_command(command, compiled, retained_profile, attestation)
        stage = "launch"
        with (output / "stderr.log").open("xb") as stderr:
            process = spawn_isolated_process(
                wrapped,
                cwd=bundle,
                environment=environment,
                environment_record=environment_record,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                bufsize=0,
            )
            stage = "worker"
            try:
                with WorkerSupervisor(
                    process,
                    primitives,
                    output / "rpc",
                    timeout_s=timeout_s,
                    max_primitive_calls=max_primitive_calls,
                    max_handles=max_handles,
                ) as worker:
                    yield worker
            finally:
                # The supervisor closes/reaps before the immutable-input recheck,
                # including callers that leave the context with an exception.
                verify_worker_launch(**inputs)
    except BaseException as exc:
        _json(output / "failure.json", {"stage": stage, "error": str(exc) or type(exc).__name__})
        if isinstance(exc, Exception) and not isinstance(exc, WorkerFailure):
            raise WorkerFailure(str(exc) or type(exc).__name__) from exc
        raise
