"""MON-8/MON-13: hosted support is bound explicitly and unknown surfaces fail closed."""

import json

import pytest

from aisle.harness.provider_hosted import hosted_request, request_tools
from aisle.harness.provider_relay import verify_provider

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "base,contract,valid",
    [
        ("https://api.openai.com/v1", "openai.responses.max_tool_calls.v1", True),
        ("http://127.0.0.1:4321/v1", "aisle.fixture.responses.max_tool_calls.v1", True),
        ("https://example.com/v1", "openai.responses.max_tool_calls.v1", False),
        ("https://chatgpt.com/backend-api/codex", "openai.responses.max_tool_calls.v1", False),
        ("https://api.openai.com/v1", "aisle.fixture.responses.max_tool_calls.v1", False),
        ("https://api.openai.com/v1", "unknown", False),
    ],
)
def test_hosted_contract_cannot_be_assigned_to_an_arbitrary_provider(base, contract, valid):
    """MON-13: a contract label cannot qualify a different service or subscription backend."""
    binding = {"base_url": base, "hosted_tool_contract": contract}
    if valid:
        assert verify_provider(binding).hostname
    else:
        with pytest.raises(ValueError):
            verify_provider(binding)


def test_hosted_limit_preserves_tools_and_a_stricter_requested_limit():
    """MON-8: available tools remain advertised and an existing tighter limit is preserved."""
    original = {
        "stream": True,
        "tools": [{"type": "web_search"}, {"type": "function", "name": "exec"}],
        "max_tool_calls": 1,
    }
    raw, limit = hosted_request(json.dumps(original).encode(), 5)
    assert limit == 1
    assert json.loads(raw) == original


@pytest.mark.parametrize(
    "change",
    [
        {"tools": [{"type": "unverified_tool"}]},
        {"tools": [{"type": "namespace", "tools": [{"type": "web_search"}]}]},
        {"background": True},
        {"stream": False},
    ],
)
def test_unknown_or_unbounded_hosted_modes_are_not_qualified(change):
    """MON-13: unsupported tool types and background jobs cannot bypass scoped accounting."""
    value = {"stream": True, "tools": [{"type": "web_search"}], **change}
    with pytest.raises(ValueError):
        request_tools(json.dumps(value).encode())
