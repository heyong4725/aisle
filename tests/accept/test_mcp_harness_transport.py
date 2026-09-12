"""MON-12/MON-13: real MCP HTTP requests require owned source and joined shutdown."""

import http.client
import json
import socket
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from test_mcp_harness_source import frames

pytestmark = pytest.mark.accept


@pytest.mark.parametrize("mismatch", [False, True])
def test_mcp_http_request_reaches_only_its_owned_harness_call(tmp_path, mismatch):
    """MON-13: HTTP tool metadata must match source before controller callback execution."""
    from aisle.harness.mcp_harness_authority import MCPHarnessAuthority
    from aisle.harness.mcp_harness_transport import MCPHarnessServer

    calls = []
    source, request = frames()
    with MCPHarnessAuthority(
        tmp_path / "authority",
        timeout_s=5,
        handle_call=lambda *args: (
            calls.append(args)
            or {
                "success": True,
                "contentItems": [{"type": "inputText", "text": "checked"}],
            }
        ),
    ) as authority:
        authority.observe(json.dumps(source).encode(), thread_id="child", turn_id="child-turn")
        with MCPHarnessServer(authority, operations=["check"]) as server:
            client = http.client.HTTPConnection(*server.address, timeout=5)
            if mismatch:
                request["params"]["name"] = "run"
            client.request(
                "POST", "/mcp", json.dumps(request), {"Content-Type": "application/json"}
            )
            response = client.getresponse()
            result = json.loads(response.read())
            client.close()
            assert response.status == 200
            if mismatch:
                assert "error" in result
                assert calls == []
            else:
                assert result["result"] == {
                    "isError": False,
                    "content": [{"type": "text", "text": "checked"}],
                }
                assert len(calls) == 1


def test_mcp_shutdown_closes_a_client_with_incomplete_headers(tmp_path):
    """MON-13: unfinished clients cannot outlive the evidence store's owning context."""
    from aisle.harness.mcp_harness_authority import MCPHarnessAuthority
    from aisle.harness.mcp_harness_transport import MCPHarnessServer

    with MCPHarnessAuthority(
        tmp_path / "authority", timeout_s=5, handle_call=lambda *args: None
    ) as authority:
        with MCPHarnessServer(authority, operations=["check"]) as server:
            client = socket.create_connection(server.address, timeout=2)
            client.sendall(b"POST /mcp HTTP/1.1\r\nHost: localhost\r\n")
            deadline = time.monotonic() + 2
            while not server.connections and time.monotonic() < deadline:
                time.sleep(0.005)
            assert server.connections
        assert not server.connections
        assert client.recv(1) == b""
        client.close()
    assert authority.reference()["artifacts"] == {}
