"""MON-8/MON-12/MON-13: retain actual nested dispatch and hook coverage limits."""

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


def _frame():
    script = (
        'await tools.exec_command({cmd:"printf first > nested-first.txt",login:false}); '
        'await tools.exec_command({cmd:"printf second > nested-second.txt",login:false});'
    )
    item = {
        "type": "custom_tool_call",
        "id": "ct_nested",
        "call_id": "call_nested",
        "name": "exec",
        "input": script,
    }
    response = {
        "id": "resp_nested",
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


@pytest.mark.parametrize("mode", ["baseline", "deny", "malformed"])
def test_actual_nested_dispatch_keeps_hook_failure_coverage_incomplete(tmp_path, monkeypatch, mode):
    """MON-8/MON-12/MON-13: wrapper authorization cannot attest nested tool enforcement."""
    if sys.platform != "darwin":
        pytest.skip("requires the actual macOS frontend fixture sandbox")
    binary = os.environ.get("AISLE_CODEX_PROBE_BINARY")
    if not binary:
        pytest.skip("requires an explicitly selected actual Codex binary")
    import frontend_codex_probe as probe

    from aisle.harness.frontend_dispatch import DispatchBudget
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    authority = tmp_path / "authority"
    with DispatchBudget(authority, session_id="nested-dispatch", ceiling=1) as budget:

        def factory(requests, errors, output):
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    raw = self.rfile.read(int(self.headers["Content-Length"]))
                    if self.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    body = json.loads(raw)
                    requests.append(
                        {
                            "authorization_present": "Authorization" in self.headers,
                            "tools": body.get("tools", []),
                            "tool_outputs": [
                                row
                                for row in body.get("input", [])
                                if row.get("type") == "custom_tool_call_output"
                            ],
                        }
                    )

                    def deliver(data):
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        self.wfile.flush()

                    if len(requests) == 1:
                        budget.dispatch(
                            {"turn_id": "turn", "call_id": "call_nested", "tool_name": "exec"},
                            _frame(),
                            deliver,
                        )
                    else:
                        deliver(probe._events(False))

            return HTTPServer(("127.0.0.1", 0), Handler)

        monkeypatch.setattr(probe, "_server", factory)
        probe.run_probe(binary, tmp_path / "frontend", mode, code_mode=True)

    output = tmp_path / "frontend"
    evidence = json.loads((output / "evidence.json").read_text())
    assert not evidence["timed_out"], evidence
    assert evidence["rc"] == 0, evidence
    assert len(evidence["requests"]) == 2, evidence
    assert all(not row["authorization_present"] for row in evidence["requests"])
    assert any(
        row.get("name") == "exec" and row.get("type") == "custom"
        for row in evidence["requests"][0]["tools"]
    )
    replies = evidence["requests"][1]["tool_outputs"]
    assert len(replies) == 1 and replies[0]["call_id"] == "call_nested", replies
    reply = json.dumps(replies[0]["output"])
    markers = {p.name: p.read_text() for p in (output / "workspace").glob("nested-*.txt")}
    if mode == "deny":
        assert markers == {}
        assert "Script failed" in reply and "blocked by PreToolUse hook" in reply
    else:
        assert markers == {"nested-first.txt": "first", "nested-second.txt": "second"}
        assert "Script completed" in reply
    if mode == "baseline":
        assert evidence["hook"] is None
    else:
        hook = evidence["hook"]
        assert hook["hook_event_name"] == "PreToolUse" and hook["tool_name"] == "Bash"
        expected = "first" if mode == "deny" else "second"
        assert hook["tool_input"]["command"] == f"printf {expected} > nested-{expected}.txt"
        assert hook["tool_use_id"].startswith("exec-")
    invocation = json.loads((output / "invocation.json").read_text())
    assert invocation["code_mode_enabled"] is True
    assert "features.code_mode=true" in invocation["argv"]
    snapshot = json.loads((output / "fixture-preflight.json").read_text())
    assert not probe._fixture_errors(output, snapshot)
    artifacts = {p.name: p.read_bytes() for p in authority.iterdir()}
    audit = verify_dispatch_journal(
        artifacts,
        expected={
            "session_id": "nested-dispatch",
            "ceiling": 1,
            "attempts": 1,
            "reserved": 1,
            "artifacts": {
                name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()
            },
        },
        byte_limit=65536,
    )
    assert audit["ok"], audit
    assert audit["complete_coverage"] is False
    assert audit["confinement_verified"] is False
