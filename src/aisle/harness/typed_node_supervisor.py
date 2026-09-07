"""Host supervision of one already-isolated typed worker process.

Launch admission, stderr retention and OS/resource confinement belong to the
caller. Pipe deadlines do not preempt a blocking trusted Node iterator; its
runtime must also have an outer execution deadline.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import time
from pathlib import Path

from aisle.harness.typed_node_requests import TypedNodeRequests
from aisle.harness.typed_node_worker import MODULES, validate_configuration
from aisle.monolith.supervisor import WorkerFailure, _DeadlinePipe
from aisle.monolith.wire import receive, send


def supervise_typed_worker(
    process,
    node,
    source,
    module_name,
    output,
    *,
    outputs,
    timeout_s,
    max_calls=100000,
    configuration=None,
):
    """Adopt the child, verify receipts against host state, and retain all observed frames."""
    created_output = False
    started = time.monotonic()
    record = {
        "schema_version": "aisle.typed-node-worker.v1",
        "classification": "infrastructure_exclusion",
        "ok": False,
        "error": None,
        "rc": None,
        "messages": 0,
        "requests": 0,
        "input_exhausted": False,
        "stdout_eof": False,
        "confirmatory_ready": False,
        "confinement_verified": False,
    }

    def stop():
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
        process.wait()

    def transfer(direction, value=None, *, expect_eof=False):
        pipe = _DeadlinePipe(
            process.stdin if direction == "host" else process.stdout,
            deadline,
            time.monotonic,
        )
        complete = False
        try:
            if expect_eof:
                result = pipe.read(1)
                if result:
                    raise WorkerFailure("typed worker emitted trailing protocol bytes")
                record["stdout_eof"] = True
            else:
                result = send(pipe, value) if direction == "host" else receive(pipe)
            complete = True
            return result
        finally:
            if not (expect_eof and complete and not pipe.observed):
                record["messages"] += 1
                name = f"{record['messages']:08d}-{direction}.frame"
                data = bytes(pipe.observed)
                with (output / name).open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                with (output / "messages.jsonl").open("a") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "sequence": record["messages"],
                                "direction": direction,
                                "file": name,
                                "sha256": hashlib.sha256(data).hexdigest(),
                                "bytes": len(data),
                                "complete": complete,
                            }
                        )
                        + "\n"
                    )
                    stream.flush()
                    os.fsync(stream.fileno())

    try:
        output = Path(output).absolute()
        if output.resolve() != output:
            raise WorkerFailure("typed worker evidence path is redirected")
        output.mkdir(parents=True, exist_ok=False)
        created_output = True
        (output / "messages.jsonl").touch(exist_ok=False)
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise WorkerFailure("typed worker timeout must be finite and positive")
        deadline = started + timeout_s
        if type(source) is not str or type(module_name) is not str or module_name not in MODULES:
            raise WorkerFailure("typed worker source binding is invalid")
        if process.stdin is None or process.stdout is None:
            raise WorkerFailure("typed worker requires dedicated binary pipes")
        service = TypedNodeRequests(node, outputs=outputs, max_calls=max_calls)
        identity = {
            "module": module_name,
            "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        }
        record.update(identity)
        settings = {}
        if configuration is not None:
            settings["configuration"] = validate_configuration(configuration)
            record["configuration"] = settings["configuration"]
        transfer("host", {"op": "init", "source": source, **identity, **settings})
        ready = False
        while True:
            message = transfer("worker")
            if type(message) is not dict:
                raise WorkerFailure("typed worker message is not an object")
            kind = message.get("kind")
            if kind == "ready":
                if ready or message != {"kind": "ready", **identity}:
                    raise WorkerFailure("typed worker source receipt differs")
                ready = True
            elif kind == "node":
                if not ready or set(message) != {"kind", "request"}:
                    raise WorkerFailure("typed node request precedes readiness or has extra fields")
                record["requests"] += 1
                reply = service.handle(message["request"])
                if message["request"]["op"] == "next" and reply["result"]["end"]:
                    record["input_exhausted"] = True
                transfer("host", reply)
            elif kind == "finished":
                if (
                    not ready
                    or set(message) != {"kind", *identity, "node_created", "input_exhausted"}
                    or any(message[key] != value for key, value in identity.items())
                    or type(message["node_created"]) is not bool
                    or type(message["input_exhausted"]) is not bool
                    or message["input_exhausted"] != record["input_exhausted"]
                    or (record["requests"] > 0 and not message["node_created"])
                ):
                    raise WorkerFailure("typed worker completion differs from host observations")
                transfer("worker", expect_eof=True)
                rc = process.wait(timeout=max(0, deadline - time.monotonic()))
                if rc != 0:
                    raise WorkerFailure("typed worker completion disagrees with process exit")
                record.update(
                    classification="module_result",
                    ok=record["input_exhausted"],
                    worker_claims=message,
                )
                if not record["input_exhausted"]:
                    record["error"] = "authored script returned before host input completion"
                break
            elif kind == "error":
                error = message.get("error")
                if (
                    set(message) != {"kind", *identity, "error"}
                    or any(message[key] != value for key, value in identity.items())
                    or type(error) is not dict
                    or set(error) != {"category", "type", "message"}
                    or any(type(value) is not str for value in error.values())
                    or error["category"] not in {"module", "protocol"}
                ):
                    raise WorkerFailure("typed worker error envelope is invalid")
                record["worker_claims"] = message
                if error["category"] != "module" or not ready:
                    raise WorkerFailure(f"typed worker protocol failed: {error['message']}")
                transfer("worker", expect_eof=True)
                if process.wait(timeout=max(0, deadline - time.monotonic())) != 1:
                    raise WorkerFailure("typed worker error disagrees with process exit")
                record.update(
                    classification="module_result", error=f"{error['type']}: {error['message']}"
                )
                break
            else:
                raise WorkerFailure("typed worker message kind is undeclared")
    except BaseException as exc:
        record.update(
            classification="infrastructure_exclusion",
            ok=False,
            error=str(exc) or type(exc).__name__,
        )
        if not isinstance(exc, Exception):
            raise
    finally:
        stop()
        record["rc"] = process.returncode
        record["elapsed_s"] = time.monotonic() - started
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
        if created_output:
            with (output / "worker.json").open("x") as stream:
                stream.write(json.dumps(record, indent=2, allow_nan=False) + "\n")
    return record
