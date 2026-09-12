"""Bound validator code for a separately confined, read-only typed check.

Building a command does not authorize executing it without the verified OS
adapter. Candidate snapshots are data; they must never enter the import path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import stat
import subprocess
import time
from pathlib import Path

from aisle.harness.matched_runtime import verify_runtime
from aisle.harness.matched_surface import record_surface
from aisle.harness.treatment_ambient import spawn_isolated_process, verify_declared_environment
from aisle.harness.treatment_confinement import (
    MacOSPolicy,
    compile_macos_profile,
    wrap_verified_command,
)
from aisle.harness.typed_snapshot import verify_typed_validation_snapshot

BUNDLE_FILES = (
    "aisle/__init__.py",
    "aisle/harness/common.py",
    "aisle/harness/registry.py",
    "aisle/harness/validate.py",
    "aisle/harness/cli.py",
    "aisle/harness/matched_surface.py",
)
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from aisle.harness.cli import main; raise SystemExit(main())"
)
_RUNTIME_BOOTSTRAP = (
    "import sys,json; sys.path[0:0] = json.loads(sys.argv.pop(1)); "
    "from aisle.harness.cli import main; raise SystemExit(main())"
)


class ValidationError(ValueError):
    """The validator code or launch declaration differs from its binding."""


def _manifest():
    record = {
        "schema_version": "aisle.typed-validation-bundle.v1",
        "files": {
            name: {
                "sha256": hashlib.sha256((_SOURCE_ROOT / name).read_bytes()).hexdigest(),
                "mode": 0o444,
            }
            for name in BUNDLE_FILES
        },
    }
    identity = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**record, "immutable_id": identity}


def build_validation_bundle(output):
    """Copy the real validate CLI's repository import closure, with no authored code."""
    output = Path(output).absolute()
    if output.resolve() != output:
        raise ValidationError("validator bundle root is redirected")
    manifest = _manifest()
    output.mkdir(parents=True, exist_ok=False)
    for name in BUNDLE_FILES:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write((_SOURCE_ROOT / name).read_bytes())
        target.chmod(0o444)
    verify_validation_bundle(output, manifest)
    return manifest


def verify_validation_bundle(bundle, manifest):
    """Check a closed inventory against both its receipt and the running controller."""
    bundle = Path(bundle).absolute()
    if bundle.resolve() != bundle or not bundle.is_dir():
        raise ValidationError("validator bundle root is redirected or missing")
    if manifest != _manifest():
        raise ValidationError("validator bundle differs from controller sources")
    observed = {}
    for path in bundle.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValidationError("validator bundle has a redirected or special entry")
        if stat.S_ISREG(mode):
            observed[path.relative_to(bundle).as_posix()] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mode": stat.S_IMODE(mode),
            }
    if observed != manifest["files"]:
        raise ValidationError("validator bundle inventory has drifted")


def _verify_interpreter_startup(python, runtime_record):
    """Bind executable selection and both locations Python searches for pyvenv.cfg."""
    python = Path(python)
    roots = tuple(Path(root) for root in runtime_record["trees"])
    if (
        not python.is_absolute()
        or python != Path(os.path.abspath(python))
        or not os.access(python, os.X_OK)
        or not any(python.resolve().is_relative_to(root) for root in roots)
    ):
        raise ValidationError("validation interpreter is outside bound runtime trees")
    for prefix in (python.parent.parent, python.resolve().parent.parent):
        if not any(prefix.is_relative_to(root) for root in roots):
            raise ValidationError("validation startup configuration is outside bound runtime trees")


def validation_command(
    python, bundle, manifest, snapshot, snapshot_record, embodiment, *, runtime_record=None
):
    """Prepare the fixed normal-validation command; caller must confine its execution."""
    if runtime_record is not None:
        verify_runtime(runtime_record)
    return _validation_command_fields(
        python,
        bundle,
        manifest,
        snapshot,
        snapshot_record,
        embodiment,
        runtime_record=runtime_record,
    )


