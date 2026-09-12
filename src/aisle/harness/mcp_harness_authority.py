"""One-use correlation of owned frontend events with incoming MCP harness calls."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from pathlib import Path

from aisle.harness.mcp_harness_source import _decode, match_mcp_request


class MCPHarnessAuthority:
    """Only the owning App Server reader may register source events.

    Incoming MCP metadata selects a source; it cannot create one. The callback
    owns budget reservation and controller authorization. The transport owner
    must stop and join request handlers before closing this evidence store.
    """

    def __init__(self, output, *, handle_call, timeout_s):
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("MCP authority requires a finite deadline")
        output = Path(output).absolute()
        if output.resolve() != output:
            raise ValueError("redirected MCP authority path")
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
        self._fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._condition = threading.Condition(threading.RLock())
        self._sources = {}
        self._consumed = set()
        self._artifacts = {}
        self._bytes = 0
        self._requests = 0
        self._closed = False
        self._closing = False
        self._failure = None
        self.failed = threading.Event()
        self._deadline = time.monotonic() + timeout_s
        self._handle_call = handle_call

    def _retain(self, name, value):
        raw = value if type(value) is bytes else json.dumps(value, allow_nan=False).encode() + b"\n"
        if self._bytes + len(raw) > 64 * 1024 * 1024:
            raise ValueError("MCP evidence byte budget exhausted")
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._fd
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self._fd)
        self._artifacts[name] = hashlib.sha256(raw).hexdigest()
        self._bytes += len(raw)

    def _check(self):
        if self._closed or self._closing or self.failed.is_set():
            raise ValueError("MCP authority retired")
        if time.monotonic() >= self._deadline:
            raise ValueError("MCP source deadline expired")

    def _fail(self, exc):
        if self._failure is None:
            self._failure = type(exc).__name__
        self.failed.set()
        self._condition.notify_all()

    def observe(self, source, *, thread_id, turn_id):
        """Register an event already acquired and scoped by the owned pipe reader."""
        with self._condition:
            try:
                self._check()
                item = _decode(source)["params"]["item"]
                # Reuse the strict source parser; this is not an incoming request.
                template = {
                    "jsonrpc": "2.0",
                    "id": 0,
                    "method": "tools/call",
                    "params": {
                        "name": item["tool"],
                        "arguments": {},
                        "_meta": {
                            "callId": item["id"],
                            "threadId": thread_id,
                            "x-codex-turn-metadata": {"thread_id": thread_id, "turn_id": turn_id},
                        },
                    },
                }
                call = match_mcp_request(
                    source, json.dumps(template).encode(), thread_id=thread_id, turn_id=turn_id
                )
                key = (thread_id, turn_id, call["call_id"])
                if key in self._sources or len(self._sources) >= 1024:
                    raise ValueError("duplicate or excessive MCP source events")
                self._retain(f"source-{len(self._sources) + 1:08d}.frame", source)
                self._sources[key] = source
                self._condition.notify_all()
            except BaseException as exc:
                self._fail(exc)
                raise

    def dispatch(self, raw):
        prefix = None
        error = None
        failure = None
        entered = False
        try:
            with self._condition:
                self._check()
                if self._requests >= 1024:
                    raise ValueError("MCP request count exhausted")
                self._requests += 1
                prefix = f"{self._requests:08d}"
                self._retain(prefix + "-request.frame", raw)
                request = _decode(raw)
                meta = request["params"]["_meta"]
                key = (meta["threadId"], meta["x-codex-turn-metadata"]["turn_id"], meta["callId"])
                if any(type(value) is not str or not 0 < len(value) <= 256 for value in key):
                    raise ValueError("invalid MCP request identity")
                while key not in self._sources:
                    self._check()
                    self._condition.wait(max(0, self._deadline - time.monotonic()))
                if key in self._consumed:
                    raise ValueError("MCP source already consumed: replay")
                source = self._sources[key]
                call = match_mcp_request(source, raw, thread_id=key[0], turn_id=key[1])
                self._retain(prefix + "-source.frame", source)
                self._consumed.add(key)
                entered = True
            result = self._handle_call(call, source)
            with self._condition:
                self._retain(prefix + "-result.json", result)
            return result
        except BaseException as exc:
            failure = exc
            error = type(exc).__name__
            with self._condition:
                self._fail(exc)
            raise
        finally:
            if prefix is not None:
                try:
                    with self._condition:
                        self._retain(
                            prefix + "-delivery.json",
                            {"handler_entered": entered, "error_type": error},
                        )
                except BaseException as retention_error:
                    with self._condition:
                        self._fail(retention_error)
                    if failure is None:
                        raise
                    failure.add_note("MCP delivery retention failed: " + str(retention_error))

    def begin_shutdown(self):
        """Wake pending requests before the transport joins its handler threads."""
        with self._condition:
            self._closing = True
            self._condition.notify_all()

    def close(self):
        with self._condition:
            if not self._closed:
                if self._sources.keys() != self._consumed:
                    self._fail(ValueError("unmatched MCP source events"))
                self._closed = True
                self._condition.notify_all()
                os.close(self._fd)

    def reference(self):
        with self._condition:
            if not self._closed:
                raise ValueError("MCP authority evidence is still open")
            return {
                "artifacts": dict(self._artifacts),
                "failure": self._failure,
                "calls": len(self._consumed),
            }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
