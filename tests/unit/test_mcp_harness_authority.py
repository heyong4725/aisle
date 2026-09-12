"""MON-12/MON-13: MCP dispatch consumes one owned source before calling the harness."""

import json

import pytest
from test_mcp_harness_source import frames

pytestmark = pytest.mark.unit


def test_mcp_source_is_consumed_once_before_controller_dispatch(tmp_path):
    """MON-13: replay cannot create another controller attempt or reservation."""
    from aisle.harness.mcp_harness_authority import MCPHarnessAuthority

    calls = []
    source, request = (json.dumps(value).encode() for value in frames())
    with MCPHarnessAuthority(
        tmp_path / "authority",
        timeout_s=1,
        handle_call=lambda call, raw: calls.append((call, raw)) or {"success": True},
    ) as authority:
        authority.observe(source, thread_id="child", turn_id="child-turn")
        assert authority.dispatch(request) == {"success": True}
        with pytest.raises(ValueError, match="replay|consumed"):
            authority.dispatch(request)
    assert len(calls) == 1 and calls[0][1] == source
    assert authority.reference()["failure"] is not None


def test_unobserved_mcp_request_never_reaches_controller(tmp_path):
    """MON-13: caller-supplied metadata alone cannot authorize a harness operation."""
    from aisle.harness.mcp_harness_authority import MCPHarnessAuthority

    calls = []
    _, request = frames()
    with MCPHarnessAuthority(
        tmp_path / "authority",
        timeout_s=0.01,
        handle_call=lambda *args: calls.append(args),
    ) as authority:
        with pytest.raises(ValueError, match="source|deadline"):
            authority.dispatch(json.dumps(request).encode())
    assert calls == []


def test_mcp_argument_mismatch_is_retained_without_dispatch(tmp_path):
    """MON-13: a valid call ID cannot authorize changed operation arguments."""
    from aisle.harness.mcp_harness_authority import MCPHarnessAuthority

    calls = []
    source, request = frames()
    request["params"]["arguments"] = {"extra": True}
    with MCPHarnessAuthority(
        tmp_path / "authority",
        timeout_s=1,
        handle_call=lambda *args: calls.append(args),
    ) as authority:
        authority.observe(json.dumps(source).encode(), thread_id="child", turn_id="child-turn")
        with pytest.raises(ValueError):
            authority.dispatch(json.dumps(request).encode())
    assert calls == []
    assert any(name.endswith("request.frame") for name in authority.reference()["artifacts"])


@pytest.mark.parametrize("suffix", ["-result.json", "-delivery.json"])
def test_mcp_retention_failure_retires_authority_after_callback(tmp_path, monkeypatch, suffix):
    """MON-12/MON-13: losing post-dispatch evidence must not yield a healthy closed reference."""
    from aisle.harness.mcp_harness_authority import MCPHarnessAuthority

    source, request = (json.dumps(value).encode() for value in frames())
    calls = []
    with MCPHarnessAuthority(
        tmp_path / "authority",
        timeout_s=5,
        handle_call=lambda *args: calls.append(args) or {"success": True},
    ) as authority:
        authority.observe(source, thread_id="child", turn_id="child-turn")
        retain = authority._retain

        def fail(name, value):
            if name.endswith(suffix):
                raise OSError("injected retention loss")
            return retain(name, value)

        monkeypatch.setattr(authority, "_retain", fail)
        with pytest.raises(OSError, match="retention loss"):
            authority.dispatch(request)
        assert authority.failed.is_set()
    assert len(calls) == 1
    assert authority.reference()["failure"] == "OSError"
