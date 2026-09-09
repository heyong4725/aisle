"""MON-8/MON-12/MON-13: reserve actual Claude Bash delivery before side effects."""

import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.accept


@pytest.mark.parametrize("failure", [None, "ceiling", "retention"])
def test_claude_requires_retained_reservation_before_second_bash_call(
    tmp_path, monkeypatch, failure
):
    """MON-8/MON-13: normal first execution distinguishes refusal from startup failure."""
    if sys.platform != "darwin":
        pytest.skip("actual Claude fixture requires the macOS outer sandbox")
    binary = os.environ.get("AISLE_CLAUDE_PROBE_BINARY")
    if not binary:
        pytest.skip("set AISLE_CLAUDE_PROBE_BINARY to the explicitly selected CLI")
    import frontend_claude_probe as probe

    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    authority = tmp_path / "authority"
    ceiling = 1 if failure == "ceiling" else 2
    refused = []
    server_errors = []
    with DispatchBudget(authority, session_id="claude-dispatch", ceiling=ceiling) as budget:
        retain = budget._retain
        if failure == "retention":

            def failing_retain(name, data):
                if name == "00000002-reservation.json":
                    raise OSError("injected reservation retention failure")
                retain(name, data)

            monkeypatch.setattr(budget, "_retain", failing_retain)

        def factory(requests, errors, output):
            issued = 0

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    nonlocal issued
                    try:
                        self.connection.settimeout(5)
                        length = int(self.headers.get("Content-Length", "-1"))
                        if (
                            self.path != "/v1/messages?beta=true"
                            or not 0 <= length <= probe.common.LIMIT
                            or len(requests) >= 8
                            or self.headers.get("Content-Encoding") is not None
                        ):
                            raise ValueError("unexpected fixture request")
                        body = json.loads(self.rfile.read(length))
                        selected = {
                            "fixture_key": self.headers.get("x-api-key") == "aisle-fixture-key",
                            "authorization_present": "Authorization" in self.headers,
                            "tools": body.get("tools", []),
                            "results": [
                                block
                                for msg in body.get("messages", [])
                                for block in (
                                    msg.get("content", [])
                                    if isinstance(msg.get("content"), list)
                                    else []
                                )
                                if block.get("type") == "tool_result"
                            ],
                        }
                        requests.append(selected)
                        probe.common._write(output / f"request-{len(requests)}.json", selected)
                        if not selected["fixture_key"] or selected["authorization_present"]:
                            raise ValueError("unexpected credentials")

                        def deliver(data):
                            (output / f"response-{len(requests)}.sse").write_bytes(data)
                            self.send_response(200)
                            self.send_header("Content-Type", "text/event-stream")
                            self.send_header("Content-Length", str(len(data)))
                            self.end_headers()
                            self.wfile.write(data)
                            self.wfile.flush()

                        if issued >= 2 or not probe.select_tool_call(body, issued=False):
                            deliver(probe._events(False, body["model"], len(requests)))
                            return
                        issued += 1
                        data = (
                            probe._events(True, body["model"], len(requests))
                            .replace(probe.CALL_ID.encode(), f"toolu_{issued}".encode())
                            .replace(b"marker.txt", f"marker-{issued}.txt".encode())
                        )
                        try:
                            budget.dispatch(
                                {
                                    "turn_id": "turn",
                                    "call_id": f"toolu_{issued}",
                                    "tool_name": "Bash",
                                },
                                data,
                                deliver,
                            )
                        except (DispatchRefused, OSError) as exc:
                            refused.append(type(exc).__name__)
                            self.send_error(403, "dispatch authorization unavailable")
                    except (OSError, ValueError, TypeError, AttributeError, KeyError) as exc:
                        message = type(exc).__name__ + ": " + str(exc)
                        errors.append(message)
                        server_errors.append(message)
                        self.send_error(400)

            return HTTPServer(("127.0.0.1", 0), Handler)

        monkeypatch.setattr(probe, "_server", factory)
        # The ordinary probe classifier expects one call. This acceptance test
        # verifies the two-call evidence directly, including fixture bindings.
        report = probe.run_probe(binary, tmp_path / "frontend", "baseline")

    output = tmp_path / "frontend"
    evidence = json.loads((output / "evidence.json").read_text())
    assert not evidence["timed_out"], evidence
    assert not server_errors
    assert not probe.common._fixture_errors(output, report["fixture_files"])
    assert probe.common._sha(Path(binary).resolve()) == report["binary_sha256"]
    assert all(r["fixture_key"] and not r["authorization_present"] for r in evidence["requests"])
    assert (output / "workspace/marker-1.txt").read_text() == "fixture"
    # Conversation history repeats results: inspect the final request instead
    # of adding overlapping counts across provider requests.
    results = evidence["requests"][-1]["results"]
    assert [r["tool_use_id"] for r in results] == (
        ["toolu_1", "toolu_2"] if failure is None else ["toolu_1"]
    ), evidence
    first = [r for r in results if r["tool_use_id"] == "toolu_1"]
    assert len(first) == 1 and first[0].get("is_error") is False, evidence
    second = output / "workspace/marker-2.txt"
    if failure is None:
        assert second.read_text() == "fixture"
        assert evidence["rc"] == 0 and not refused
        reply = [r for r in results if r["tool_use_id"] == "toolu_2"]
        assert len(reply) == 1 and reply[0].get("is_error") is False
    else:
        assert not second.exists()
        assert evidence["rc"] != 0
        assert refused == ["DispatchRefused" if failure == "ceiling" else "OSError"]
        assert not any(r["tool_use_id"] == "toolu_2" for r in results)
    artifacts = {path.name: path.read_bytes() for path in authority.iterdir()}
    audit = verify_dispatch_journal(
        artifacts,
        expected={
            "session_id": "claude-dispatch",
            "ceiling": ceiling,
            "attempts": 2,
            "reserved": 1 if failure == "ceiling" else 2,
            "artifacts": {
                name: hashlib.sha256(data).hexdigest() for name, data in artifacts.items()
            },
        },
        byte_limit=65536,
    )
    assert audit["ok"] is (failure != "retention"), audit
    assert audit["complete_coverage"] is False
    assert audit["confinement_verified"] is False
