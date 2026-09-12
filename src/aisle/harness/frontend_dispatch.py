"""Durable reservations before delivery of covered frontend calls.

Only a trusted adapter may call this layer. The adapter must authenticate and
parse the call identity, bind it to the frame, and supply a bounded byte writer.
This layer neither discovers routes nor authenticates participant-supplied IDs;
it is not, by itself, a complete frontend or campaign enforcement boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

MAX_FRAME_BYTES = 16 * 1024 * 1024


class DispatchRefused(ValueError):
    """A covered call cannot receive another delivery authorization."""


class DispatchBudget:
    """Own one fresh session authority; consumed reservations are never refunded.

    A delivery callback returning proves only that the byte writer returned,
    not that the frontend received or executed the call. Any uncertain delivery
    or retention failure closes this authority. Recovery may not reset its
    directory; campaign invalidation and recovery belong to the caller.
    """

    def __init__(self, output, *, session_id, ceiling):
        if type(session_id) is not str or not session_id or len(session_id) > 256:
            raise ValueError("dispatch session identity must be a bounded string")
        if type(ceiling) is not int or ceiling <= 0:
            raise ValueError("dispatch ceiling must be a positive integer")
        output = Path(output).absolute()
        if output.resolve() != output:
            raise ValueError("dispatch authority path is redirected")
        output.mkdir(parents=True, exist_ok=False, mode=0o700)
        self._fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._lock = threading.Lock()
        self._closed = False
        self._seen = set()
        self._attempts = 0
        self._reserved = 0
        self._digests = {}
        self._retention_failed = False
        self._hosted_requests = 0
        self._hosted_seen = set()
        self.session_id = session_id
        self.ceiling = ceiling
        try:
            self._retain(
                "authority.json",
                {
                    "schema_version": "aisle.frontend-dispatch-authority.v1",
                    "session_id": session_id,
                    "ceiling": ceiling,
                    "complete_coverage": False,
                    "confinement_verified": False,
                },
            )
        except BaseException:
            self.close()
            raise

    def _retain(self, name, data):
        if type(data) is not bytes:
            data = json.dumps(data, sort_keys=True, allow_nan=False).encode() + b"\n"
        handle = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self._fd,
        )
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(self._fd)
        self._digests[name] = hashlib.sha256(data).hexdigest()

    def dispatch(self, call, frame, deliver):
        """Retain the decision before invoking the adapter's delivery callback."""
        with self._lock:
            if self._closed:
                raise DispatchRefused("dispatch authority is closed")
            try:
                if (
                    type(call) is not dict
                    or set(call) != {"turn_id", "call_id", "tool_name"}
                    or any(
                        type(value) is not str or not value or len(value) > 256
                        for value in call.values()
                    )
                    or type(frame) is not bytes
                    or len(frame) > MAX_FRAME_BYTES
                    or not callable(deliver)
                ):
                    raise ValueError("adapter supplied an invalid covered call or frame")
                self._attempts += 1
                prefix = f"{self._attempts:08d}"
                identity = (call["turn_id"], call["call_id"])
                reason = None
                if identity in self._seen:
                    reason = "call identity replay refused"
                elif self._reserved >= self.ceiling:
                    reason = "dispatch budget exhausted"
                self._seen.add(identity)
                if reason is None:
                    self._reserved += 1
                reservation = {
                    "schema_version": "aisle.frontend-dispatch-reservation.v1",
                    "session_id": self.session_id,
                    "attempt": self._attempts,
                    "call": dict(call),
                    "frame_sha256": hashlib.sha256(frame).hexdigest(),
                    "frame_bytes": len(frame),
                    "decision": "authorized" if reason is None else "refused",
                    "reason": reason,
                    "reserved_after": self._reserved,
                }
                self._retain(f"{prefix}.frame", frame)
                self._retain(f"{prefix}-reservation.json", reservation)
            except BaseException:
                self._retention_failed = True
                self._closed = True
                raise
            if reason is not None:
                raise DispatchRefused(reason)
            delivery = {
                "schema_version": "aisle.frontend-dispatch-delivery.v1",
                "session_id": self.session_id,
                "attempt": self._attempts,
                "frame_sha256": reservation["frame_sha256"],
                "status": "uncertain",
                "error_type": None,
            }
            callback_returned = False
            try:
                deliver(frame)
                callback_returned = True
                delivery["status"] = "returned"
                self._retain(f"{prefix}-delivery.json", delivery)
            except BaseException as exc:
                self._closed = True
                if callback_returned:
                    self._retention_failed = True
                delivery["status"] = "uncertain"
                delivery["error_type"] = type(exc).__name__
                try:
                    suffix = "delivery-error" if callback_returned else "delivery"
                    self._retain(f"{prefix}-{suffix}.json", delivery)
                except BaseException as retention_error:
                    self._retention_failed = True
                    exc.add_note(f"dispatch failure retention failed: {retention_error}")
                raise
            return {"reservation": reservation, "delivery": delivery}

    def hosted_response(self, request_id, request, exchange):
        """Hold an allowance before upstream work; release only provably unused slots.

        The owning relay must bind a provider enforcing max_tool_calls. The lock
        spans the bounded exchange, so concurrent local/hosted requests cannot
        spend its allowance. Completed calls are never refunded; uncertain work
        consumes the entire allowance and retires this authority.
        """
        from aisle.harness.provider_hosted import hosted_calls, hosted_request

        def retain(name, value):
            try:
                self._retain(name, value)
            except BaseException:
                self._retention_failed = True
                raise

        with self._lock:
            if self._closed:
                raise DispatchRefused("dispatch authority is closed")
            if (
                type(request_id) is not str
                or not 0 < len(request_id) <= 256
                or request_id in self._hosted_seen
                or self._hosted_requests >= 10000
            ):
                self._closed = True
                raise DispatchRefused("invalid or replayed hosted request")
            self._hosted_seen.add(request_id)
            before = self._reserved
            try:
                frame, allowance = hosted_request(request, self.ceiling - before)
                self._hosted_requests += 1
                prefix = f"hosted-{self._hosted_requests:08d}"
                retain(prefix + "-request.frame", frame)
                retain(
                    prefix + "-reservation.json",
                    {
                        "schema_version": "aisle.hosted-reservation.v1",
                        "session_id": self.session_id,
                        "request_id": request_id,
                        "local_attempts": self._attempts,
                        "allowance": allowance,
                        "reserved_before": before,
                        "request_sha256": hashlib.sha256(frame).hexdigest(),
                        "request_bytes": len(frame),
                    },
                )
            except BaseException:
                self._closed = True
                self._retention_failed = True
                raise
            self._reserved += allowance
            settlement = {
                "schema_version": "aisle.hosted-settlement.v1",
                "session_id": self.session_id,
                "request_id": request_id,
                "status": "uncertain",
                "error_type": None,
                "calls": None,
                "reserved_after": self._reserved,
                "response_sha256": None,
                "response_bytes": None,
            }
            if allowance == 0:
                settlement.update(status="refused", calls=[])
                try:
                    retain(prefix + "-settlement.json", settlement)
                except BaseException:
                    self._closed = self._retention_failed = True
                    raise
                raise DispatchRefused("dispatch budget exhausted")
            source = None
            try:
                source = exchange(frame)
                if type(source) is not bytes or len(source) > MAX_FRAME_BYTES:
                    raise ValueError("unbounded hosted response")
                retain(prefix + "-response.frame", source)
                settlement.update(
                    response_sha256=hashlib.sha256(source).hexdigest(), response_bytes=len(source)
                )
                calls = hosted_calls(source)
                identities = {(call["turn_id"], call["call_id"]) for call in calls}
                if len(calls) > allowance or identities & self._seen:
                    raise ValueError("hosted limit breach or call replay")
                settlement.update(
                    status="completed", calls=calls, reserved_after=before + len(calls)
                )
                retain(prefix + "-settlement.json", settlement)
                self._seen.update(identities)
                self._reserved = settlement["reserved_after"]
                return source
            except BaseException as exc:
                self._closed = True
                settlement.update(
                    status="uncertain",
                    error_type=type(exc).__name__,
                    calls=None,
                    reserved_after=self._reserved,
                )
                try:
                    retain(prefix + "-settlement.json", settlement)
                except BaseException as retention_error:
                    self._retention_failed = True
                    exc.add_note("hosted failure retention failed: " + str(retention_error))
                raise

    def reference(self):
        """Return the closed controller's write-time identities without rereading files."""
        with self._lock:
            if not self._closed or self._fd is not None:
                raise DispatchRefused("dispatch authority must be closed before audit")
            if self._retention_failed:
                raise DispatchRefused("dispatch retention failed")
            result = {
                "session_id": self.session_id,
                "ceiling": self.ceiling,
                "attempts": self._attempts,
                "reserved": self._reserved,
                "artifacts": dict(self._digests),
            }
            if self._hosted_requests:
                result["hosted_requests"] = self._hosted_requests
            return result

    def close(self):
        with self._lock:
            self._closed = True
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def __enter__(self):
        if self._closed:
            raise DispatchRefused("dispatch authority is closed")
        return self

    def __exit__(self, *_):
        self.close()
