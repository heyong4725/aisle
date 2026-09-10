"""MON-8/MON-12/MON-13: HTTP delivery uses the owned shared admission boundary."""

import hashlib
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from aisle.harness.frontend_dispatch import DispatchBudget
from aisle.harness.provider_relay import ProviderRelay

pytestmark = pytest.mark.accept


def response_frame(count):
    items = [
        {
            "id": f"item-{i}",
            "type": "function_call",
            "call_id": f"call-{i}",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": f"touch marker-{i}"}),
        }
        for i in range(count)
    ]
    events = [{"type": "response.created", "response": {"id": "response"}}]
    events.extend(
        {"type": "response.output_item.done", "output_index": i, "item": item}
        for i, item in enumerate(items)
    )
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": "response",
                "status": "completed",
                "output": items,
            },
        }
    )
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


@pytest.mark.parametrize("case", ["success", "ceiling", "malformed"])
def test_http_relay_keeps_exact_source_and_admitted_prefix(tmp_path, case):
    """MON-13: real sockets cannot deliver a second executable item on one reservation."""
    frame = response_frame(2)
    if case == "malformed":
        frame = frame.replace(b'"status": "completed"', b'"status": "failed"')
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frame)))
            self.end_headers()
            self.wfile.write(frame)

    upstream = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=lambda: upstream.serve_forever(poll_interval=0.05))
    worker.start()
    root = tmp_path / "provider"
    try:
        with DispatchBudget(
            tmp_path / "budget", session_id="session", ceiling=1 if case == "ceiling" else 2
        ) as budget:
            with ProviderRelay(
                {"base_url": f"http://127.0.0.1:{upstream.server_port}/v1"},
                dispatch=budget,
                output=root,
                timeout_s=5,
            ) as relay:
                client = http.client.HTTPConnection(*relay.address, timeout=5)
                body = b'{"model":"fixture","stream":true,"input":[],"tools":[]}'
                client.request("POST", "/v1/responses", body, {"Content-Type": "application/json"})
                reply = client.getresponse()
                delivered = reply.read()
                client.close()
            reference = relay.reference()
        assert requests == [body]
        assert reference["artifacts"] == {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir()
        }
        if case == "success":
            assert reply.status == 200 and delivered == frame
            assert reference["failure"] is None
            assert budget.reference()["reserved"] == 2
            from aisle.harness.provider_source_audit import verify_provider_sources

            source = {path.name: path.read_bytes() for path in root.iterdir()}
            dispatch = {
                "artifacts": {
                    path.name: path.read_bytes() for path in (tmp_path / "budget").iterdir()
                },
                "expected": budget.reference(),
                "byte_limit": 4 * 1024 * 1024,
            }
            options = dict(
                expected=reference,
                dispatch=dispatch,
                byte_limit=4 * 1024 * 1024,
                delegated_tools=set(),
            )
            audited = verify_provider_sources(source, **options)
            assert audited["ok"] and audited["native_attempts"] == [1, 2], audited
            altered = dict(source)
            altered["00000001-response.sse"] = frame.replace(b"touch marker-0", b"touch changed0")
            assert not verify_provider_sources(altered, **options)["ok"]
            # Even a separately authenticated but inconsistent source reference
            # cannot link different call bytes to the existing reservations.
            replacement = {
                **reference,
                "bytes": sum(map(len, altered.values())),
                "artifacts": {
                    name: hashlib.sha256(raw).hexdigest() for name, raw in altered.items()
                },
            }
            assert not verify_provider_sources(altered, **{**options, "expected": replacement})[
                "ok"
            ]
        elif case == "ceiling":
            assert b'"call-0"' in delivered and b'"call-1"' not in delivered
            assert b"response.completed" not in delivered
            assert reference["failure"] == "DispatchRefused"
        else:
            assert b"response.output_item.done" not in delivered
            assert reference["failure"] == "ValueError"
    finally:
        upstream.shutdown()
        upstream.server_close()
        worker.join()


def test_shutdown_reaps_a_client_that_never_finishes_headers(tmp_path):
    """MON-13: cancellation owns accepted sockets before HTTP request parsing finishes."""
    import socket

    entered = threading.Event()
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=1) as budget:
        relay = ProviderRelay(
            {"base_url": "http://127.0.0.1:1/v1"},
            dispatch=budget,
            output=tmp_path / "provider",
            timeout_s=5,
        ).__enter__()
        original = relay._server.finish_request

        def observed(*args):
            entered.set()
            return original(*args)

        relay._server.finish_request = observed
        client = socket.create_connection(relay.address, timeout=2)
        stopped = threading.Thread(target=lambda: relay.__exit__(None, None, None))
        try:
            client.sendall(b"POST /v1/responses HTTP/1.1\r\n")
            assert entered.wait(2)
            stopped.start()
            stopped.join(0.5)
            still_running = stopped.is_alive()
        finally:
            client.close()
            stopped.join(2)
        assert not stopped.is_alive(), "relay shutdown left a handler running"
        assert not still_running, "shutdown waited for client input instead of closing its socket"
        assert relay.reference()["failure"] is not None


def test_upstream_timeout_retains_received_prefix(tmp_path):
    """MON-12/MON-13: a timed-out provider response keeps its acquired failure evidence."""
    release = threading.Event()
    prefix = (
        b'event: response.created\ndata: {"type":"response.created","response":{"id":"slow"}}\n\n'
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(prefix)
            self.wfile.flush()
            release.wait(3)

    upstream = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: upstream.serve_forever(poll_interval=0.05))
    thread.start()
    root = tmp_path / "provider"
    try:
        with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=1) as budget:
            with ProviderRelay(
                {"base_url": f"http://127.0.0.1:{upstream.server_port}/v1"},
                dispatch=budget,
                output=root,
                timeout_s=0.25,
            ) as relay:
                client = http.client.HTTPConnection(*relay.address, timeout=2)
                client.request("POST", "/v1/responses", b"{}")
                response = client.getresponse()
                response.read()
                client.close()
            assert relay.reference()["failure"] == "TimeoutError"
        assert (root / "00000001-response-partial.sse").read_bytes() == prefix
        assert budget.reference()["reserved"] == 0
    finally:
        release.set()
        upstream.shutdown()
        upstream.server_close()
        thread.join()
