"""Measure socket denial with the Python interpreter allowed by a worker profile.

This is one capability component, not a complete adapter attestation.
"""

from __future__ import annotations

import copy
import errno
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

from aisle.harness.treatment_ambient import spawn_isolated_process, verify_declared_environment
from aisle.harness.treatment_confinement import SANDBOX_EXEC, SYSTEM_PROFILE, compile_macos_profile
from aisle.harness.typed_snapshot import _read

_CODE = """
import json, socket, sys
print(json.dumps({"phase": "ready"}), flush=True)
connection = socket.socket()
connection.settimeout(2)
try:
    connection.connect(("127.0.0.1", int(sys.argv[1])))
except OSError as exc:
    print(json.dumps({"phase": "connect", "errno": exc.errno}), flush=True)
    raise SystemExit(3)
try:
    chunks = []
    left = int(sys.argv[2])
    while left:
        data = connection.recv(left)
        if not data:
            break
        chunks.append(data)
        left -= len(data)
    print(json.dumps({"phase": "read", "data": b"".join(chunks).hex()}), flush=True)
finally:
    connection.close()
"""


def connection_case(result, sentinel, *, confined):
    """Attribute denial only to the connection operation of the fixed started probe."""
    try:
        rows = [json.loads(line) for line in result.stdout.splitlines()]
    except (ValueError, UnicodeError):
        rows = []
    ready = len(rows) == 2 and rows[0] == {"phase": "ready"}
    final = rows[-1] if ready and type(rows[-1]) is dict else {}
    received = final == {"phase": "read", "data": sentinel.hex()}
    exposed = any(
        marker in stream
        for marker in (sentinel, sentinel.hex().encode())
        for stream in (result.stdout, result.stderr)
    )
    denied = (
        ready
        and result.returncode == 3
        and set(final) == {"phase", "errno"}
        and final["phase"] == "connect"
        and type(final["errno"]) is int
        and final["errno"] in (errno.EACCES, errno.EPERM)
    )
    return {
        "id": "tcp_read" if confined else "unrestricted_tcp_baseline",
        "passed": bool(
            denied and not exposed if confined else ready and result.returncode == 0 and received
        ),
        "network_attempted": bool(ready and final.get("phase") in {"connect", "read"}),
        "sentinel_exposed": exposed,
        "denied": bool(denied),
        "returncode": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
    }


def _capture(command, cwd, environment, environment_record, output):
    output.mkdir()
    (output / "invocation.json").write_text(
        json.dumps(
            {
                "argv": command,
                "cwd": str(cwd),
                "environment_record": environment_record,
            }
        )
    )
    with (
        (output / "stdout.jsonl").open("xb") as stdout,
        (output / "stderr.log").open("xb") as stderr,
    ):
        process = None
        error = None
        try:
            process = spawn_isolated_process(
                command,
                cwd=cwd,
                environment=environment,
                environment_record=environment_record,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
            process.wait(timeout=5)
        except BaseException as exc:
            error = str(exc) or type(exc).__name__
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    process.kill()
                process.wait()
            raise
        finally:
            (output / "process.json").write_text(
                json.dumps(
                    {
                        "started": process is not None,
                        "returncode": None if process is None else process.returncode,
                        "error": error,
                    }
                )
            )
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        (output / "stdout.jsonl").read_bytes(),
        (output / "stderr.log").read_bytes(),
    )


def probe_worker_network(
    *, policy, profile_path, python, environment, environment_record, cwd, output, sentinel
):
    """Run positive and confined controls against one local controller-owned socket."""
    if sys.platform != "darwin":
        raise ValueError("worker network capability requires actual macOS sandbox-exec")
    if type(sentinel) is not bytes or not 1 <= len(sentinel) <= 64:
        raise ValueError("network probe requires a 1-64 byte controller sentinel")
    python, profile_path, cwd, output = (
        Path(p).absolute() for p in (python, profile_path, cwd, output)
    )
    python = python.resolve(strict=True)
    if set(policy.allowed_executables) != {python.resolve()}:
        raise ValueError("network probe requires the worker's Python-only executable grant")
    compiled = compile_macos_profile(policy)
    if hashlib.sha256(_read(profile_path.parent, profile_path.name)).hexdigest() != compiled.sha256:
        raise ValueError("worker probe profile differs from declared policy")
    readable = (*policy.visible_roots, *policy.output_roots, *policy.runtime_read_roots)
    if (
        output.resolve() != output
        or any(output.is_relative_to(p) or p.is_relative_to(output) for p in readable)
        or not any(output.is_relative_to(p) for p in policy.hidden_roots)
    ):
        raise ValueError("worker probe output must be private and disjoint from worker authority")
    if cwd.resolve() != cwd or not cwd.is_dir():
        raise ValueError("worker probe cwd must be a canonical directory")
    environment, environment_record = dict(environment), copy.deepcopy(environment_record)
    verify_declared_environment(environment, environment_record)
    identities = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (python, SANDBOX_EXEC, SYSTEM_PROFILE, profile_path)
    }
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": "aisle.worker-network-probe.v1",
        "ok": False,
        "confirmatory_ready": False,
        "policy_id": compiled.policy_id,
        "identities": identities,
        "cases": [],
        "listener_errors": [],
        "error": None,
    }
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(0.1)
            stopped = threading.Event()

            def serve():
                try:
                    while not stopped.is_set():
                        try:
                            connection, _ = listener.accept()
                        except TimeoutError:
                            continue
                        with connection:
                            connection.settimeout(1)
                            connection.sendall(sentinel)
                except BaseException as exc:
                    report["listener_errors"].append(str(exc) or type(exc).__name__)
                    stopped.set()

            server = threading.Thread(target=serve, daemon=True)
            server.start()
            command = [
                str(python),
                "-I",
                "-B",
                "-c",
                _CODE,
                str(listener.getsockname()[1]),
                str(len(sentinel)),
            ]
            try:
                for confined in (False, True):
                    argv = (
                        [str(SANDBOX_EXEC), "-f", str(profile_path)] if confined else []
                    ) + command
                    result = _capture(
                        argv,
                        cwd,
                        environment,
                        environment_record,
                        output / ("confined" if confined else "baseline"),
                    )
                    report["cases"].append(connection_case(result, sentinel, confined=confined))
            finally:
                stopped.set()
                server.join(timeout=2)
                if server.is_alive():
                    raise RuntimeError("worker probe listener did not stop")
                if report["listener_errors"]:
                    raise RuntimeError("worker probe listener failed")
        for path, digest in identities.items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError("worker probe identity changed during execution")
        report["ok"] = len(report["cases"]) == 2 and all(row["passed"] for row in report["cases"])
    except BaseException as exc:
        report["error"] = str(exc) or type(exc).__name__
        if not isinstance(exc, Exception):
            raise
    finally:
        (output / "report.json").write_text(json.dumps(report, allow_nan=False))
    return report
