"""One-use request grants issued by a trusted frontend adapter.

The adapter authenticates and parses frontend calls before calling authorize.
This object authenticates delegation by possession of a request-bound grant;
it does not identify the requesting OS process or attest frontend coverage.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from pathlib import Path


class RequestRefused(ValueError):
    """The request has no usable controller-owned authorization."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RequestRefused("duplicate request field")
        result[key] = value
    return result


class RequestAuthority:
    """Fresh session authority; retention failures close it without refunds."""

    def __init__(self, output, *, session_id):
        if type(session_id) is not str or not session_id or len(session_id) > 256:
            raise ValueError("invalid session identity")
        output = Path(output).absolute()
        if output.resolve() != output:
            raise ValueError("redirected authority path")
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
        self._fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._lock = threading.Lock()
        self._grants = {}
        self._calls = set()
        self._requests = set()
        self._consumed = set()
        self._refusals = 0
        self._closed = False
        self._failed = False
        self._digests = {}
        self.session_id = session_id
        try:
            self._retain(
                "authority.json",
                {
                    "schema_version": "aisle.frontend-request-authority.v1",
                    "session_id": session_id,
                    "complete_coverage": False,
                    "confinement_verified": False,
                },
            )
        except BaseException:
            self.close()
            raise

    def _retain(self, name, value):
        data = json.dumps(value, sort_keys=True, allow_nan=False).encode() + b"\n"
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._fd
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self._fd)
        self._digests[name] = hashlib.sha256(data).hexdigest()

    def authorize(self, *, call, request):
        """Bind a trusted adapter's call to the exact bytes it will release."""
        with self._lock:
            if self._closed:
                raise RequestRefused("request authority is closed")
            try:
                if (
                    type(call) is not dict
                    or set(call) != {"turn_id", "call_id", "tool_name"}
                    or any(type(v) is not str or not v or len(v) > 256 for v in call.values())
                    or type(request) is not bytes
                    or len(request) > 4096
                    or not request.endswith(b"\n")
                ):
                    raise RequestRefused("invalid call or request")
                parsed = json.loads(request, object_pairs_hook=_object)
                if (
                    type(parsed) is not dict
                    or set(parsed) != {"schema_version", "id", "operation"}
                    or parsed["schema_version"] != "aisle.matched-tool-request.v1"
                    or type(parsed["id"]) is not str
                    or re.fullmatch(r"[a-f0-9]{32}", parsed["id"]) is None
                    or parsed["operation"] not in ("check", "run")
                    or call["tool_name"] != "harness." + parsed["operation"]
                ):
                    raise RequestRefused("call does not authorize this operation")
                identity = (call["turn_id"], call["call_id"])
                if identity in self._calls or parsed["id"] in self._requests:
                    raise RequestRefused("call or request already authorized")
                token = secrets.token_hex(32)
                grant = {
                    "schema_version": "aisle.frontend-request-grant.v1",
                    "authorization_id": token,
                    "session_id": self.session_id,
                    "call": dict(call),
                    "request_id": parsed["id"],
                    "request_sha256": hashlib.sha256(request).hexdigest(),
                    "request_bytes": len(request),
                }
                self._retain(token + "-grant.json", grant)
                self._calls.add(identity)
                self._requests.add(parsed["id"])
                self._grants[token] = grant
            except BaseException:
                self._failed = True
                self._closed = True
                raise
            return json.loads(json.dumps(grant))

    def consume(self, request, *, authorization_id):
        """Persist consumption before the caller can start a controller attempt."""
        with self._lock:
            if self._closed:
                raise RequestRefused("request authority is closed")
            grant = self._grants.get(authorization_id) if type(authorization_id) is str else None
            if (
                grant is None
                or authorization_id in self._consumed
                or type(request) is not bytes
                or len(request) != grant["request_bytes"]
                or hashlib.sha256(request).hexdigest() != grant["request_sha256"]
            ):
                self._refusals += 1
                bounded = type(request) is bytes and len(request) <= 4096
                receipt = {
                    "schema_version": "aisle.frontend-request-refusal.v1",
                    "session_id": self.session_id,
                    "decision": "refused",
                    "reason": "missing, altered or consumed request authorization",
                    "authorization_id": authorization_id
                    if type(authorization_id) is str and len(authorization_id) <= 256
                    else None,
                    "request_bytes": len(request) if type(request) is bytes else None,
                    "request_sha256": hashlib.sha256(request).hexdigest() if bounded else None,
                }
                try:
                    self._retain(f"{self._refusals:08d}-refused.json", receipt)
                except BaseException:
                    self._failed = True
                    self._closed = True
                    raise
                raise RequestRefused(receipt["reason"])
            self._consumed.add(authorization_id)
            try:
                self._retain(authorization_id + "-consumed.json", grant)
            except BaseException:
                self._failed = True
                self._closed = True
                raise
            return json.loads(json.dumps(grant))

    def retain_response_rejection(self, *, call, request_id, response):
        """Retain a bounded rejected controller result and retire this authority."""
        with self._lock:
            if self._closed:
                raise RequestRefused("request authority is closed")
            try:
                matches = [
                    grant
                    for grant in self._grants.values()
                    if grant["request_id"] == request_id and grant["call"] == call
                ]
                if len(matches) != 1:
                    raise RequestRefused("rejected response has no issued request grant")
                receipt = {
                    "schema_version": "aisle.frontend-response-rejection.v1",
                    "session_id": self.session_id,
                    "request_id": request_id,
                    "call": dict(call),
                    "response": response,
                }
                if len(json.dumps(receipt, sort_keys=True, allow_nan=False).encode()) + 1 > 65536:
                    raise RequestRefused("rejected response exceeds retention limit")
                self._retain(request_id + "-response-rejected.json", receipt)
            except BaseException:
                self._failed = True
                raise
            finally:
                self._closed = True

    def reference(self):
        """Return write-time hashes after closure, never recomputed from the evidence files.

        The controller must retain this reference outside participant authority.
        It is not a signature and does not attest origin to an external reviewer.
        """
        with self._lock:
            if not self._closed or self._fd is not None:
                raise RequestRefused("request authority must be closed before audit")
            if self._failed:
                raise RequestRefused("request authority retention or issuance failed")
            return {"session_id": self.session_id, "artifacts": dict(self._digests)}

    def close(self):
        with self._lock:
            self._closed = True
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def __enter__(self):
        if self._closed:
            raise RequestRefused("request authority is closed")
        return self

    def __exit__(self, *_):
        self.close()
