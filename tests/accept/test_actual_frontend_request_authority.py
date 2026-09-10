"""MON-8/MON-12/MON-13: actual frontend delivery of trusted request grants.

A scripted local provider delegates one harness check through exec_command.
This verifies capability transport, not production provider authentication,
complete frontend coverage, or independently reviewed confinement.
"""

import gzip
import json
import os
import shlex
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from test_frontend_continued_input import _frame

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
pytestmark = pytest.mark.accept


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("delivery", ["valid", "altered", "unissued"])
def test_actual_frontend_delivers_only_the_authorized_request(tmp_path, monkeypatch, arm, delivery):
    """MON-8/MON-13: modified or invented grants cannot start either arm's check."""
    if sys.platform != "darwin":
        pytest.skip("requires the actual macOS frontend fixture sandbox")
    binary = os.environ.get("AISLE_CODEX_PROBE_BINARY")
    if not binary:
        pytest.skip("requires an explicitly selected actual Codex binary")
    import frontend_codex_probe as probe
    from test_matched_tools import _controller

    from aisle.harness.frontend_request_authority import RequestAuthority
    from aisle.harness.matched_tool_service import ToolService

    controller, _, retained = _controller(tmp_path, arm)
    channel = Path(controller.plan["ambient_bindings"][arm]["environment"]["HOME"]) / "tool-channel"
    channel.mkdir()
    identity = "e" * 32
    raw = (
        json.dumps(
            {
                "schema_version": "aisle.matched-tool-request.v1",
                "id": identity,
                "operation": "check",
            }
        )
        + "\n"
    ).encode()
    authority_path = tmp_path / "authority"
    with RequestAuthority(authority_path, session_id=controller.session_id) as authority:
        grant = authority.authorize(
            call={"turn_id": "fixture-turn", "call_id": "call_1", "tool_name": "harness.check"},
            request=raw,
        )
        token = grant["authorization_id"] if delivery != "unissued" else "invented"
        delivered = raw if delivery != "altered" else raw[:-1] + b" \n"
        # Write the sidecar before publishing the complete newline-delimited request.
        # A frontend-visible completion marker proves exec_command actually ran.
        code = (
            "import json,pathlib,time; "
            f"c=pathlib.Path({str(channel)!r}); "
            f"a=pathlib.Path({str(authority_path / 'authority.json')!r}); "
            "\ntry:\n a.read_bytes()\nexcept PermissionError:\n "
            "pathlib.Path('authority-denied.txt').write_text('denied')\n"
            f"(c/{(identity + '.authorization.json')!r}).write_text("
            f"{(json.dumps({'authorization_id': token}) + chr(10))!r}); "
            f"(c/{(identity + '.request.json')!r}).write_bytes({delivered!r}); "
            "pathlib.Path('submitted.txt').write_text('submitted'); "
            "deadline=time.monotonic()+5\n"
            f"while not (c/{(identity + '.response.json')!r}).exists() "
            "and time.monotonic()<deadline:\n"
            " time.sleep(.01)\n"
        )
        command = shlex.join([sys.executable, "-I", "-B", "-c", code])

        def factory(requests, errors, output):
            profile = output / "fixture.sb"
            with profile.open("a") as stream:
                stream.write(f"(allow file-write* (subpath {json.dumps(str(channel))}))\n")
                for path in (authority_path, retained):
                    stream.write(f"(deny file-read* (subpath {json.dumps(str(path))}))\n")

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    body = self.rfile.read(int(self.headers["Content-Length"]))
                    if self.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    parsed = json.loads(body)
                    requests.append(
                        {
                            "authorization_present": "Authorization" in self.headers,
                            "tool_outputs": [
                                row
                                for row in parsed.get("input", [])
                                if row.get("type") == "function_call_output"
                            ],
                        }
                    )
                    data = (
                        _frame(
                            1,
                            "exec_command",
                            {
                                "cmd": command,
                                "login": False,
                                "yield_time_ms": 1000,
                                "max_output_tokens": 1000,
                            },
                        )
                        if len(requests) == 1
                        else probe._events(False)
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    self.wfile.flush()

            return HTTPServer(("127.0.0.1", 0), Handler)

        monkeypatch.setattr(probe, "_server", factory)
        with ToolService(controller, request_authority=authority) as service:
            probe.run_probe(binary, tmp_path / "frontend", "baseline")
            if delivery != "valid":
                assert service.failed.wait(5), service.error

    frontend = tmp_path / "frontend"
    evidence = json.loads((frontend / "evidence.json").read_text())
    assert not evidence["timed_out"], evidence
    assert evidence["rc"] == 0, evidence
    assert (frontend / "workspace/submitted.txt").read_text() == "submitted"
    assert (frontend / "workspace/authority-denied.txt").read_text() == "denied"
    snapshot = json.loads((frontend / "fixture-preflight.json").read_text())
    assert not probe._fixture_errors(frontend, snapshot)
    if delivery == "valid":
        from aisle.harness.matched_evidence import audit_tool_journal

        artifacts = {path.name: path.read_bytes() for path in authority_path.iterdir()}
        audited = audit_tool_journal(
            retained,
            session_id=controller.session_id,
            plan_id=controller.plan["immutable_id"],
            arm=arm,
            request_authority={
                "artifacts": artifacts,
                "expected": authority.reference(),
                "byte_limit": 65536,
            },
        )
        assert audited["ok"], audited
        assert audited["frontend_authorization_verified"] is True
        assert audited["attempted_tools"] == 1
        assert service.report["ok"], service.report
        assert controller.attempts == 1
        index = [
            json.loads(line)
            for line in (retained / "tool-request-index.jsonl").read_text().splitlines()
        ]
        assert index[0]["frontend_authorization"] == grant
        assert json.loads((authority_path / (token + "-consumed.json")).read_text()) == grant
    else:
        assert not service.report["ok"]
        assert controller.attempts == 0
        assert len(list(authority_path.glob("*-refused.json"))) == 1
        assert not list(authority_path.glob("*-consumed.json"))
