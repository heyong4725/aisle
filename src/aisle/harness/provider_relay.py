"""Owned, bounded HTTP Responses relay with durable provider source evidence."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from aisle.harness.frontend_dispatch import MAX_FRAME_BYTES
from aisle.harness.provider_response_authority import ProviderResponseAuthority, _require

MAX_EVIDENCE_BYTES = 64 * 1024 * 1024


def verify_provider(binding):
    """Require an explicit upstream; local HTTP is for operator-owned fixtures only."""
    _require(
        type(binding) is dict
        and set(binding) in ({"base_url"}, {"base_url", "requires_openai_auth"}),
        "invalid provider binding",
    )
    _require(type(binding.get("requires_openai_auth", True)) is bool, "invalid provider auth mode")
    value = binding["base_url"]
    _require(type(value) is str and 0 < len(value) <= 2048, "invalid provider URL")
    url = urlsplit(value)
    _require(
        url.scheme in ("http", "https")
        and url.hostname
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment,
        "provider URL must have a fixed authority without credentials or query",
    )
    _require(
        url.scheme == "https" or url.hostname in ("127.0.0.1", "::1", "localhost"),
        "unencrypted provider must be loopback",
    )
    _require(not any(ord(c) < 33 for c in value), "invalid provider URL characters")
    _require(url.port is None or 0 < url.port < 65536, "invalid provider port")
    return url


class ProviderRelay:
    """Own the relay and stop all handlers before closing its evidence reference.

    Authentication is forwarded only to the bound upstream and is never retained
    in the journal. The relay does not follow redirects or accept alternate
    destinations. Network ownership still requires independent confinement.
    """

    def __init__(self, binding, *, dispatch, output, timeout_s, delegated_tools=()):
        self.url = verify_provider(binding)
        _require(
            type(timeout_s) in (int, float) and 0 < timeout_s < 86400, "invalid provider deadline"
        )
        self.authority = ProviderResponseAuthority(dispatch, delegated_tools=delegated_tools)
        self.output = Path(output).absolute()
        _require(self.output.resolve() == self.output, "redirected provider evidence")
        self.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        self._fd = os.open(self.output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._lock = threading.RLock()
        self._forward_lock = threading.Lock()
        self._handlers = threading.BoundedSemaphore(8)
        self._connections = set()
        self._artifacts = {}
        self._bytes = 0
        self._requests = 0
        self._forwards = 0
        self._closed = False
        self._failure = None
        self._retention_failed = False
        self.failed = threading.Event()
        self._deadline = time.monotonic() + timeout_s
        self._server = None
        self._worker = None
        try:
            self._retain(
                "invocation.json",
                {
                    "schema_version": "aisle.provider-relay.v1",
                    "binding": binding,
                    "delegated_tools": sorted([list(tool) for tool in delegated_tools], key=repr),
                    "session_id": dispatch.session_id,
                    "complete_coverage": False,
                    "confinement_verified": False,
                },
            )
        except BaseException:
            os.close(self._fd)
            self._fd = None
            raise

    def _retain(self, name, value):
        raw = (
            value
            if type(value) is bytes
            else json.dumps(value, allow_nan=False, sort_keys=True).encode() + b"\n"
        )
        with self._lock:
            try:
                _require(
                    self._bytes + len(raw) <= MAX_EVIDENCE_BYTES, "provider evidence limit exceeded"
                )
                fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self._fd,
                )
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(self._fd)
                self._artifacts[name] = hashlib.sha256(raw).hexdigest()
                self._bytes += len(raw)
            except BaseException:
                self._retention_failed = True
                raise

    def _fail(self, error):
        with self._lock:
            if self._failure is None:
                self._failure = type(error).__name__
            self.failed.set()

    def _check(self):
        _require(not self._closed and not self.failed.is_set(), "provider relay retired")
        if time.monotonic() >= self._deadline:
            raise TimeoutError("provider session deadline expired")

    def _handle(self, handler):
        upstream = None
        prefix = None
        started = False
        count = 0
        sequence = None
        error = None
        chunks = []
        source_retained = False
        try:
            self._check()
            _require(handler.path == "/v1/responses", "unsupported provider route")
            _require(
                not handler.headers.get_all("Transfer-Encoding"), "chunked provider request refused"
            )
            lengths = handler.headers.get_all("Content-Length", [])
            _require(len(lengths) == 1 and lengths[0].isdigit(), "invalid provider request length")
            length = int(lengths[0])
            _require(0 < length <= MAX_FRAME_BYTES, "unbounded provider request")
            with self._lock:
                _require(self._requests < 10000, "provider request count exceeded")
                self._requests += 1
                prefix = f"{self._requests:08d}"
            handler.connection.settimeout(1)
            raw = handler.rfile.read(length)
            _require(len(raw) == length, "truncated provider request")
            self._retain(prefix + "-request.body", raw)
            headers = {
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Accept-Encoding": "identity",
            }
            for key in (
                "Authorization",
                "Content-Encoding",
                "OpenAI-Beta",
                "OpenAI-Organization",
                "OpenAI-Project",
            ):
                values = handler.headers.get_all(key, [])
                _require(len(values) <= 1, "duplicate provider header")
                if values:
                    headers[key] = values[0]
            self._retain(
                prefix + "-request.json",
                {
                    "content_encoding": headers.get("Content-Encoding", "identity"),
                    "authorization_present": "Authorization" in headers,
                },
            )
            connection = (
                http.client.HTTPSConnection
                if self.url.scheme == "https"
                else http.client.HTTPConnection
            )
            self._check()
            upstream = connection(
                self.url.hostname,
                self.url.port,
                timeout=max(0.01, self._deadline - time.monotonic()),
            )
            with self._lock:
                self._connections.add(upstream)
            upstream.request("POST", self.url.path.rstrip("/") + "/responses", raw, headers)
            response = upstream.getresponse()
            _require(response.status == 200, "provider returned non-success status")
            _require(
                response.getheader("Content-Type", "").split(";", 1)[0].strip()
                == "text/event-stream",
                "provider did not return SSE",
            )
            _require(
                response.getheader("Content-Encoding", "identity") == "identity",
                "compressed provider response refused",
            )
            size = 0
            while True:
                self._check()
                part = response.read1(min(65536, MAX_FRAME_BYTES + 1 - size))
                if not part:
                    break
                chunks.append(part)
                size += len(part)
                _require(size <= MAX_FRAME_BYTES, "unbounded provider response")
            source = b"".join(chunks)
            self._retain(prefix + "-response.sse", source)
            source_retained = True
            with self._forward_lock:
                self._check()
                self._forwards += 1
                sequence = self._forwards

                def deliver(frame):
                    nonlocal started, count
                    self._check()
                    if not started:
                        handler.send_response(200)
                        handler.send_header("Content-Type", "text/event-stream")
                        handler.send_header("Connection", "close")
                        handler.end_headers()
                        started = True
                    handler.wfile.write(frame)
                    handler.wfile.flush()
                    count += 1

                self.authority.forward(source, deliver)
        except BaseException as exc:
            error = type(exc).__name__
            self._fail(exc)
            if not started:
                try:
                    handler.send_error(502, "provider admission unavailable")
                except OSError:
                    pass
        finally:
            handler.close_connection = True
            if upstream is not None:
                upstream.close()
            with self._lock:
                self._connections.discard(upstream)
            if prefix is not None:
                try:
                    if chunks and not source_retained:
                        self._retain(prefix + "-response-partial.sse", b"".join(chunks))
                    self._retain(
                        prefix + "-delivery.json",
                        {
                            "sequence": sequence,
                            "events_returned": count,
                            "error_type": error,
                        },
                    )
                except BaseException as exc:
                    self._fail(exc)

    def __enter__(self):
        relay = self

        class Server(ThreadingHTTPServer):
            # ThreadingHTTPServer defaults to daemon handlers. Evidence may not
            # close while an accepted request still owns work or a byte writer.
            daemon_threads = False

            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(1)
                with relay._lock:
                    if relay._closed:
                        connection.close()
                        raise OSError("provider relay is closing")
                    relay._connections.add(connection)
                return connection, address

            def process_request_thread(self, request, client_address):
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    with relay._lock:
                        relay._connections.discard(request)
                    relay._handlers.release()

            def process_request(self, request, client_address):
                if not relay._handlers.acquire(blocking=False):
                    relay._fail(ValueError("too many concurrent provider requests"))
                    self.shutdown_request(request)
                    with relay._lock:
                        relay._connections.discard(request)
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    relay._handlers.release()
                    with relay._lock:
                        relay._connections.discard(request)
                    raise

            def handle_error(self, request, client_address):
                relay._fail(ValueError("provider request handler failed"))

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.admission_handled = False
                super().setup()

            def finish(self):
                try:
                    super().finish()
                finally:
                    if not self.admission_handled:
                        relay._fail(ValueError("provider request did not reach admission"))

            def log_message(self, *_):
                pass

            def do_POST(self):
                self.admission_handled = True
                relay._handle(self)

        try:
            self._server = Server(("127.0.0.1", 0), Handler)
            self.address = self._server.server_address
            self._worker = threading.Thread(
                target=lambda: self._server.serve_forever(poll_interval=0.05)
            )
            self._worker.start()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        self._closed = True
        if self._worker is not None:
            self._server.shutdown()
            self._worker.join()
        with self._lock:
            connections = list(self._connections)
        if connections:
            self._fail(ValueError("provider request cancelled during shutdown"))
        for connection in connections:
            try:
                if isinstance(connection, socket.socket):
                    connection.shutdown(socket.SHUT_RDWR)
                elif connection.sock is not None:
                    connection.sock.shutdown(socket.SHUT_RDWR)
                connection.close()
            except OSError:
                pass
        if self._server is not None:
            self._server.server_close()
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def reference(self):
        _require(self._closed and self._fd is None, "provider reference requires closed relay")
        _require(not self._retention_failed, "provider evidence retention failed")
        return {
            "artifacts": dict(self._artifacts),
            "bytes": self._bytes,
            "failure": self._failure,
            "complete_coverage": False,
            "confinement_verified": False,
        }
