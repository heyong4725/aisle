"""MON-8/MON-12/MON-13: MCP calls require separate dispatch reservations."""

import gzip
import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.accept


def _frame(index):
    item = {
        "id": f"fc_{index}",
        "type": "function_call",
        "call_id": f"call_{index}",
        "name": "record",
        "namespace": "mcp__aisle_fixture",
        "arguments": "{}",
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


@pytest.mark.parametrize("refuse_second", [False, True])
def test_actual_mcp_calls_require_separate_dispatch_reservations(
    tmp_path, monkeypatch, refuse_second
):
    """MON-8/MON-12/MON-13: denial prevents the second actual MCP call and its side effect."""
    if sys.platform != "darwin":
        pytest.skip("requires the actual macOS frontend fixture sandbox")
    binary = os.environ.get("AISLE_CODEX_PROBE_BINARY")
    if not binary:
        pytest.skip("requires an explicitly selected actual Codex binary")
    import frontend_codex_probe as probe

    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    authority = tmp_path / "authority"
    refused = []
    ceiling = 1 if refuse_second else 2
    with DispatchBudget(authority, session_id="mcp-dispatch", ceiling=ceiling) as budget:

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
                            "tools": body.get("tools", []),
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
                    try:
                        budget.dispatch(
                            {
                                "turn_id": "turn",
                                "call_id": f"call_{index}",
                                "tool_name": "mcp__aisle_fixture.record",
                            },
                            _frame(index),
                            deliver,
                        )
                    except DispatchRefused:
                        refused.append(index)
                        self.send_error(403, "dispatch authorization unavailable")

            return HTTPServer(("127.0.0.1", 0), Handler)

        monkeypatch.setattr(probe, "_server", factory)
        probe.run_probe(binary, tmp_path / "frontend", "baseline", mcp_fixture=True)

    evidence = json.loads((tmp_path / "frontend/evidence.json").read_text())
    assert not evidence["timed_out"], evidence
    assert all(not row["authorization_present"] for row in evidence["requests"])
    namespace = next(
        row for row in evidence["requests"][0]["tools"] if row.get("name") == "mcp__aisle_fixture"
    )
    assert namespace["type"] == "namespace"
    assert [tool["name"] for tool in namespace["tools"]] == ["record"]
    invocation = json.loads((tmp_path / "frontend/invocation.json").read_text())
    assert invocation["mcp_enabled"] is True
    snapshot = json.loads((tmp_path / "frontend/fixture-preflight.json").read_text())
    assert not probe._fixture_errors(tmp_path / "frontend", snapshot)
    calls = [
        json.loads(line)
        for line in (tmp_path / "frontend/mcp-requests.jsonl").read_text().splitlines()
    ]
    calls = [row for row in calls if row.get("method") == "tools/call"]
    count = 1 if refuse_second else 2
    assert len(calls) == count, calls
    assert [row["params"]["_meta"]["callId"] for row in calls] == [
        f"call_{i}" for i in range(1, count + 1)
    ]
    assert [row["params"]["_meta"]["itemId"] for row in calls] == [
        f"fc_{i}" for i in range(1, count + 1)
    ]
    markers = set((tmp_path / "frontend/workspace").glob("mcp-marker-*.txt"))
    assert {path.name for path in markers} == {f"mcp-marker-{i}.txt" for i in range(1, count + 1)}
    assert all(path.read_text() == "fixture" for path in markers)
    assert refused == ([2] if refuse_second else [])
    assert evidence["rc"] == (1 if refuse_second else 0)
    assert "frontend_mcp_fixture.py" in snapshot
    assert "mcp-runtime.json" in snapshot
    runtime = json.loads((tmp_path / "frontend/mcp-runtime.json").read_text())
    assert runtime["python_sha256"] == probe._sha(runtime["python"])
    artifacts = {path.name: path.read_bytes() for path in authority.iterdir()}
    audit = verify_dispatch_journal(
        artifacts,
        expected={
            "session_id": "mcp-dispatch",
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
