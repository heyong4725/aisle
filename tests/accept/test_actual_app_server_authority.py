"""MON-8/MON-12/MON-13: owned app-server requests authorize real controller checks."""

import gzip
import hashlib
import json
import os
import re
import shlex
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
    tmp_path,
    monkeypatch,
    arm,
    failure,
    *,
    nested=False,
    nested_ceiling=2,
    provider=False,
    provider_route="native",
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

    controller, _, retained = _controller(
        tmp_path, arm, model="gpt-5.4" if provider_route == "freeform" else None
    )
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
        + (
            '(allow file-write* (literal "/dev/ptmx") (regex #"^/dev/ttys[0-9]+$"))\n'
            if provider_route == "continued"
            else ""
        )
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
            if provider and not nested:
                index = len(requests)
                if index in (1, 3):
                    frame = _frame(
                        index,
                        "exec_command",
                        {
                            "cmd": f"printf native > provider-{index}.txt",
                            "login": False,
                        },
                    )
                elif index == 2:
                    frame = _frame(index, "check", {}).replace(
                        b'"name": "check"', b'"namespace": "harness", "name": "check"'
                    )
                if provider_route == "continued" and index == 1:
                    code = (
                        "import pathlib,select,sys; "
                        "pathlib.Path('provider-1.txt').write_text('native'); "
                        "ready=select.select([sys.stdin],[],[],15)[0]; "
                        "value=sys.stdin.readline() if ready else ''; "
                        "pathlib.Path('continued.txt').write_text(value) if value else None"
                    )
                    frame = _frame(
                        index,
                        "exec_command",
                        {
                            "cmd": shlex.join([sys.executable, "-I", "-B", "-c", code]),
                            "login": False,
                            "tty": True,
                            "yield_time_ms": 1000,
                        },
                    )
                if index == 3:
                    if provider_route == "continued":
                        reply = next(
                            row["output"]
                            for row in requests[-1]["input"]
                            if row.get("type") == "function_call_output"
                            and row.get("call_id") == "call_1"
                        )
                        match = re.search(r"Process running with session ID (\d+)", reply)
                        if match is None:
                            self.send_error(400, "interactive fixture session unavailable")
                            return
                        frame = _frame(
                            index,
                            "write_stdin",
                            {
                                "session_id": int(match.group(1)),
                                "chars": "delivered\n",
                                "yield_time_ms": 1000,
                            },
                        )
                    elif provider_route == "mcp":
                        from test_frontend_mcp_dispatch import _frame as mcp_frame

                        frame = mcp_frame(index)
                    elif provider_route == "freeform":
                        from test_frontend_native_edit import _frame as edit_frame

                        frame = edit_frame()
                    elif provider_route == "multi":
                        events = [
                            json.loads(line[6:])
                            for line in frame.splitlines()
                            if line.startswith(b"data: ")
                        ]
                        second = json.loads(json.dumps(events[1]))
                        second["output_index"] = 1
                        second["item"].update(
                            id="fc_4",
                            call_id="call_4",
                            arguments=json.dumps(
                                {
                                    "cmd": "printf native > provider-4.txt",
                                    "login": False,
                                }
                            ),
                        )
                        events.insert(2, second)
                        events[-1]["response"]["output"].append(second["item"])
                        frame = b"".join(
                            f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode()
                            for e in events
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
        if provider_route == "mcp":
            mcp_source = Path(probe.__file__).with_name("frontend_mcp_fixture.py")
            mcp_script = frontend / "frontend_mcp_fixture.py"
            mcp_script.write_bytes(mcp_source.read_bytes())
            (frontend / "workspace").mkdir()
            config.update(
                {
                    "mcp_servers.aisle_fixture.command": str(Path(sys.executable).resolve()),
                    "mcp_servers.aisle_fixture.args": [
                        "-I",
                        "-B",
                        str(mcp_script),
                        "--output",
                        str(frontend),
                    ],
                }
            )
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
        if provider_route in {"mcp", "freeform"}:
            inputs["route_frames"] = Path(__file__).with_name(
                "test_frontend_mcp_dispatch.py"
                if provider_route == "mcp"
                else "test_frontend_native_edit.py"
            )
        if provider_route == "mcp":
            inputs.update(mcp_script=mcp_script, python=Path(sys.executable).resolve())
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
        ceiling = nested_ceiling if nested or provider else (1 if failure == "ceiling" else 2)
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
                            "provider": {
                                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                                "requires_openai_auth": False,
                            }
                        }
                        if provider
                        else {}
                    ),
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

        refused = 3 if provider_route == "multi" else 2
        marker = {
            "native": "provider-3.txt",
            "mcp": "workspace/mcp-marker-1.txt",
            "freeform": "patch-marker.txt",
            "continued": "continued.txt",
            "multi": "provider-4.txt",
        }[provider_route]
        if provider and not nested and ceiling == refused:
            with pytest.raises(ValueError, match="provider relay failed: DispatchRefused"):
                execute()
            assert controller.attempts == 1
            assert (frontend / "provider-1.txt").read_text() == "native"
            assert not (frontend / marker).exists()
            assert references["dispatch"]["reserved"] == refused
            assert references["dispatch"]["attempts"] == refused + 1
            assert references["provider"]["failure"] == "DispatchRefused"
            return
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
        if provider:
            assert "provider" in references, "owned provider boundary is missing"
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
        if provider and not nested:
            assert (frontend / "provider-1.txt").read_text() == "native"
            assert (frontend / marker).read_text() == {
                "native": "native",
                "mcp": "fixture",
                "freeform": "fixture patch\n",
                "continued": "delivered\n",
                "multi": "native",
            }[provider_route]
            assert references["dispatch"]["reserved"] == refused + 1
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


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("ceiling", [2, 3])
def test_actual_provider_native_calls_share_controller_budget(tmp_path, monkeypatch, arm, ceiling):
    """MON-8/MON-12/MON-13: production provider delivery and harness calls share reservations."""
    test_app_server_request_reaches_authorized_controller(
        tmp_path, monkeypatch, arm, None, provider=True, nested_ceiling=ceiling
    )


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_actual_provider_and_nested_host_share_controller_budget(tmp_path, monkeypatch, arm):
    """MON-8/MON-12/MON-13: provider wrappers delegate without charging nested calls twice."""
    test_app_server_request_reaches_authorized_controller(
        tmp_path, monkeypatch, arm, None, provider=True, nested=True, nested_ceiling=3
    )


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("route", ["multi", "mcp", "freeform", "continued"])
@pytest.mark.parametrize("refuse", [False, True])
def test_actual_provider_routes_share_controller_budget(tmp_path, monkeypatch, arm, route, refuse):
    """MON-8/MON-12/MON-13: real route side effects distinguish admission from refusal."""
    ceiling = (4 if route == "multi" else 3) - int(refuse)
    test_app_server_request_reaches_authorized_controller(
        tmp_path,
        monkeypatch,
        arm,
        None,
        provider=True,
        provider_route=route,
        nested_ceiling=ceiling,
    )
