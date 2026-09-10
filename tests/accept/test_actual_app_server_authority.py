"""MON-8/MON-12/MON-13: owned app-server requests authorize real controller checks."""

import gzip
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from test_frontend_continued_input import _frame

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.accept


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("failure", [None, "controller_unavailable", "grant_retention"])
def test_app_server_request_reaches_authorized_controller(tmp_path, monkeypatch, arm, failure):
    """MON-12/MON-13: request origin is the owned frontend pipe, not a participant call ID."""
    binary = os.environ.get("AISLE_CODEX_PROBE_BINARY")
    if sys.platform != "darwin" or not binary:
        pytest.skip("requires explicitly selected actual Codex and macOS sandbox")
    import frontend_codex_probe as probe
    from test_matched_tools import _controller

    from aisle.harness.matched_app_server import (
        acquire_authority_evidence,
        run_authorized_app_server,
    )
    from aisle.harness.matched_evidence import audit_tool_journal

    controller, _, retained = _controller(tmp_path, arm)
    channel = Path(controller.plan["ambient_bindings"][arm]["environment"]["HOME"]) / "tool-channel"
    channel.mkdir()
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    home = frontend / "home"
    home.mkdir()
    profile = frontend / "outer.sb"
    profile.write_text(
        "(version 1)\n(allow default)\n(deny network*)\n"
        '(allow network-outbound (remote ip "localhost:*"))\n'
        "(deny file-write*)\n"
        f'(allow file-write* (subpath {json.dumps(str(frontend))}) (literal "/dev/null"))\n'
        f"(deny file-read* (subpath {json.dumps(str(tmp_path / 'authority'))}))\n"
    )
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            requests.append(json.loads(raw))
            frame = (
                _frame(1, "check", {}).replace(
                    b'"name": "check"', b'"namespace": "harness", "name": "check"'
                )
                if len(requests) == 1
                else probe._events(False)
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frame)))
            self.end_headers()
            self.wfile.write(frame)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    config = {
        "model_provider": "aisle_fixture",
        "model": "aisle-fixture",
        "model_providers.aisle_fixture.name": "AISLE fixture",
        "model_providers.aisle_fixture.base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "model_providers.aisle_fixture.requires_openai_auth": False,
        "model_providers.aisle_fixture.supports_websockets": False,
        "model_providers.aisle_fixture.request_max_retries": 0,
        "model_providers.aisle_fixture.stream_max_retries": 0,
    }
    argv = [
        "/usr/bin/sandbox-exec",
        "-f",
        str(profile),
        binary,
        "app-server",
        "--listen",
        "stdio://",
    ]
    for key, value in config.items():
        argv += ["-c", key + "=" + json.dumps(value)]
    if failure == "controller_unavailable":
        from aisle.harness.matched_tool_service import ToolService

        def unavailable(service):
            service.error = "injected controller unavailable"
            service.failed.set()

        monkeypatch.setattr(ToolService, "_serve", unavailable)
    elif failure == "grant_retention":
        from aisle.harness.frontend_request_authority import RequestAuthority

        original = RequestAuthority._retain

        def unavailable(authority, name, value):
            if name.endswith("-grant.json"):
                raise OSError("injected grant retention failure")
            original(authority, name, value)

        monkeypatch.setattr(RequestAuthority, "_retain", unavailable)
    references = {}
    try:

        def execute():
            return run_authorized_app_server(
                controller,
                argv,
                cwd=frontend,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(home),
                    "CODEX_HOME": str(home),
                    "TMPDIR": str(frontend),
                },
                launch={
                    "app_server": {
                        "baseInstructions": "system prompt",
                        "developerInstructions": "research contract",
                    }
                },
                budget={**controller.plan["arms"][arm]["budget"], "wall_ceiling_s": 20},
                references=references,
            )

        if failure is not None:
            with pytest.raises((OSError, ValueError), match="injected"):
                execute()
            assert controller.attempts == 0
            assert (retained / "frontend-protocol/failure.json").is_file()
            assert any(
                json.loads(path.read_bytes()).get("method") == "item/tool/call"
                for path in (retained / "frontend-protocol").glob("*-received.json")
            )
            return
        result = execute()
        audited = audit_tool_journal(
            retained,
            session_id=controller.session_id,
            plan_id=controller.plan["immutable_id"],
            arm=arm,
            require_frontend_source=True,
            request_authority=acquire_authority_evidence(retained, references),
        )
        assert audited["ok"], audited
        assert audited["frontend_authorization_verified"] is True
        assert audited["frontend_source_verified"] is True
        assert result["tokens"] > 0
        assert result["tokens_generated"] > 0
        assert result["rc"] == 0
        assert references["protocol"]["dynamic_calls"] == controller.attempts == 1
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
