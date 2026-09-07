"""Manage an already admitted worker process and retain its RPC boundary.

The caller owns launch-policy admission and the stderr sink. This supervisor
never starts an unrestricted child or asserts that a supplied child is confined.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import select
import signal
import time
from pathlib import Path

from aisle.monolith.requests import PrimitiveRequestError, PrimitiveRequests
from aisle.monolith.wire import WireError, receive, send
from aisle.nodes.monolith_broker import validate_action


class WorkerFailure(RuntimeError):
    """The worker failed and cannot produce further accepted actions."""


class WorkerModuleFailure(WorkerFailure):
    """An ordinary authored-module error reported by the child."""


class _DeadlinePipe:
    def __init__(self, stream, deadline, clock):
        self.fd = stream.fileno()
        self.deadline, self.clock = deadline, clock
        self.observed = bytearray()
        os.set_blocking(self.fd, False)

    def _wait(self, reading):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise WorkerFailure("worker pipe deadline exceeded")
        readers, writers, _ = select.select(
            [self.fd] if reading else [], [] if reading else [self.fd], [], remaining
        )
        if not readers and not writers:
            raise WorkerFailure("worker pipe deadline exceeded")

    def read(self, size):
        while True:
            self._wait(True)
            try:
                chunk = os.read(self.fd, size)
                self.observed.extend(chunk)
                return chunk
            except BlockingIOError:
                continue

    def write(self, value):
        while True:
            self._wait(False)
            try:
                count = os.write(self.fd, value)
                self.observed.extend(value[:count])
                return count
            except BlockingIOError:
                continue

    def flush(self):
        pass  # os.write has no user-space buffer.


class WorkerSupervisor:
    """Own one worker's command lifecycle, primitive requests and action checks."""

    def __init__(
        self,
        process,
        primitives,
        output,
        *,
        timeout_s,
        monotonic=time.monotonic,
        max_primitive_calls=100000,
        max_handles=1024,
    ):
        self.process = process
        self.output = Path(output)
        self.clock = monotonic
        self.timeout_s = timeout_s
        self.state, self.error = "new", None
        self.command_id = self.request_id = self.message_id = 0
        self._finished = False
        try:
            self.service = PrimitiveRequests(
                primitives, max_calls=max_primitive_calls, max_handles=max_handles
            )
            self.api_version, self.n_dof = primitives.api_version, len(primitives.home)
            if (
                type(timeout_s) not in (int, float)
                or not math.isfinite(timeout_s)
                or timeout_s <= 0
            ):
                raise WorkerFailure("worker pipe timeout must be finite and positive")
            if process.stdin is None or process.stdout is None:
                raise WorkerFailure("worker requires dedicated binary pipes")
            self.output.mkdir(parents=True, exist_ok=False)
            (self.output / "messages.jsonl").touch(exist_ok=False)
        except BaseException:
            self._stop()
            raise

    def _retain(self, direction, raw, complete):
        self.message_id += 1
        name = f"{self.message_id:08d}-{direction}.frame"
        with (self.output / name).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        row = {
            "sequence": self.message_id,
            "direction": direction,
            "file": name,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "complete": complete,
        }
        with (self.output / "messages.jsonl").open("a") as stream:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _transfer(self, direction, deadline, value=None):
        pipe = _DeadlinePipe(
            self.process.stdin if direction == "parent" else self.process.stdout,
            deadline,
            self.clock,
        )
        complete = False
        try:
            result = send(pipe, value) if direction == "parent" else receive(pipe)
            complete = True
            return result
        finally:
            self._retain(direction, pipe.observed, complete)

    def _stop(self):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                self.process.kill()
        self.process.wait()
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None:
                stream.close()

    def _finish(self):
        if self._finished:
            return
        record = {
            "schema_version": "aisle.monolith.worker.v1",
            "state": self.state,
            "error": self.error,
            "rc": self.process.returncode,
            "commands": self.command_id,
            "primitive_calls": self.service.calls,
            "messages": self.message_id,
            "confinement_verified": False,
        }
        with (self.output / "worker.json").open("x") as stream:
            stream.write(json.dumps(record, indent=2, allow_nan=False) + "\n")
        self._finished = True

    def _fail(self, exc):
        if self.state != "failed":
            self.state, self.error = "failed", str(exc) or type(exc).__name__
            self._stop()
            self._finish()

    def _command(self, operation, **payload):
        if self.state in {"failed", "closed"}:
            raise WorkerFailure("worker is already terminal")
        self.command_id += 1
        deadline = self.clock() + self.timeout_s
        try:
            self._transfer("parent", deadline, {"id": self.command_id, "op": operation, **payload})
            while True:
                reply = self._transfer("worker", deadline)
                if type(reply) is not dict or type(reply.get("id")) is not int:
                    raise WorkerFailure("invalid worker reply")
                kind = reply.get("kind")
                if kind == "primitive":
                    if (
                        set(reply) != {"kind", "id", "request"}
                        or reply["id"] != self.request_id + 1
                    ):
                        raise WorkerFailure("worker primitive sequence differs")
                    self.request_id += 1
                    try:
                        result = self.service.dispatch(reply["request"])
                    except (PrimitiveRequestError, WireError) as exc:
                        # A denied request invalidates the session even if authored
                        # code would catch a returned error and attempt to continue.
                        raise WorkerFailure(f"primitive request refused: {exc}") from exc
                    except Exception as exc:
                        result = {
                            "error": {
                                "category": "primitive",
                                "kind": type(exc).__name__,
                                "message": str(exc)[:4096],
                            }
                        }
                    self._transfer(
                        "parent",
                        deadline,
                        {"kind": "primitive_reply", "id": self.request_id, "reply": result},
                    )
                    continue
                if reply["id"] != self.command_id:
                    raise WorkerFailure("worker command reply sequence differs")
                if kind == "error" and set(reply) == {"kind", "id", "error"}:
                    error = reply["error"]
                    if (
                        type(error) is not dict
                        or set(error) != {"category", "kind", "message"}
                        or any(type(value) is not str for value in error.values())
                        or error["category"] not in {"module", "protocol", "confinement"}
                    ):
                        raise WorkerFailure("invalid worker error envelope")
                    failure = (
                        WorkerModuleFailure if error["category"] == "module" else WorkerFailure
                    )
                    raise failure(f"{error['kind']}: {error['message']}")
                if kind != "result" or set(reply) != {"kind", "id", "value"}:
                    raise WorkerFailure("invalid worker result envelope")
                return reply["value"]
        except BaseException as exc:
            self._fail(exc)
            if not isinstance(exc, Exception) or isinstance(exc, WorkerModuleFailure):
                raise
            raise WorkerFailure(self.error) from exc

    def initialize(self, source, filename):
        if self.state != "new":
            raise WorkerFailure("worker source is already bound")
        result = self._command("init", source=source, filename=filename)
        expected = {
            "api_version": self.api_version,
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        }
        if result != expected:
            exc = WorkerFailure("worker source receipt differs")
            self._fail(exc)
            raise exc
        self.state = "ready"
        return result

    def event(self, event):
        if self.state != "ready":
            raise WorkerFailure("worker is not initialized")
        raw = self._command("event", event=event)
        try:
            if raw is None:
                raw = []
            if type(raw) is not list:
                raise WorkerFailure("worker actions must be a list")
            return [validate_action(action, self.n_dof) for action in raw]
        except Exception as exc:
            self._fail(exc)
            raise WorkerFailure(self.error) from exc

    def close(self):
        if self.state in {"failed", "closed"}:
            return
        try:
            if self._command("close") is not None:
                raise WorkerFailure("worker close receipt differs")
            if self.process.wait(timeout=self.timeout_s) != 0:
                raise WorkerFailure("worker exited unsuccessfully")
            self._stop()
            self.state = "closed"
            self._finish()
        except BaseException as exc:
            self._fail(exc)
            if not isinstance(exc, Exception) or isinstance(exc, WorkerModuleFailure):
                raise
            raise WorkerFailure(self.error) from exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc is not None:
            self._fail(exc)
        else:
            self.close()
        return False
