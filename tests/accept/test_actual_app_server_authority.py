"""MON-8/MON-12/MON-13: owned app-server requests authorize real controller checks."""

import gzip
import hashlib
import json
import os
import subprocess
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
@pytest.mark.parametrize(
    "failure",
    [
        None,
        "controller_unavailable",
        "grant_retention",
        "ceiling",
        "reservation_retention",
        "delivery_retention",
    ],
)
def test_app_server_request_reaches_authorized_controller(
    tmp_path, monkeypatch, arm, failure, *, nested=False, nested_ceiling=2
):
    """MON-12/MON-13: request origin is the owned frontend pipe, not a participant call ID."""
    binary = os.environ.get("AISLE_CODEX_PROBE_BINARY")
    if sys.platform != "darwin" or not binary:
        pytest.skip("requires explicitly selected actual Codex and macOS sandbox")
    host = os.environ.get("AISLE_CODE_MODE_HOST_BINARY") if nested else None
    if nested and not host:
        pytest.skip("requires explicitly selected actual Code Mode host")
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
        "(allow network* (local unix-socket) (remote unix-socket))\n"
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
                _frame(len(requests), "check", {}).replace(
                    b'"name": "check"', b'"namespace": "harness", "name": "check"'
                )
                if len(requests) == 1 or (failure == "ceiling" and len(requests) == 2)
                else probe._events(False)
            )
            if nested and len(requests) == 1:
                from test_frontend_nested_dispatch import _frame as nested_frame

                frame = nested_frame(
                    'await tools.exec_command({cmd:"printf first > nested-first.txt",'
                    "login:false}); "
                    "await tools.harness__check({}); "
                    'await tools.exec_command({cmd:"printf second > nested-second.txt",'
                    "login:false});"
                )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(frame)))
            self.end_headers()
            self.wfile.write(frame)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    inputs = {}
    identities = {}
    worker.start()
    try:
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
        version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
        assert version.returncode == 0 and version.stdout.strip() == "codex-cli 0.153.4"
        inputs = {
            "binary": Path(binary),
            "fixture": Path(__file__),
            "provider_frames": Path(__file__).with_name("test_frontend_continued_input.py"),
            "provider_completion": Path(probe.__file__),
            "profile": profile,
        }
        identities = {
            name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in inputs.items()
        }
        (frontend / "fixture-identity.json").write_text(
            json.dumps(
                {
                    "version": version.stdout.strip(),
                    "sha256": identities,
                    "argv": argv,
                    "complete_coverage": False,
                    "confinement_verified": False,
                }
            )
            + "\n"
        )
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
        elif failure in {"reservation_retention", "delivery_retention"}:
            from aisle.harness.frontend_dispatch import DispatchBudget

            original = DispatchBudget._retain
            suffix = "-reservation.json" if failure == "reservation_retention" else "-delivery.json"

            def unavailable(authority, name, value):
                if name.endswith(suffix):
                    raise OSError("injected dispatch retention failure")
                return original(authority, name, value)

            monkeypatch.setattr(DispatchBudget, "_retain", unavailable)
        elif failure == "ceiling":
            from aisle.harness.matched_frontend import FrontendToolBudget

            observe = FrontendToolBudget.__call__

            def record_without_stopping(guard, event):
                # Inject a missing observation stop: source admission must independently refuse.
                observe(guard, event)
                return None

            monkeypatch.setattr(FrontendToolBudget, "__call__", record_without_stopping)
        ceiling = nested_ceiling if nested else (1 if failure == "ceiling" else 2)
        references = {}

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
                    **(
                        {
                            "code_mode_host": {
                                "path": host,
                                "sha256": hashlib.sha256(Path(host).read_bytes()).hexdigest(),
                            }
                        }
                        if nested
                        else {}
                    ),
                    "app_server": {
                        "baseInstructions": "system prompt",
                        "developerInstructions": "research contract",
                    },
                },
                budget={
                    **controller.plan["arms"][arm]["budget"],
                    "wall_ceiling_s": 20,
                    "frontend_tool_ceiling": ceiling,
                },
                references=references,
            )

        if failure is not None:
            with pytest.raises((OSError, ValueError), match="injected|dispatch budget exhausted"):
                execute()
            assert controller.attempts == (1 if failure in {"ceiling", "delivery_retention"} else 0)
            assert (retained / "session.jsonl").is_file()
            assert (retained / "frontend-live.json").is_file()
            assert (retained / "frontend-protocol/failure.json").is_file()
            assert any(
                json.loads(path.read_bytes()).get("method") == "item/tool/call"
                for path in (retained / "frontend-protocol").glob("*-received.json")
            )
            if failure == "ceiling":
                from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

                dispatch_audit = verify_dispatch_journal(
                    {p.name: p.read_bytes() for p in (retained / "frontend-dispatch").iterdir()},
                    expected=references["dispatch"],
                    byte_limit=4 * 1024 * 1024,
                )
                assert dispatch_audit["ok"], dispatch_audit
                assert dispatch_audit["attempts"] == 2 and dispatch_audit["reserved"] == 1
                assert dispatch_audit["delivery_uncertain"] is False
                assert len(list((retained / "frontend-authority").glob("*-grant.json"))) == 1
            return
        result = execute()
        audited = audit_tool_journal(
            retained,
            session_id=controller.session_id,
            plan_id=controller.plan["immutable_id"],
            arm=arm,
            require_frontend_source=True,
            frontend_dispatch_ceiling=ceiling,
            request_authority=acquire_authority_evidence(retained, references),
        )
        assert audited["ok"], audited
        assert audited["frontend_authorization_verified"] is True
        assert audited["frontend_source_verified"] is True
        assert audited["frontend_reservation_verified"] is True
        assert result["tokens"] > 0
        assert result["tokens_generated"] > 0
        assert result["rc"] == 0
        assert references["protocol"]["dynamic_calls"] == controller.attempts == 1
        if nested:
            assert (frontend / "nested-first.txt").read_text() == "first"
            assert (frontend / "nested-second.txt").exists() == (nested_ceiling == 3)
            assert references["dispatch"]["reserved"] == nested_ceiling
            assert references["dispatch"]["attempts"] == 3
            assert references["code_mode"]["failure"] is None
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
        if identities:
            assert identities == {
                name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in inputs.items()
            }


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("ceiling", [2, 3])
def test_actual_mixed_nested_calls_share_controller_budget(tmp_path, monkeypatch, arm, ceiling):
    """MON-8/MON-12/MON-13: native commands and real harness checks share one audited ceiling."""
    test_app_server_request_reaches_authorized_controller(
        tmp_path, monkeypatch, arm, None, nested=True, nested_ceiling=ceiling
    )
