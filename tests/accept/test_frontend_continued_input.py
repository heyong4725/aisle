"""MON-8/MON-12/MON-13: continued process input requires its own reservation."""

import gzip
import hashlib
import json
import os
import re
import shlex
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.accept


def _frame(index, name, arguments):
    item = {
        "id": f"fc_{index}",
        "type": "function_call",
        "call_id": f"call_{index}",
        "name": name,
        "arguments": json.dumps(arguments),
    }
    response = {
        "id": f"resp_{index}",
        "object": "response",
        "status": "completed",
        "output": [item],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    events = [
        {
            "type": "response.created",
            "response": {**response, "status": "in_progress", "output": []},
        },
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response},
    ]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


@pytest.mark.parametrize("refuse_input", [False, True])
def test_actual_continued_input_requires_a_separate_dispatch_reservation(
    tmp_path, monkeypatch, refuse_input
):
    """MON-8/MON-13: denying write_stdin prevents input to a successfully started process."""
    if sys.platform != "darwin":
        pytest.skip("requires the actual macOS frontend fixture sandbox")
    binary = os.environ.get("AISLE_CODEX_PROBE_BINARY")
    if not binary:
        pytest.skip("requires an explicitly selected actual Codex binary")
    import frontend_codex_probe as probe

    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    code = (
        "import pathlib,select,sys; "
        "pathlib.Path('ready.txt').write_text('ready'); "
        "ready=select.select([sys.stdin],[],[],8)[0]; "
        "value=sys.stdin.readline() if ready else ''; "
        "pathlib.Path('input.txt').write_text(value) if value else None"
    )
    command = shlex.join([sys.executable, "-I", "-B", "-c", code])
    authority = tmp_path / "authority"
    refused, sessions = [], []
    ceiling = 1 if refuse_input else 2
    with DispatchBudget(authority, session_id="continued-input", ceiling=ceiling) as budget:

        def factory(requests, errors, output):
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    raw = self.rfile.read(int(self.headers["Content-Length"]))
                    if self.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    body = json.loads(raw)
                    replies = [
                        row
                        for row in body.get("input", [])
                        if row.get("type") == "function_call_output"
                    ]
                    requests.append(
                        {
                            "authorization_present": "Authorization" in self.headers,
                            "tool_outputs": replies,
                        }
                    )
                    index = len(requests)

                    def deliver(data):
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        self.wfile.flush()

                    if index > 2:
                        deliver(probe._events(False))
                        return
                    if index == 1:
                        name = "exec_command"
                        arguments = {
                            "cmd": command,
                            "login": False,
                            "tty": True,
                            "yield_time_ms": 1000,
                            "max_output_tokens": 1000,
                        }
                    else:
                        reply = next(
                            (row["output"] for row in replies if row["call_id"] == "call_1"), ""
                        )
                        match = re.search(r"Process running with session ID (\d+)", reply)
                        if match is None:
                            errors.append(
                                "first tool did not retain an interactive session: " + reply
                            )
                            self.send_error(400, "interactive session unavailable")
                            return
                        sessions.append(int(match.group(1)))
                        name = "write_stdin"
                        arguments = {
                            "session_id": sessions[-1],
                            "chars": "delivered\n",
                            "yield_time_ms": 1000,
                            "max_output_tokens": 1000,
                        }
                    try:
                        budget.dispatch(
                            {"turn_id": "turn", "call_id": f"call_{index}", "tool_name": name},
                            _frame(index, name, arguments),
                            deliver,
                        )
                    except DispatchRefused:
                        refused.append(index)
                        self.send_error(403, "dispatch authorization unavailable")

            return HTTPServer(("127.0.0.1", 0), Handler)

        monkeypatch.setattr(probe, "_server", factory)
        probe.run_probe(binary, tmp_path / "frontend", "baseline", allow_pty=True)

    evidence = json.loads((tmp_path / "frontend/evidence.json").read_text())
    assert not evidence["timed_out"], evidence
    assert all(not row["authorization_present"] for row in evidence["requests"])
    invocation = json.loads((tmp_path / "frontend/invocation.json").read_text())
    assert invocation["pty_enabled"] is True
    snapshot = json.loads((tmp_path / "frontend/fixture-preflight.json").read_text())
    assert not probe._fixture_errors(tmp_path / "frontend", snapshot)
    assert sessions, evidence
    assert (tmp_path / "frontend/workspace/ready.txt").read_text() == "ready"
    marker = tmp_path / "frontend/workspace/input.txt"
    if refuse_input:
        assert not marker.exists()
        assert refused == [2]
        assert evidence["rc"] != 0
    else:
        assert marker.read_text() == "delivered\n"
        assert not refused
        assert evidence["rc"] == 0
    artifacts = {path.name: path.read_bytes() for path in authority.iterdir()}
    audit = verify_dispatch_journal(
        artifacts,
        expected={
            "session_id": "continued-input",
            "ceiling": ceiling,
            "attempts": 2,
            "reserved": ceiling,
            "artifacts": {
                name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()
            },
        },
        byte_limit=65536,
    )
    assert audit["ok"], audit
    assert audit["complete_coverage"] is False
    assert audit["confinement_verified"] is False
