"""MON-8/MON-13: the provider relay and frontend share a controller lifetime."""

import asyncio
import threading

import pytest

from aisle.harness import provider_runner

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("failed", [False, True, "closing"])
def test_provider_failure_reaps_frontend_and_retains_reference(tmp_path, monkeypatch, failed):
    """MON-13: failed admission cannot leave an unmediated frontend running."""
    finished = []
    captured = {}

    class Relay:
        address = ("127.0.0.1", 12345)

        def __init__(self, binding, **kwargs):
            captured.update(kwargs)
            self.failed = threading.Event()
            self._failure = None
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.closed = True

        def reference(self):
            assert self.closed
            return {"failure": "DispatchRefused" if failed else None}

    relay_instance = None

    def factory(*args, **kwargs):
        nonlocal relay_instance
        relay_instance = Relay(*args, **kwargs)
        return relay_instance

    async def frontend(argv, **kwargs):
        captured["argv"] = argv
        try:
            if failed is True:
                relay_instance._failure = "DispatchRefused"
                relay_instance.failed.set()
                await asyncio.Event().wait()
            return {"ok": True}
        finally:
            finished.append(True)

    monkeypatch.setattr(provider_runner, "ProviderRelay", factory)
    monkeypatch.setattr(provider_runner, "run_app_server_async", frontend)
    references = {}
    options = dict(
        binding={"base_url": "http://127.0.0.1:1234/v1", "requires_openai_auth": False},
        dispatch=object(),
        delegated_tools={("harness", "check", "function_call")},
        provider_output=tmp_path,
        references=references,
        argv=["codex", "app-server"],
        timeout_s=1,
        output=tmp_path / "protocol",
        handle_call=lambda *_: None,
    )
    if failed:
        with pytest.raises(ValueError, match="provider.*DispatchRefused"):
            provider_runner.run_provider_app_server(**options)
    else:
        assert provider_runner.run_provider_app_server(**options) == {"ok": True}
    assert finished == [True]
    assert references["provider"]["failure"] == ("DispatchRefused" if failed else None)
    assert 'model_provider="aisle_authorized"' in captured["argv"]
    assert "model_providers.aisle_authorized.supports_websockets=false" in captured["argv"]
    assert "model_providers.aisle_authorized.requires_openai_auth=false" in captured["argv"]


def test_mcp_closure_failure_still_retains_provider_reference(tmp_path, monkeypatch):
    """MON-12/MON-13: one adapter's cleanup failure must not discard another's evidence."""
    from types import SimpleNamespace

    from aisle.harness import mcp_harness_authority, mcp_harness_transport

    class Resource:
        address = ("127.0.0.1", 12345)

        def __init__(self, *args, **kwargs):
            self.failed = threading.Event()
            self._failure = None
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.closed = True

        def observe(self, *args, **kwargs):
            pass

        def reference(self):
            assert self.closed
            return {"failure": None}

    class FailedMCP(Resource):
        def reference(self):
            assert self.closed
            return {"failure": "OSError"}

    async def frontend(*args, **kwargs):
        return {"ok": True}

    monkeypatch.setattr(provider_runner, "ProviderRelay", Resource)
    monkeypatch.setattr(provider_runner, "run_app_server_async", frontend)
    monkeypatch.setattr(mcp_harness_authority, "MCPHarnessAuthority", FailedMCP)
    monkeypatch.setattr(mcp_harness_transport, "MCPHarnessServer", Resource)
    references = {}
    with pytest.raises(ValueError, match="MCP harness failed"):
        provider_runner.run_provider_app_server(
            binding={"base_url": "http://127.0.0.1:1234/v1"},
            dispatch=SimpleNamespace(session_id="session"),
            delegated_tools={("mcp__aisle_harness", "check", "function_call")},
            provider_output=tmp_path / "provider",
            references=references,
            argv=["codex", "app-server"],
            timeout_s=1,
            mcp_harness=True,
            mcp_output=tmp_path / "mcp",
            output=tmp_path / "protocol",
            handle_call=lambda *_: None,
        )
    assert references["mcp_harness"]["listener"] == {
        "schema_version": "aisle.mcp-harness-listener.v1",
        "session_id": "session",
        "host": "127.0.0.1",
        "port": 12345,
    }
    assert references["mcp_harness"]["failure"] == "OSError"
    assert references["provider"]["failure"] is None
