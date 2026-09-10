"""Filesystem request channel for one controller-owned engineering session.

The participant may write requests and consume responses. The authoritative
attempt journal and retained request copies stay outside participant authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from pathlib import Path

from aisle.harness.matched_session import AdmissionError

_REQUEST = re.compile(r"([a-f0-9]{32})\.request\.json\Z")


def _request_object(pairs):
    request = {}
    for key, value in pairs:
        if key in request:
            raise AdmissionError("duplicate tool request field")
        request[key] = value
    return request


def _directory(path: Path) -> int:
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _read(fd: int, name: str, limit: int) -> bytes | None:
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise AdmissionError("tool channel entry is not a bounded private regular file")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise AdmissionError("tool channel entry exceeds its size limit")
    return data if data.endswith(b"\n") else None


def _write(fd: int, name: str, value: dict) -> None:
    data = json.dumps(value, allow_nan=False).encode() + b"\n"
    handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(handle, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def request_check(channel, *, timeout_s=30, request_id=None) -> dict:
    """Send a check request; the returned response is not the controller's audit record."""
    return _request("check", channel, timeout_s=timeout_s, request_id=request_id)


def request_run(channel, *, timeout_s=30, request_id=None) -> dict:
    """Request the sealed development run without supplying run parameters."""
    return _request("run", channel, timeout_s=timeout_s, request_id=request_id)


def _request(operation, channel, *, timeout_s, request_id):
    request_id = request_id or uuid.uuid4().hex
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise ValueError("invalid tool request id")
    fd = _directory(Path(channel))
    try:
        _write(
            fd,
            f"{request_id}.request.json",
            {
                "schema_version": "aisle.matched-tool-request.v1",
                "id": request_id,
                "operation": operation,
            },
        )
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                data = _read(fd, f"{request_id}.response.json", 1024 * 1024)
            except FileNotFoundError:
                data = None
            if data is not None:
                response = json.loads(data)
                if not isinstance(response, dict) or response.get("request_id") != request_id:
                    raise ValueError("tool response identity mismatch")
                return response
            time.sleep(0.01)
        raise TimeoutError("tool controller response deadline expired")
    finally:
        os.close(fd)


class ToolService:
    """Serve the active arm's pre-provisioned HOME/tool-channel on one controller."""

    def __init__(self, controller, *, request_authority=None):
        if request_authority is not None and request_authority.session_id != controller.session_id:
            raise AdmissionError("request authority belongs to another session")
        self.request_authority = request_authority
        self.controller = controller
        home = controller.plan["ambient_bindings"][controller.arm]["environment"]["HOME"]
        self.channel = Path(home) / "tool-channel"
        self.stop = threading.Event()
        self.failed = threading.Event()
        self.seen = set()
        self.processed = 0
        self.error = None
        self.fd = None
        self.thread = None
        self._responses = {}
        self._response_lock = threading.Lock()

    def controller_response(self, request_id):
        """Return a copy of the controller-owned result, never participant channel bytes."""
        with self._response_lock:
            value = self._responses.get(request_id)
            return json.loads(json.dumps(value)) if value is not None else None

    def __enter__(self):
        if self.channel.resolve() != self.channel:
            raise AdmissionError("tool channel must be canonical")
        self.fd = _directory(self.channel)
        try:
            if os.listdir(self.fd):
                raise AdmissionError("tool channel must be fresh and empty")
            self.identity = os.fstat(self.fd)
            self.thread = threading.Thread(target=self._serve, name="matched-tool-controller")
            self.thread.start()
            return self
        except Exception:
            os.close(self.fd)
            self.fd = None
            raise

    def _serve(self):
        try:
            while not self.stop.is_set():
                current = self.channel.lstat()
                if (current.st_dev, current.st_ino) != (self.identity.st_dev, self.identity.st_ino):
                    raise AdmissionError("tool channel directory identity changed")
                for name in sorted(os.listdir(self.fd)):
                    if self.stop.is_set():
                        break
                    match = _REQUEST.fullmatch(name)
                    if not match or name in self.seen:
                        continue
                    data = _read(self.fd, name, 4096)
                    if data is None:
                        continue
                    request_id = match.group(1)
                    self.seen.add(name)
                    retained = self.controller.output / f"request-{request_id}.json"
                    with retained.open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    retained_directory = _directory(self.controller.output)
                    try:
                        os.fsync(retained_directory)
                    finally:
                        os.close(retained_directory)
                    request = json.loads(data, object_pairs_hook=_request_object)
                    if (
                        not isinstance(request, dict)
                        or set(request) != {"schema_version", "id", "operation"}
                        or request["schema_version"] != "aisle.matched-tool-request.v1"
                        or request["id"] != request_id
                        or request["operation"] not in ("check", "run")
                    ):
                        raise AdmissionError("unsupported or malformed tool request")
                    authorization = None
                    if self.request_authority is not None:
                        try:
                            token_data = _read(self.fd, f"{request_id}.authorization.json", 4096)
                        except FileNotFoundError:
                            token_data = None
                        token = None
                        if token_data is not None:
                            envelope = json.loads(token_data, object_pairs_hook=_request_object)
                            if type(envelope) is dict and set(envelope) == {"authorization_id"}:
                                token = envelope["authorization_id"]
                        authorization = self.request_authority.consume(data, authorization_id=token)
                    record = (
                        self.controller.check()
                        if request["operation"] == "check"
                        else self.controller.run()
                    )
                    self.processed += 1
                    response = {
                        key: record[key]
                        for key in ("ok", "classification", "result", "error", "attempt")
                    }
                    response["request_id"] = request_id
                    with (self.controller.output / "tool-request-index.jsonl").open("a") as stream:
                        stream.write(
                            json.dumps(
                                {
                                    "request_id": request_id,
                                    "request_sha256": hashlib.sha256(data).hexdigest(),
                                    "attempt_id": record["immutable_id"],
                                    "attempt": record["attempt"],
                                    **(
                                        {"frontend_authorization": authorization}
                                        if authorization is not None
                                        else {}
                                    ),
                                }
                            )
                            + "\n"
                        )
                        stream.flush()
                        os.fsync(stream.fileno())
                    with self._response_lock:
                        self._responses[request_id] = response
                    _write(self.fd, f"{request_id}.response.json", response)
                self.stop.wait(0.01)
        except Exception as exc:
            self.error = str(exc)
            self.failed.set()

    def __exit__(self, exc_type, exc, traceback):
        self.stop.set()
        self.thread.join()
        try:
            pending = [
                name
                for name in os.listdir(self.fd)
                if _REQUEST.fullmatch(name) and name not in self.seen
            ]
            report = {
                "schema_version": "aisle.matched-tool-service.v1",
                "session_id": self.controller.session_id,
                "plan_id": self.controller.plan["immutable_id"],
                "arm": self.controller.arm,
                "ok": self.error is None and not pending,
                **(
                    {"request_authority_required": True}
                    if self.request_authority is not None
                    else {}
                ),
                "processed_requests": self.processed,
                "seen_requests": len(self.seen),
                "pending_requests": sorted(pending),
                "error": self.error,
            }
            self.report = report
            with (self.controller.output / "tool-service.json").open("x") as stream:
                json.dump(report, stream, indent=2)
                stream.write("\n")
        finally:
            os.close(self.fd)
        return False
