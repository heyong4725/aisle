"""MON-8/MON-12/MON-13: native editing requires model-bound route evidence."""

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
PATCH = "*** Begin Patch\n*** Add File: patch-marker.txt\n+fixture patch\n*** End Patch"


def _frame():
    item = {
        "type": "custom_tool_call",
        "id": "ct_patch",
        "call_id": "call_patch",
        "name": "apply_patch",
        "input": PATCH,
    }
    response = {
        "id": "resp_patch",
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


@pytest.mark.parametrize(
    ("native_edit", "mode"),
    [(False, "baseline"), (True, "baseline"), (True, "deny"), (True, "malformed")],
)
def test_native_edit_availability_and_hook_failures_remain_partial(
    tmp_path, monkeypatch, native_edit, mode
):
    """MON-8/MON-12/MON-13: unavailable and fail-open routes cannot attest complete coverage."""
    if sys.platform != "darwin":
        pytest.skip("requires the actual macOS frontend fixture sandbox")
    binary = os.environ.get("AISLE_CODEX_PROBE_BINARY")
    if not binary:
        pytest.skip("requires an explicitly selected actual Codex binary")
    import frontend_codex_probe as probe

    from aisle.harness.frontend_dispatch import DispatchBudget
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    authority = tmp_path / "authority"
    with DispatchBudget(authority, session_id="native-edit", ceiling=1) as budget:

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
                            "model": body.get("model"),
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
                            {
                                "turn_id": "turn",
                                "call_id": "call_patch",
                                "tool_name": "apply_patch",
                            },
                            _frame(),
                            deliver,
                        )
                    else:
                        deliver(probe._events(False))

            return HTTPServer(("127.0.0.1", 0), Handler)

        monkeypatch.setattr(probe, "_server", factory)
        probe.run_probe(binary, tmp_path / "frontend", mode, native_edit=native_edit)

    output = tmp_path / "frontend"
    evidence = json.loads((output / "evidence.json").read_text())
    assert evidence["rc"] == 0 and not evidence["timed_out"], evidence
    requests = evidence["requests"]
    assert len(requests) == 2 and all(not row["authorization_present"] for row in requests)
    assert all(row["model"] == ("gpt-5.4" if native_edit else "aisle-fixture") for row in requests)
    assert (
        any(
            row.get("name") == "apply_patch" and row.get("type") == "custom"
            for row in requests[0]["tools"]
        )
        is native_edit
    )
    replies = requests[1]["tool_outputs"]
    assert len(replies) == 1 and replies[0]["call_id"] == "call_patch", replies
    reply = replies[0]["output"]
    marker = output / "workspace/patch-marker.txt"
    if not native_edit:
        assert reply == "unsupported custom tool call: apply_patch"
        assert not marker.exists()
    elif mode == "deny":
        assert reply.startswith("Command blocked by PreToolUse hook:")
        assert not marker.exists()
    else:
        assert "Success. Updated the following files:" in reply
        assert marker.read_text() == "fixture patch\n"
    if mode == "baseline":
        assert evidence["hook"] is None
    else:
        hook = evidence["hook"]
        assert hook["hook_event_name"] == "PreToolUse" and hook["tool_name"] == "apply_patch"
        assert hook["tool_use_id"] == "call_patch" and hook["tool_input"] == {"command": PATCH}
    invocation = json.loads((output / "invocation.json").read_text())
    assert invocation["native_edit_enabled"] is native_edit
    if mode != "baseline":
        assert any('matcher="apply_patch"' in arg for arg in invocation["argv"])
    snapshot = json.loads((output / "fixture-preflight.json").read_text())
    assert not probe._fixture_errors(output, snapshot)
    artifacts = {p.name: p.read_bytes() for p in authority.iterdir()}
    audit = verify_dispatch_journal(
        artifacts,
        expected={
            "session_id": "native-edit",
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
    assert audit["complete_coverage"] is False and audit["confinement_verified"] is False