def _validation_command_fields(
    python, bundle, manifest, snapshot, snapshot_record, embodiment, *, runtime_record=None
):
    """Check command inputs; the launch boundary separately inventories runtime bytes."""
    bundle, snapshot = Path(bundle).absolute(), Path(snapshot).absolute()
    verify_validation_bundle(bundle, manifest)
    verify_typed_validation_snapshot(snapshot, snapshot_record)
    if bundle.is_relative_to(snapshot) or snapshot.is_relative_to(bundle):
        raise ValidationError("validator code and candidate data overlap")
    if embodiment not in ("franka", "so101"):
        raise ValidationError("unsupported matched validation embodiment")
    if runtime_record is not None:
        _verify_interpreter_startup(python, runtime_record)
    return [
        str(Path(python).absolute()),
        "-I",
        "-B",
        "-c",
        _RUNTIME_BOOTSTRAP if runtime_record is not None else _BOOTSTRAP,
        (
            json.dumps([str(bundle), *sorted(runtime_record["trees"])])
            if runtime_record is not None
            else str(bundle)
        ),
        "validate",
        str(snapshot / record_surface(snapshot_record).typed_graph),
        "--root",
        str(snapshot),
        "--embodiment",
        embodiment,
    ]


def _json(path, value):
    with path.open("x") as stream:
        stream.write(json.dumps(value, indent=2, allow_nan=False) + "\n")


def verify_validation_launch(
    *,
    bundle,
    bundle_manifest,
    snapshot_storage,
    source_roots,
    policy,
    python,
    python_sha256,
    runtime_record,
    environment,
    environment_record,
):
    """Verify current runtime contents and the declared validation authority."""
    verify_runtime(runtime_record)
    return _verify_validation_launch_fields(
        bundle=bundle,
        bundle_manifest=bundle_manifest,
        snapshot_storage=snapshot_storage,
        source_roots=source_roots,
        policy=policy,
        python=python,
        python_sha256=python_sha256,
        runtime_record=runtime_record,
        environment=environment,
        environment_record=environment_record,
    )


def _verify_validation_launch_fields(
    *,
    bundle,
    bundle_manifest,
    snapshot_storage,
    source_roots,
    policy,
    python,
    python_sha256,
    runtime_record,
    environment,
    environment_record,
):
    """Verify static validation authority without executing a participant or validator."""
    bundle, snapshot_storage, python = (
        Path(p).absolute() for p in (bundle, snapshot_storage, python)
    )
    if snapshot_storage.resolve() != snapshot_storage or not snapshot_storage.is_dir():
        raise ValidationError("validation snapshot storage is redirected or not a directory")
    verify_validation_bundle(bundle, bundle_manifest)
    compiled = compile_macos_profile(policy)
    runtime_roots = {Path(p) for p in runtime_record["trees"]}
    system_roots = {Path("/System"), Path("/usr/lib")}
    if set(policy.runtime_read_roots) - system_roots != runtime_roots:
        raise ValidationError("validation runtime read grants differ from bound trees")
    _verify_interpreter_startup(python, runtime_record)
    if any(
        read.is_relative_to(write) or write.is_relative_to(read)
        for read in runtime_roots
        for write in policy.output_roots
    ):
        raise ValidationError("validation runtime has write authority")
    if policy.network_policy != "deny-external":
        raise ValidationError("validation requires denied external networking")
    if set(policy.visible_roots) != {bundle, snapshot_storage}:
        raise ValidationError("validation visible roots must be exactly code and snapshot")
    home = Path(environment_record["home"])
    if set(policy.output_roots) != {home}:
        raise ValidationError("validation writes must be confined to private HOME")
    if not source_roots or any(
        not Path(root).is_absolute()
        or Path(root).resolve() != Path(root)
        or not any(Path(root).is_relative_to(p) for p in policy.hidden_roots)
        for root in source_roots
    ):
        raise ValidationError("validation source roots require canonical hidden bindings")
    for root in (bundle, snapshot_storage):
        if any(root.is_relative_to(p) or p.is_relative_to(root) for p in policy.output_roots):
            raise ValidationError("validation code or snapshot has write authority")
    if set(policy.allowed_executables) != {python.resolve()} or not os.access(python, os.X_OK):
        raise ValidationError("validation requires exactly its bound interpreter")
    if hashlib.sha256(python.read_bytes()).hexdigest() != python_sha256:
        raise ValidationError("validation interpreter identity has drifted")
    verify_declared_environment(environment, environment_record)
    if any(not Path(p).is_relative_to(home) for p in environment_record["state_directories"]):
        raise ValidationError("validation private state escapes HOME")
    return compiled


