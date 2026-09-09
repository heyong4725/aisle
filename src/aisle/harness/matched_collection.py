"""Supervise trusted run-evidence collection within a remaining tool budget.

The child isolates blocking filesystem/native reads. Timeout retains partial
files without presenting them as a completed collection or study evidence.
"""

from __future__ import annotations

import json
import math
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

from aisle.harness.matched_run_launch import _terminate_owned_run
from aisle.harness.treatment_ambient import build_declared_environment, spawn_isolated_process

_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from aisle.harness.matched_evidence import retain_run; "
    "result=retain_run(sys.argv[2], sys.argv[3], run_id=sys.argv[4]); "
    "raise SystemExit(0 if result['ok'] else 1)"
)
_REPORT_LIMIT = 16 * 1024 * 1024


def retain_run_bounded(source, destination, *, run_id, timeout_s):
    """Return a completed collection or refuse after stopping the owned child.

    Process cleanup has its own bounded grace. Its time is retained, and a
    timeout never becomes successful collection even if partial files exist.
    """
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("collection requires a finite positive deadline")
    started = time.monotonic()
    source, destination = Path(source).absolute(), Path(destination).absolute()
    control = destination.with_name(destination.name + "-collector")
    if destination.resolve() != destination or control.resolve() != control:
        raise ValueError("collection evidence path is redirected")
    if any(
        path.is_relative_to(source.resolve()) or source.resolve().is_relative_to(path)
        for path in (destination, control)
    ):
        raise ValueError("collection evidence overlaps source")
    if destination.exists() or destination.is_symlink():
        raise ValueError("collection destination already exists")
    control.mkdir(parents=True, exist_ok=False)
    record = {
        "schema_version": "aisle.matched-collection-process.v1",
        "ok": False,
        "process": None,
        "cleanup": None,
        "error": None,
        "timeout_s": timeout_s,
    }
    process = None

    def remaining():
        value = timeout_s - (time.monotonic() - started)
        if value <= 0:
            raise ValueError("collection wall deadline exhausted")
        return value

    try:
        environment, environment_record = build_declared_environment(
            control / "home", source_env={}
        )
        command = [
            sys.executable,
            "-I",
            "-B",
            "-c",
            _BOOTSTRAP,
            str(Path(__file__).resolve().parents[2]),
            str(source),
            str(destination),
            run_id,
        ]
        with (control / "invocation.json").open("x") as stream:
            json.dump(
                {
                    "argv": command,
                    "environment": environment,
                    "environment_record": environment_record,
                },
                stream,
                allow_nan=False,
            )
        remaining()
        with (
            (control / "stdout.log").open("xb") as stdout,
            (control / "stderr.log").open("xb") as stderr,
        ):
            process = spawn_isolated_process(
                command,
                cwd=control,
                environment=environment,
                environment_record=environment_record,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
            try:
                rc = process.wait(timeout=remaining())
            except BaseException as exc:
                record["cleanup"] = _terminate_owned_run(process)
                record["process"] = {
                    "rc": process.returncode,
                    "timed_out": isinstance(exc, (subprocess.TimeoutExpired, ValueError)),
                }
                if isinstance(exc, (subprocess.TimeoutExpired, ValueError)):
                    raise ValueError("collection wall deadline exhausted") from exc
                raise
        record["process"] = {"rc": rc, "timed_out": False}
        remaining()
        if rc not in (0, 1):
            raise ValueError(f"collection child failed with exit status {rc}")
        handle = os.open(
            destination / "run-evidence.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(handle, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _REPORT_LIMIT:
                raise ValueError("collection report is not a bounded regular file")
            raw = stream.read(_REPORT_LIMIT + 1)
        if len(raw) > _REPORT_LIMIT:
            raise ValueError("collection report exceeds size limit")
        try:
            result = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise ValueError("collection report is not valid JSON") from exc
        if (
            type(result) is not dict
            or type(result.get("ok")) is not bool
            or result.get("run_id") != run_id
            or rc != int(not result["ok"])
        ):
            raise ValueError("collection report differs from child verdict or run identity")
        remaining()
        record["ok"] = result["ok"]
        return result
    except BaseException as exc:
        record["error"] = str(exc) or type(exc).__name__
        raise
    finally:
        record["wall_s"] = time.monotonic() - started
        late_result = record["error"] is None and record["wall_s"] >= timeout_s
        if late_result:
            record["ok"] = False
            record["error"] = "collection wall deadline exhausted"
        with (control / "process.json").open("x") as stream:
            json.dump(record, stream, indent=2, allow_nan=False)
        if late_result:
            raise ValueError(record["error"])
