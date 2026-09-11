"""Launch the trusted run controller with bound runtime, ambient state and deadline.

This supervises trusted harness code. Authored code still requires its worker
boundary; this module does not establish external process-tree/resource evidence.
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
from aisle.harness.treatment_ambient import (
    _GENERATED_PATHS,
    spawn_isolated_process,
    verify_declared_environment,
)

_BOOTSTRAP = (
    "import json, sys; sys.path[0:0] = json.loads(sys.argv.pop(1)); "
    "from aisle.harness.matched_run import main; raise SystemExit(main())"
)


def verify_controller_binding(binding, runtime_record, source_roots, participant_policies):
    """Bind interpreter and private controller environment, separately from worker grants."""
    verify_runtime(runtime_record)
    return _verify_controller_binding_fields(
        binding, runtime_record, source_roots, participant_policies
    )


def _verify_controller_binding_fields(binding, runtime_record, source_roots, participant_policies):
    """Check launch fields against runtime contents verified in this pre/postflight."""
    if (
        type(binding) is not dict
        or set(binding)
        != {
            "schema_version",
            "python",
            "python_sha256",
            "environment",
            "environment_record",
        }
        or binding["schema_version"] != "aisle.matched-run-controller.v1"
    ):
        raise ValueError("run controller binding fields are unresolved")
    python = Path(binding["python"])
    if (
        not python.is_absolute()
        or python != Path(os.path.abspath(python))
        or not os.access(python, os.X_OK)
        or not any(python.resolve().is_relative_to(Path(p)) for p in runtime_record["trees"])
    ):
        raise ValueError("run controller interpreter is outside bound runtime")
    # Python searches beside the invocation and one directory above for
    # pyvenv.cfg. Bind those directories (including an absent config), as well
    # as the resolved installation, so a symlink cannot select unbound startup
    # state while retaining the same executable hash.
    for prefix in (python.parent.parent, python.resolve().parent.parent):
        if not any(prefix.is_relative_to(Path(root)) for root in runtime_record["trees"]):
            raise ValueError("run controller startup configuration is outside bound runtime")
    if hashlib.sha256(python.read_bytes()).hexdigest() != binding["python_sha256"]:
        raise ValueError("run controller interpreter identity differs")
    verify_declared_environment(binding["environment"], binding["environment_record"])
    home = Path(binding["environment_record"]["home"])
    directories = {str(home / relative) for relative in _GENERATED_PATHS.values()}
    if set(binding["environment_record"]["state_directories"]) != directories or any(
        binding["environment"].get(name) != str(home / relative)
        for name, relative in _GENERATED_PATHS.items()
    ):
        raise ValueError("run controller environment paths differ from private HOME")
    for value in directories:
        directory = Path(value)
        if directory.resolve() != directory or not directory.is_dir():
            raise ValueError("run controller private state is missing or redirected")
        info = directory.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("run controller state is not private to its owner")
    protected = [Path(p).resolve() for p in (*source_roots, *runtime_record["trees"])]
    for policy in participant_policies:
        protected.extend(
            Path(p).resolve()
            for key in ("visible_roots", "output_roots", "runtime_read_roots")
            for p in policy[key]
        )
    if any(home.is_relative_to(p) or p.is_relative_to(home) for p in protected):
        raise ValueError("run controller state overlaps participant or immutable authority")
    return binding


def _process_table(*, timeout_s=5):
    """Read kernel process identities without command arguments or caller-selected PIDs."""
    rows = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,stat=,lstart=,comm="],
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout_s,
        env={**os.environ, "LC_ALL": "C"},
    )
    table = {}
    for line in rows.stdout.splitlines():
        fields = line.strip().split(None, 8)
        if not fields:
            continue
        if len(fields) != 9:
            raise ValueError("process inventory contains an unresolved identity")
        pid, parent = int(fields[0]), int(fields[1])
        table[pid] = {
            "parent": parent,
            "state": fields[2],
            "identity": (" ".join(fields[3:8]), fields[8]),
        }
    return table


def _terminate_owned_run(process):
    """Stop observed descendants before reaping the owned controller.

    This is operational cleanup, not an exhaustive containment attestation:
    children reparented before observation may be absent from the snapshot.
    """
    report = {
        "schema_version": "aisle.matched-run-cleanup.v1",
        "ok": False,
        "exhaustive": False,
        "parent_pid": process.pid,
        "observed_descendants": [],
        "signalled": [],
        "remaining": [],
        "errors": [],
    }
    owned = {}
    changed_identities = set()
    observation_deadline = time.monotonic() + 5

    def snapshot():
        remaining = observation_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("descendant observation deadline exhausted")
        return _process_table(timeout_s=remaining)

    try:
        if process.returncode is not None:
            raise ValueError("controller was already reaped; ownership is unresolved")
        table = snapshot()
        if process.pid not in table:
            raise ValueError("controller exited before descendant observation")
        depth = {process.pid: 0}
        while True:
            additions = {
                pid: depth[row["parent"]] + 1
                for pid, row in table.items()
                if pid not in depth and row["parent"] in depth
            }
            if not additions:
                break
            depth.update(additions)
        owned = {pid: table[pid] for pid in depth if pid != process.pid}
        report["observed_descendants"] = sorted(owned)
        for pid in sorted(owned, key=lambda value: depth[value], reverse=True):
            current = snapshot().get(pid)
            if current is None or current["state"].startswith("Z"):
                continue
            if current["identity"] != owned[pid]["identity"]:
                changed_identities.add(pid)
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                report["signalled"].append(pid)
            except ProcessLookupError:
                pass
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report["errors"].append(str(exc))
    finally:
        # Only an unreaped Popen child still reserves its PID for this owner.
        try:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
                process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            report["errors"].append(str(exc))
    try:
        # Allow signal delivery, without waiting for an external reaper to remove
        # zombies. Report unresolved live descendants instead of blessing them.
        deadline = time.monotonic() + 1
        while True:
            current = _process_table(timeout_s=max(0.001, deadline - time.monotonic()))
            report["remaining"] = [
                pid
                for pid in owned
                if pid in current
                and not current[pid]["state"].startswith("Z")
                and (
                    pid in changed_identities or current[pid]["identity"] == owned[pid]["identity"]
                )
            ]
            if not report["remaining"] or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report["errors"].append(str(exc))
    for pid in sorted(changed_identities.intersection(report["remaining"])):
        report["errors"].append(f"process {pid} identity changed and remains unresolved")
    report["ok"] = not report["remaining"] and not report["errors"]
    return report


def launch_configured_run(
    *,
    binding,
    runtime_record,
    source_roots,
    participant_policies,
    config_path,
    config_sha256,
    expected,
    output,
    timeout_s,
):
    """Launch only the fixed run entry, retaining child failure and timeout observations."""
    output = Path(output).absolute()
    if output.resolve() != output:
        raise ValueError("run controller evidence path is redirected")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    record = {
        "schema_version": "aisle.matched-run-process.v1",
        "ok": False,
        "process": None,
        "result": None,
        "error": None,
    }
    kwargs = dict(
        binding=binding,
        runtime_record=runtime_record,
        source_roots=source_roots,
        participant_policies=participant_policies,
    )
    try:
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("run controller requires a finite positive deadline")
        from aisle.harness.matched_run import _load

        config = _load(config_path, config_sha256, controller_root=expected["controller_root"])
        fields = {
            "session_id",
            "plan_id",
            "run_id",
            "arm",
            "controller_root",
            "participant_root",
            "development",
            "runtime_record",
            "worker_adapter_sha256",
        }
        if set(expected) != fields or any(config[key] != expected[key] for key in fields):
            raise ValueError("prepared run differs from active session or reserved attempt")
        if config["runtime_record"] != runtime_record:
            raise ValueError("prepared run runtime differs from controller binding")
        # _load freshly inventories the exact runtime compared above. Do not
        # immediately inventory those same trees a second time in this phase.
        _verify_controller_binding_fields(**kwargs)
        for policy in participant_policies:
            for key in ("visible_roots", "output_roots", "runtime_read_roots"):
                for value in policy[key]:
                    root = Path(value).resolve()
                    if (
                        output.is_relative_to(root)
                        or root.is_relative_to(output)
                        or Path(config_path).absolute().is_relative_to(root)
                    ):
                        raise ValueError(
                            "controller evidence or configuration overlaps participant authority"
                        )
        command = [
            binding["python"],
            "-I",
            "-B",
            "-c",
            _BOOTSTRAP,
            json.dumps(
                [str(Path(config["controller_root"]) / "src"), *sorted(runtime_record["trees"])]
            ),
            "--config",
            str(Path(config_path).absolute()),
            "--config-sha256",
            config_sha256,
        ]
        with (output / "invocation.json").open("x") as stream:
            json.dump(
                {
                    "argv": command,
                    "cwd": config["controller_root"],
                    "runtime_id": runtime_record["immutable_id"],
                    "environment_record": binding["environment_record"],
                    "timeout_s": timeout_s,
                },
                stream,
            )
        remaining = timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            raise ValueError("run controller deadline exhausted before spawn")
        with (
            (output / "stdout.json").open("xb") as stdout,
            (output / "stderr.log").open("xb") as stderr,
        ):
            process = spawn_isolated_process(
                command,
                cwd=config["controller_root"],
                environment=binding["environment"],
                environment_record=binding["environment_record"],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
            try:
                rc = process.wait(timeout=max(0, timeout_s - (time.monotonic() - started)))
            except BaseException as exc:
                record["cleanup"] = _terminate_owned_run(process)
                record["process"] = {
                    "rc": process.returncode,
                    "timed_out": isinstance(exc, subprocess.TimeoutExpired),
                }
                raise
            record["process"] = {"rc": rc, "timed_out": False}
        result = json.loads((output / "stdout.json").read_text())
        json.dumps(result, allow_nan=False)
        if (
            type(result) is not dict
            or type(result.get("ok")) is not bool
            or rc not in (0, 1)
            or (rc == 0) != result["ok"]
        ):
            raise ValueError("run controller exit status differs from JSON verdict")
        record["result"] = result
        _load(config_path, config_sha256, controller_root=expected["controller_root"])
        _verify_controller_binding_fields(**kwargs)
        record["ok"] = result["ok"]
    except BaseException as exc:
        record["error"] = str(exc) or type(exc).__name__
        if not isinstance(exc, Exception):
            raise
    finally:
        record["wall_s"] = time.monotonic() - started
        with (output / "process.json").open("x") as stream:
            json.dump(record, stream, allow_nan=False)
    return record