def run_validation(
    *,
    bundle,
    bundle_manifest,
    snapshot,
    snapshot_record,
    embodiment,
    policy,
    profile_path,
    attestation,
    python,
    python_sha256,
    environment,
    environment_record,
    runtime_record,
    output,
    timeout_s,
    snapshot_storage=None,
):
    """Run one unscored check through a verified adapter, retaining terminal evidence.

    Session admission must bind these launch inputs. Capability verification is
    not independent review or permission to collect a study baseline.
    """
    bundle, snapshot, output = (Path(p).absolute() for p in (bundle, snapshot, output))
    python = Path(python).absolute()
    storage = snapshot if snapshot_storage is None else Path(snapshot_storage).absolute()
    if storage.resolve() != storage or (snapshot != storage and snapshot.parent != storage):
        raise ValidationError("validation snapshot is outside its admitted storage")
    readable = (*policy.visible_roots, *policy.runtime_read_roots, *policy.output_roots)
    if output.resolve() != output or any(
        output.is_relative_to(p) or p.is_relative_to(output) for p in readable
    ):
        raise ValidationError("validation evidence path is redirected or participant-readable")
    if not any(output.is_relative_to(p) for p in policy.hidden_roots):
        raise ValidationError("validation evidence has no hidden-root binding")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    record = {
        "schema_version": "aisle.typed-validation-result.v1",
        "snapshot_id": snapshot_record["immutable_id"],
        "embodiment": embodiment,
        "classification": "infrastructure_exclusion",
        "ok": False,
        "confirmatory_ready": False,
        "stage": "preflight",
    }
    try:
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValidationError("validation timeout must be finite and positive")
        compiled = _verify_validation_launch_fields(
            bundle=bundle,
            bundle_manifest=bundle_manifest,
            snapshot_storage=storage,
            source_roots=(snapshot_record["controller_root"], snapshot_record["participant_root"]),
            policy=policy,
            python=python,
            python_sha256=python_sha256,
            runtime_record=runtime_record,
            environment=environment,
            environment_record=environment_record,
        )
        command = _validation_command_fields(
            python,
            bundle,
            bundle_manifest,
            snapshot,
            snapshot_record,
            embodiment,
            runtime_record=runtime_record,
        )
        retained_profile = output / "profile.sb"
        with retained_profile.open("xb") as stream:
            stream.write(Path(profile_path).read_bytes())
        wrapped = wrap_verified_command(command, compiled, retained_profile, attestation)
        _json(output / "runtime.json", runtime_record)
        _json(output / "bundle.json", bundle_manifest)
        _json(output / "snapshot.json", snapshot_record)
        _json(output / "capability.json", attestation)
        _json(
            output / "launch.json",
            {
                "argv": command,
                "wrapped_argv": wrapped,
                "cwd": str(snapshot),
                "python_sha256": python_sha256,
                "environment_record": environment_record,
                "policy_id": compiled.policy_id,
                "profile_sha256": compiled.sha256,
                "timeout_s": timeout_s,
            },
        )
        validation_command(
            python,
            bundle,
            bundle_manifest,
            snapshot,
            snapshot_record,
            embodiment,
            runtime_record=runtime_record,
        )
        if hashlib.sha256(python.read_bytes()).hexdigest() != python_sha256:
            raise ValidationError("validation interpreter drifted before launch")
        wrapped = wrap_verified_command(command, compiled, retained_profile, attestation)
        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise ValidationError("validation wall budget exhausted before launch")
        record["stage"] = "process"
        with (
            (output / "stdout.json").open("xb") as stdout,
            (output / "stderr.log").open("xb") as stderr,
        ):
            process = spawn_isolated_process(
                wrapped,
                cwd=snapshot,
                environment=environment,
                environment_record=environment_record,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
            try:
                rc = process.wait(timeout=remaining)
            except BaseException as exc:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
                process.wait()
                record["process"] = {
                    "rc": process.returncode,
                    "timed_out": isinstance(exc, subprocess.TimeoutExpired),
                }
                raise
        record["process"] = {"rc": rc, "timed_out": False}
        record["stage"] = "postflight"
        if hashlib.sha256(python.read_bytes()).hexdigest() != python_sha256:
            raise ValidationError("validation interpreter drifted during process")
        verify_runtime(runtime_record)
        verify_validation_bundle(bundle, bundle_manifest)
        verify_typed_validation_snapshot(snapshot, snapshot_record)
        result = json.loads((output / "stdout.json").read_text())
        json.dumps(result, allow_nan=False)
        if type(result) is not dict or type(result.get("ok")) is not bool:
            raise ValidationError("validator returned an invalid JSON verdict")
        if rc not in (0, 1) or (rc == 0) != result["ok"]:
            raise ValidationError("validator exit status disagrees with its verdict")
        record.update(classification="tool_result", ok=result["ok"], result=result, stage="done")
    except BaseException as exc:
        record["error"] = str(exc) or type(exc).__name__
        if not isinstance(exc, Exception):
            raise
    finally:
        record["elapsed_s"] = time.monotonic() - started
        _json(output / "result.json", record)
    return record


def verify_validation_binding(binding, runtime_record, source_roots, participant_policies):
    """Verify current runtime contents and the declared validation authority."""
    verify_runtime(runtime_record)
    return _verify_validation_binding_fields(
        binding=binding,
        runtime_record=runtime_record,
        source_roots=source_roots,
        participant_policies=participant_policies,
    )


def _verify_validation_binding_fields(binding, runtime_record, source_roots, participant_policies):
    """Decode and verify a fixed typed-check launch declaration for session admission."""
    fields = {
        "schema_version",
        "bundle",
        "bundle_manifest",
        "snapshot_storage",
        "policy",
        "profile_path",
        "attestation",
        "python",
        "python_sha256",
        "environment",
        "environment_record",
    }
    if type(binding) is not dict or set(binding) != fields:
        raise ValidationError("validation binding fields are unresolved")
    if binding["schema_version"] != "aisle.typed-validation-binding.v1":
        raise ValidationError("validation binding schema is unsupported")
    policy = MacOSPolicy(
        **{
            key: value if key == "network_policy" else tuple(Path(p) for p in value)
            for key, value in binding["policy"].items()
        }
    )
    kwargs = {key: value for key, value in binding.items() if key != "schema_version"}
    kwargs["policy"] = policy
    kwargs["runtime_record"] = runtime_record
    protected = [Path(binding[key]) for key in ("bundle", "snapshot_storage")]
    protected.append(Path(binding["environment_record"]["home"]))
    for declared in participant_policies:
        for key in ("visible_roots", "output_roots", "runtime_read_roots"):
            for name in declared[key]:
                path = Path(name).resolve()
                if any(
                    asset.is_relative_to(path) or path.is_relative_to(asset) for asset in protected
                ):
                    raise ValidationError("validation assets overlap participant authority")
    compiled = _verify_validation_launch_fields(
        **{
            key: kwargs[key]
            for key in (
                "bundle",
                "bundle_manifest",
                "snapshot_storage",
                "policy",
                "python",
                "python_sha256",
                "runtime_record",
                "environment",
                "environment_record",
            )
        },
        source_roots=source_roots,
    )
    wrap_verified_command(
        [binding["python"], "-I", "-B", "-c", "pass"],
        compiled,
        Path(binding["profile_path"]),
        binding["attestation"],
    )
    return kwargs
