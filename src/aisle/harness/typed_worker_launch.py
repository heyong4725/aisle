"""Verified launch boundary for typed node workers; session binding is caller-owned."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from pathlib import Path

from aisle.harness.matched_runtime import verify_runtime
from aisle.harness.treatment_ambient import spawn_isolated_process, verify_declared_environment
from aisle.harness.treatment_confinement import compile_macos_profile, wrap_verified_command
from aisle.harness.typed_execution_bundle import PARTICIPANT_FILES, verify_execution_bundle
from aisle.harness.typed_node_supervisor import supervise_typed_worker
from aisle.harness.typed_node_worker import validate_configuration

_BOOTSTRAP = (
    "import sys; sys.path[0:0] = sys.argv[1:]; "
    "from aisle.harness.typed_node_worker import serve; "
    "raise SystemExit(serve(sys.stdin.buffer, sys.stdout.buffer, sys.stderr))"
)


class TypedLaunchError(ValueError):
    """Typed worker launch inputs are invalid or have drifted."""


def _json(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")


def verify_typed_launch(
    *,
    bundle,
    bundle_manifest,
    source_roots,
    policy,
    python,
    python_sha256,
    runtime_record,
    environment,
    environment_record,
):
    """Verify exact read/execute grants and immutable launch assets before spawn."""
    bundle, python = (Path(p).absolute() for p in (bundle, python))
    verify_execution_bundle(bundle, bundle_manifest)
    compiled = compile_macos_profile(policy)
    verify_runtime(runtime_record)
    runtime_roots = {Path(p) for p in runtime_record["trees"]}
    if set(policy.runtime_read_roots) - {Path("/System"), Path("/usr/lib")} != runtime_roots:
        raise TypedLaunchError("worker runtime grants differ from bound trees")
    if not any(python.resolve().is_relative_to(p) for p in runtime_roots):
        raise TypedLaunchError("worker interpreter is outside bound runtime")
    if set(policy.visible_roots) != {bundle}:
        raise TypedLaunchError("worker visible roots must be exactly the execution bundle")
    if policy.network_policy != "deny-external":
        raise TypedLaunchError("worker external networking must be denied")
    verify_declared_environment(environment, environment_record)
    home = Path(environment_record["home"])
    if set(policy.output_roots) != {home} or any(
        not Path(p).is_relative_to(home) for p in environment_record["state_directories"]
    ):
        raise TypedLaunchError("worker writes must remain in its private HOME")
    if any(
        read.is_relative_to(home) or home.is_relative_to(read) for read in (*runtime_roots, bundle)
    ):
        raise TypedLaunchError("worker immutable inputs overlap write authority")
    if not source_roots or any(
        Path(root).resolve() != Path(root).absolute()
        or not any(Path(root).is_relative_to(hidden) for hidden in policy.hidden_roots)
        for root in source_roots
    ):
        raise TypedLaunchError("worker source roots require canonical hidden bindings")
    if set(policy.allowed_executables) != {python.resolve()} or not os.access(python, os.X_OK):
        raise TypedLaunchError("worker requires exactly its bound interpreter")
    if hashlib.sha256(python.read_bytes()).hexdigest() != python_sha256:
        raise TypedLaunchError("worker interpreter identity drift")
    return compiled


def launch_typed_worker(
    *,
    bundle,
    bundle_manifest,
    source_roots,
    policy,
    profile_path,
    attestation,
    python,
    python_sha256,
    environment,
    environment_record,
    runtime_record,
    output,
    node,
    module,
    outputs,
    timeout_s,
    max_calls=100000,
    configuration=None,
):
    """Launch one authored script and retain host-observed supervision evidence.

    This function requires adapter evidence but does not create that evidence or
    authorize study collection. The caller owns outer resource limits, including
    bounds on trusted host event iteration and native decoding.
    """
    bundle, output, python = (Path(p).absolute() for p in (bundle, output, python))
    readable = (*policy.visible_roots, *policy.runtime_read_roots, *policy.output_roots)
    if output.resolve() != output or any(
        output.is_relative_to(p) or p.is_relative_to(output) for p in readable
    ):
        raise TypedLaunchError("worker evidence path is redirected or worker-readable")
    if not any(output.is_relative_to(p) for p in policy.hidden_roots):
        raise TypedLaunchError("worker evidence requires a hidden-root binding")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    record = {
        "schema_version": "aisle.typed-worker-launch-result.v1",
        "classification": "infrastructure_exclusion",
        "ok": False,
        "confirmatory_ready": False,
        "stage": "preflight",
    }
    try:
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise TypedLaunchError("worker timeout must be finite and positive")
        if type(max_calls) is not int or max_calls <= 0:
            raise TypedLaunchError("worker call budget must be a positive integer")
        modules = {p[4:-3].replace("/", "."): p for p in PARTICIPANT_FILES}
        if type(module) is not str or module not in modules:
            raise TypedLaunchError("worker module is outside the authored Python surface")
        if type(outputs) not in (set, frozenset) or any(
            type(name) is not str or not name or name == "turn_done" for name in outputs
        ):
            raise TypedLaunchError("worker output grants are invalid")
        kwargs = dict(
            bundle=bundle,
            bundle_manifest=bundle_manifest,
            source_roots=source_roots,
            policy=policy,
            python=python,
            python_sha256=python_sha256,
            runtime_record=runtime_record,
            environment=environment,
            environment_record=environment_record,
        )
        configuration = validate_configuration(
            {"environment": {}, "arguments": []} if configuration is None else configuration
        )
        compiled = verify_typed_launch(**kwargs)
        source = (bundle / modules[module]).read_bytes().decode("utf-8")
        profile = output / "profile.sb"
        with profile.open("xb") as stream:
            stream.write(Path(profile_path).read_bytes())
        command = [
            str(python),
            "-I",
            "-B",
            "-c",
            _BOOTSTRAP,
            str(bundle / "src"),
            *sorted(runtime_record["trees"]),
        ]
        wrapped = wrap_verified_command(command, compiled, profile, attestation)
        for name, value in (
            ("bundle", bundle_manifest),
            ("runtime", runtime_record),
            ("capability", attestation),
            (
                "launch",
                {
                    "argv": command,
                    "wrapped_argv": wrapped,
                    "cwd": str(bundle),
                    "module": module,
                    "configuration": configuration,
                    "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "outputs": sorted(outputs),
                    "timeout_s": timeout_s,
                    "max_calls": max_calls,
                    "python_sha256": python_sha256,
                    "policy_id": compiled.policy_id,
                    "profile_sha256": compiled.sha256,
                    "environment_record": environment_record,
                },
            ),
        ):
            _json(output / f"{name}.json", value)
        verify_typed_launch(**kwargs)
        wrapped = wrap_verified_command(command, compiled, profile, attestation)
        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise TypedLaunchError("worker wall budget exhausted before spawn")
        record["stage"] = "worker"
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
            record["worker"] = supervise_typed_worker(
                process,
                node,
                source,
                module,
                output / "rpc",
                outputs=outputs,
                timeout_s=remaining,
                max_calls=max_calls,
                configuration=configuration,
            )
        record["stage"] = "postflight"
        verify_typed_launch(**kwargs)
        record.update(
            classification=record["worker"]["classification"],
            ok=record["worker"]["ok"],
            stage="done",
        )
    except BaseException as exc:
        record["error"] = str(exc) or type(exc).__name__
        if not isinstance(exc, Exception):
            raise
    finally:
        record["elapsed_s"] = time.monotonic() - started
        _json(output / "result.json", record)
    return record
