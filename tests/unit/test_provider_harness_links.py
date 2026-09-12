"""MON-12/MON-13: provider delegation must match each owned harness transport."""

import json

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "drift",
    [
        None,
        "arguments",
        "missing_source",
        "missing_provider",
        "wrong_namespace",
        "duplicate",
        "nested_disguise",
    ],
)
def test_provider_harness_links_match_mixed_dynamic_and_mcp_calls(drift):
    """MON-13: MCP calls cannot escape provider correlation or masquerade as nested dynamic
    calls.
    """
    from aisle.harness.frontend_qualification import match_provider_harness_calls

    provider = [
        {"call_id": "parent", "namespace": "harness", "name": "check", "payload": {}},
        {"call_id": "child", "namespace": "mcp__aisle_harness", "name": "check", "payload": {}},
    ]
    prefix = {
        "calls": [
            (7, {"call_id": "parent", "tool_name": "harness.check"}, b'{"params":{"arguments":{}}}')
        ],
        "mcp_sources": {
            ("child-turn", "child"): json.dumps(
                {
                    "params": {
                        "item": {
                            "id": "child",
                            "tool": "check",
                            "server": "aisle_harness",
                            "arguments": {},
                        }
                    }
                }
            ).encode()
        },
    }
    if drift == "arguments":
        provider[1]["payload"] = {"extra": True}
    elif drift == "missing_source":
        prefix["mcp_sources"] = {}
    elif drift in {"missing_provider", "nested_disguise"}:
        provider.pop()
    elif drift == "wrong_namespace":
        provider[1]["namespace"] = "harness"
    elif drift == "duplicate":
        provider.append(dict(provider[1]))
    if drift is None:
        assert match_provider_harness_calls(provider, prefix, nested=False) == []
    else:
        with pytest.raises(ValueError):
            match_provider_harness_calls(provider, prefix, nested=drift == "nested_disguise")


@pytest.mark.parametrize("drift", [None, "missing", "turn", "call", "duplicate", "disabled"])
def test_nested_mcp_requires_replayed_host_link(drift):
    """MON-13: MCP may lack a direct provider call only with an exact verified nested result
    link.
    """
    from aisle.harness.frontend_qualification import match_provider_harness_calls

    prefix = {
        "calls": [],
        "mcp_sources": {
            ("turn", "call"): json.dumps(
                {
                    "params": {
                        "turnId": "turn",
                        "item": {
                            "id": "call",
                            "tool": "check",
                            "server": "aisle_harness",
                            "arguments": {},
                        },
                    }
                }
            ).encode()
        },
    }
    links = [{"turn_id": "turn", "call_id": "call"}]
    if drift == "missing":
        links.clear()
    elif drift in {"turn", "call"}:
        links[0][drift + "_id"] = "foreign"
    elif drift == "duplicate":
        links.append(dict(links[0]))
    if drift is None:
        assert match_provider_harness_calls([], prefix, nested=True, nested_links=links) == [
            {"call_id": "call", "namespace": "mcp__aisle_harness", "name": "check", "payload": {}}
        ]
    else:
        with pytest.raises(ValueError):
            match_provider_harness_calls([], prefix, nested=drift != "disabled", nested_links=links)
