"""Owned loopback MCP HTTP transport for authenticated harness requests."""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from aisle.harness.mcp_harness_source import MAX_FRAME_BYTES, _decode


class MCPHarnessServer:
    """Stateless MCP connections share one session authority and bounded lifetime."""

    def __init__(self, authority, *, operations):
        if (
            not operations
            or len(set(operations)) != len(operations)
            or set(operations) - {"check", "run"}
        ):
            raise ValueError("invalid MCP harness operations")
        self.authority = authority
        self.operations = tuple(operations)
        self._connections = set()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(8)
        self._server = None
        self._worker = None

    @property
    def connections(self):
        with self._lock:
            return set(self._connections)

    @property
    def address(self):
        return self._server.server_address

    def __enter__(self):
        owner = self

        class Server(ThreadingHTTPServer):
            daemon_threads = False

            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(1)
                with owner._lock:
                    owner._connections.add(connection)
                return connection, address

            def process_request(self, request, address):
                if not owner._slots.acquire(blocking=False):
                    with owner.authority._condition:
                        owner.authority._fail(ValueError("MCP handler limit exceeded"))
                    self.shutdown_request(request)
                    with owner._lock:
                        owner._connections.discard(request)
                    return
                try:
                    super().process_request(request, address)
                except BaseException:
                    owner._slots.release()
                    raise

            def process_request_thread(self, request, address):
                try:
                    super().process_request_thread(request, address)
                finally:
                    with owner._lock:
                        owner._connections.discard(request)
                    owner._slots.release()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def respond(self, status, value=None):
                raw = b"" if value is None else json.dumps(value, allow_nan=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
                self.close_connection = True

            def do_GET(self):
                self.respond(405)

            def do_DELETE(self):
                self.respond(405)

            def do_POST(self):
                identity = None
                try:
                    lengths = self.headers.get_all("Content-Length", [])
                    if (
                        self.path != "/mcp"
                        or self.headers.get_all("Transfer-Encoding")
                        or len(lengths) != 1
                        or not lengths[0].isdigit()
                        or not 0 < int(lengths[0]) <= MAX_FRAME_BYTES
                    ):
                        raise ValueError("invalid MCP HTTP frame")
                    raw = self.rfile.read(int(lengths[0]))
                    if len(raw) != int(lengths[0]):
                        raise ValueError("truncated MCP HTTP frame")
                    request = _decode(raw)
                    if request.get("jsonrpc") != "2.0":
                        raise ValueError("invalid MCP protocol version")
                    if "id" not in request:
                        if request.get("method") != "notifications/initialized":
                            raise ValueError("unsupported MCP notification")
                        self.respond(202)
                        return
                    identity = request["id"]
                    if type(identity) not in (str, int):
                        raise ValueError("invalid MCP request identity")
                    method = request.get("method")
                    if method == "initialize":
                        result = {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "aisle-harness", "version": "1"},
                        }
                    elif method == "tools/list":
                        result = {
                            "tools": [
                                {
                                    "name": op,
                                    "description": "Request the admitted harness " + op,
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {},
                                        "additionalProperties": False,
                                    },
                                }
                                for op in owner.operations
                            ]
                        }
                    elif method == "tools/call":
                        value = owner.authority.dispatch(raw)
                        result = {
                            "isError": not value["success"],
                            "content": [
                                {"type": "text", "text": item["text"]}
                                for item in value["contentItems"]
                            ],
                        }
                    else:
                        self.respond(
                            200,
                            {
                                "jsonrpc": "2.0",
                                "id": identity,
                                "error": {"code": -32601, "message": "unsupported MCP method"},
                            },
                        )
                        return
                    self.respond(200, {"jsonrpc": "2.0", "id": identity, "result": result})
                except Exception as exc:
                    with owner.authority._condition:
                        owner.authority._fail(exc)
                    try:
                        self.respond(
                            200,
                            {
                                "jsonrpc": "2.0",
                                "id": identity,
                                "error": {
                                    "code": -32000,
                                    "message": "MCP harness authorization unavailable",
                                },
                            },
                        )
                    except OSError:
                        pass

        self._server = Server(("127.0.0.1", 0), Handler)
        self._worker = threading.Thread(
            target=lambda: self._server.serve_forever(poll_interval=0.05)
        )
        self._worker.start()
        return self

    def __exit__(self, *args):
        self.authority.begin_shutdown()
        self._server.shutdown()
        for connection in self.connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._server.server_close()
        self._worker.join()
