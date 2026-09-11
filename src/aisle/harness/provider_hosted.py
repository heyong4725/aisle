"""Responses web-search allowance encoding and completed-call reconciliation.

The caller must bind an upstream that enforces Responses max_tool_calls before
using this protocol. Parsing a response does not establish that provider contract.
"""

from __future__ import annotations

import json

from aisle.harness.frontend_dispatch import MAX_FRAME_BYTES
from aisle.harness.provider_response_authority import _constant, _object, _require, _response


def request_tools(raw):
    """Validate the bounded tool inventory for this supported provider contract."""
    _require(type(raw) is bytes and len(raw) <= MAX_FRAME_BYTES, "unbounded hosted request")
    value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    _require(
        type(value) is dict
        and value.get("stream") is True
        and value.get("background", False) is False,
        "unsupported hosted response mode",
    )
    tools = value.get("tools")
    _require(type(tools) is list and len(tools) <= 1024, "invalid hosted tool inventory")
    hosted = False
    for tool in tools:
        _require(type(tool) is dict, "invalid provider tool")
        kind = tool.get("type")
        if kind in {"web_search", "web_search_preview"}:
            hosted = True
        elif kind == "namespace":
            children = tool.get("tools")
            _require(
                type(children) is list
                and len(children) <= 1024
                and all(
                    type(child) is dict and child.get("type") in {"function", "custom"}
                    for child in children
                ),
                "unsupported provider namespace",
            )
        else:
            _require(
                kind in {"function", "custom"}
                or (kind == "tool_search" and tool.get("execution") == "client"),
                "unsupported provider tool in hosted profile",
            )
    return value, hosted


def hosted_request(raw, remaining):
    """Keep advertised tools, binding the upstream limit to available shared slots."""
    value, hosted = request_tools(raw)
    _require(hosted, "request does not advertise a supported hosted tool")
    _require(type(remaining) is int and remaining >= 0, "invalid hosted allowance")
    requested = value.get("max_tool_calls")
    _require(
        requested is None or type(requested) is int and requested > 0,
        "invalid requested hosted limit",
    )
    allowance = remaining if requested is None else min(remaining, requested)
    value["max_tool_calls"] = allowance
    return json.dumps(value, allow_nan=False, sort_keys=True).encode(), allowance


def hosted_calls(raw):
    """Count completed hosted calls in a fully validated Responses stream."""
    response_id, events, _ = _response(raw)
    calls = []
    for _, event in events:
        if event is None or event["type"] != "response.output_item.done":
            continue
        item = event["item"]
        kind = item["type"]
        if kind == "web_search_call":
            _require(item.get("status") == "completed", "hosted call did not complete")
            calls.append({"turn_id": response_id, "call_id": item["id"], "tool_name": "web_search"})
        else:
            _require(
                kind in {"message", "reasoning", "compaction", "function_call", "custom_tool_call"}
                or (kind == "tool_search_call" and item.get("execution") == "client"),
                "unverified hosted output type",
            )
    return calls


def web_search_action(action, *, provider=False):
    """Normalize the pinned App Server action fields without conflating output IDs."""
    _require(type(action) is dict, "missing hosted search action")
    kind = action.get("type")
    if provider:
        kind = {"search": "search", "open_page": "openPage", "find_in_page": "findInPage"}.get(kind)
    fields = {
        "search": ("query", "queries"),
        "openPage": ("url",),
        "findInPage": ("url", "pattern"),
    }
    _require(type(kind) is str and kind in fields, "unverified hosted search action")
    result = {"type": kind}
    for name in fields[kind]:
        value = action.get(name)
        if name == "queries":
            _require(
                value is None
                or type(value) is list
                and len(value) <= 1024
                and all(type(query) is str for query in value),
                "invalid hosted search queries",
            )
        else:
            _require(value is None or type(value) is str, "invalid hosted search argument")
        result[name] = value
    return result


def hosted_frontend_items(raw):
    """Derive expected frontend item identities/actions from retained provider bytes."""
    _, events, _ = _response(raw)
    return [
        {
            "id": event["item"]["id"],
            "action": web_search_action(event["item"].get("action"), provider=True),
        }
        for _, event in events
        if event is not None
        and event["type"] == "response.output_item.done"
        and event["item"]["type"] == "web_search_call"
    ]
