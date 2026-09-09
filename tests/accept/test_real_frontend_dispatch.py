"""MON-8/MON-12/MON-13: actual CLI calls cannot pass a refused delivery reservation."""

import gzip
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.accept


def frames(index):
    item = {
        "id": f"fc_{index}",
        "type": "function_call",
        "call_id": f"call_{index}",
        "name": "exec_command",
        "arguments": json.dumps(
            {
                "cmd": f"printf fixture > marker-{index}.txt",
                "login": False,
                "yield_time_ms": 1000,
                "max_output_tokens": 1000,
            }
        ),
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


@pytest.mark.parametrize("failure", [None, "ceiling", "retention"])
def test_actual_cli_cannot_execute_second_call_without_retained_authorization(
    tmp_path, monkeypatch, failure
):
    """MON-8/MON-13: actual side effects distinguish refusal from a dead frontend."""
    if sys.platform != "darwin":
        pytest.skip("the actual CLI fixture requires its macOS outer sandbox")
    binary = os.environ.get("AISLE_CODEX_PROBE_BINARY")
    if not binary:
        pytest.skip("set AISLE_CODEX_PROBE_BINARY to the explicitly selected CLI")
    import frontend_codex_probe as probe

    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused

    # The authority is outside the frontend's writable output directory.
    authority = tmp_path / "authority"
    refused = []
    with DispatchBudget(
        authority, session_id="session", ceiling=1 if failure == "ceiling" else 2
    ) as budget:
        retain = budget._retain
        if failure == "retention":

            def failing_retain(name, data):
                if name == "00000002-reservation.json":
                    raise OSError("injected reservation retention failure")
                retain(name, data)

            monkeypatch.setattr(budget, "_retain", failing_retain)

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
                            "tool_outputs": [
                                v
                                for v in body.get("input", [])
                                if v.get("type") == "function_call_output"
                            ],
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
                                "tool_name": "exec_command",
                            },
                            frames(index),
                            deliver,
                        )
                    except (DispatchRefused, OSError) as exc:
                        refused.append(type(exc).__name__)
                        self.send_error(403, "dispatch authorization unavailable")

            return HTTPServer(("127.0.0.1", 0), Handler)

        monkeypatch.setattr(probe, "_server", factory)
        # This is a two-call acceptance scenario, not the helper's one-call
        # conformance profile. Check the retained process and effects below.
        probe.run_probe(binary, tmp_path / "frontend", "baseline")

    evidence = json.loads((tmp_path / "frontend/evidence.json").read_text())
    assert not evidence["timed_out"]
    assert all(not row["authorization_present"] for row in evidence["requests"])
    first = tmp_path / "frontend/workspace/marker-1.txt"
    second = tmp_path / "frontend/workspace/marker-2.txt"
    assert first.read_text() == "fixture", evidence
    replies = [value for row in evidence["requests"] for value in row["tool_outputs"]]
    assert any(
        row["call_id"] == "call_1" and "Process exited with code 0" in row["output"]
        for row in replies
    )
    if failure is None:
        assert second.read_text() == "fixture"
        assert evidence["rc"] == 0
        assert not refused
    else:
        assert not second.exists()
        assert evidence["rc"] != 0
        assert refused == ["DispatchRefused" if failure == "ceiling" else "OSError"]
    record = json.loads((authority / "00000001-reservation.json").read_text())
    assert record["decision"] == "authorized" and record["reserved_after"] == 1
    if failure == "ceiling":
        assert (
            json.loads((authority / "00000002-reservation.json").read_text())["decision"]
            == "refused"
        )
