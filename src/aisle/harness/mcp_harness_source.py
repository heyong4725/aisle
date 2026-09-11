"""Match an MCP request to a separately acquired, owned App Server source.

This parser supplies correlation, not authority by itself. The live caller must
acquire source bytes from its owned pipe and bind the thread/turn through the
session scope. Dispatch and request authorities enforce one-use reservations.
Offline auditing must supply those same retained bytes and scope identities.
"""

from __future__ import annotations

import hashlib
import json

MAX_FRAME_BYTES = 65536


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate MCP source field")
        result[key] = value
    return result


def _invalid(value):
    raise ValueError("nonfinite MCP source value")


def _decode(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_FRAME_BYTES:
        raise ValueError("missing or unbounded MCP source frame")
    try:
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_invalid)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid MCP source encoding or nesting") from exc
    if type(value) is not dict:
        raise ValueError("MCP source frame is not an object")
    return value


def match_mcp_request(source, request, *, thread_id, turn_id):
    """Return the normalized call only when both source and request match its scope."""
    if any(type(value) is not str or not 0 < len(value) <= 256 for value in (thread_id, turn_id)):
        raise ValueError("unbound MCP source scope")
    observed, incoming = _decode(source), _decode(request)
    try:
        params = observed["params"]
        item = params["item"]
        submitted = incoming["params"]
        meta = submitted["_meta"]
        turn = meta["x-codex-turn-metadata"]
        if (
            set(observed) - {"emittedAtMs"} != {"method", "params"}
            or observed["method"] != "item/started"
            or params["threadId"] != thread_id
            or params["turnId"] != turn_id
            or item["type"] != "mcpToolCall"
            or item["server"] != "aisle_harness"
            or item["status"] != "inProgress"
            or item["tool"] not in ("check", "run")
            or type(item["id"]) is not str
            or not 0 < len(item["id"]) <= 256
            or item["arguments"] != {}
            or set(incoming) != {"jsonrpc", "id", "method", "params"}
            or incoming["jsonrpc"] != "2.0"
            or type(incoming["id"]) not in (str, int)
            or incoming["method"] != "tools/call"
            or set(submitted) != {"name", "arguments", "_meta"}
            or submitted["name"] != item["tool"]
            or submitted["arguments"] != {}
            or meta["callId"] != item["id"]
            or meta["threadId"] != thread_id
            or turn["thread_id"] != thread_id
            or turn["turn_id"] != turn_id
        ):
            raise ValueError("MCP request differs from owned frontend source")
        return {
            "turn_id": turn_id,
            "call_id": item["id"],
            "tool_name": "harness." + item["tool"],
        }
    except (KeyError, TypeError) as exc:
        raise ValueError("incomplete MCP source binding") from exc


def verify_mcp_sources(artifacts, *, expected, byte_limit, sources, completions):
    """Recompute MCP requests against scoped pipe events and returned tool results."""
    _verify_mcp_sources(
        artifacts,
        expected=expected,
        byte_limit=byte_limit,
        sources=sources,
        completions=completions,
    )


def link_mcp_refusal_sources(artifacts, *, expected, byte_limit, sources, completions, dispatch):
    """Link refused MCP requests to separately scoped owned pipe bytes and shared quota.

    This does not acquire source authority or establish full session qualification.
    """
    return _verify_mcp_sources(
        artifacts,
        expected=expected,
        byte_limit=byte_limit,
        sources=sources,
        completions=completions,
        dispatch=dispatch,
    )


def _verify_mcp_sources(artifacts, *, expected, byte_limit, sources, completions, dispatch=None):
    refusal_mode = dispatch is not None
    if (
        type(byte_limit) is not int
        or byte_limit <= 0
        or type(artifacts) is not dict
        or any(type(name) is not str or type(raw) is not bytes for name, raw in artifacts.items())
        or sum(map(len, artifacts.values())) > byte_limit
        or type(expected) is not dict
        or set(expected)
        not in ({"artifacts", "failure", "calls"}, {"artifacts", "failure", "calls", "listener"})
        or expected["failure"] != ("DispatchRefused" if refusal_mode else None)
        or type(expected["calls"]) is not int
        or not 0 <= expected["calls"] <= 1024
        or expected["artifacts"]
        != {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()}
        or (not refusal_mode and set(sources) != set(completions))
        or not set(completions).issubset(sources)
        or len(sources) != expected["calls"]
    ):
        raise ValueError("invalid or incomplete MCP evidence reference")
    if "listener" in expected:
        from aisle.harness.provider_source_audit import verify_listener_identity

        listener = expected["listener"]
        if type(listener) is not dict:
            raise ValueError("invalid MCP listener descriptor")
        # Shape validation only here; original-launch verification binds the session.
        verify_listener_identity(
            listener, schema="aisle.mcp-harness-listener.v1", session_id=listener.get("session_id")
        )
    count = expected["calls"]
    refusals = []
    if refusal_mode:
        from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

        report = verify_dispatch_journal(**dispatch)
        if not report["ok"] or report["delivery_uncertain"]:
            raise ValueError("invalid MCP refusal dispatch journal")
        refusals = report["budget_refusals"]
    names = {f"source-{i:08d}.frame" for i in range(1, count + 1)}
    names.update(
        f"{i:08d}-{suffix}"
        for i in range(1, count + 1)
        for suffix in ("request.frame", "source.frame", "result.json", "delivery.json")
    )
    refused_requests = set()
    if refusal_mode:
        for i in range(1, count + 1):
            name = f"{i:08d}-delivery.json"
            if name not in artifacts:
                raise ValueError("missing MCP delivery evidence")
            if _decode(artifacts[name]) == {
                "handler_entered": True,
                "error_type": "DispatchRefused",
            }:
                refused_requests.add(i)
                names.remove(f"{i:08d}-result.json")
        if not refused_requests:
            raise ValueError("no MCP quota refusal to link")
    if set(artifacts) != names:
        raise ValueError("MCP evidence inventory differs")
    registered = [artifacts[f"source-{i:08d}.frame"] for i in range(1, count + 1)]
    if len(set(registered)) != count or set(registered) != set(sources.values()):
        raise ValueError("MCP registered sources differ from owned pipe")
    consumed = set()
    linked = []
    for i in range(1, count + 1):
        prefix = f"{i:08d}"
        raw = artifacts[prefix + "-source.frame"]
        params = _decode(raw)["params"]
        call = match_mcp_request(
            raw,
            artifacts[prefix + "-request.frame"],
            thread_id=params["threadId"],
            turn_id=params["turnId"],
        )
        key = (call["turn_id"], call["call_id"])
        if key in consumed or sources.get(key) != raw:
            raise ValueError("MCP request has no unique owned source")
        consumed.add(key)
        if i in refused_requests:
            matches = []
            for refusal in refusals:
                if refusal["kind"] != "local" or refusal["call"] != call:
                    continue
                prefix = refusal["reservation"].removesuffix("-reservation.json")
                if dispatch["artifacts"][prefix + ".frame"] == raw:
                    matches.append(int(prefix))
            if len(matches) != 1 or matches[0] in linked:
                raise ValueError("MCP refusal lacks a unique exhausted reservation")
            if key in completions:
                completed = completions[key]
                original = params["item"]
                error = {
                    "message": "tool call error: tool call failed for `aisle_harness/"
                    + original["tool"]
                    + (
                        "`\n\nCaused by:\n    Mcp error: -32000: MCP harn"
                        "ess authorization unavailable"
                    )
                }
                if (
                    type(completed) is not dict
                    or completed.get("status") != "failed"
                    or completed.get("result", "missing") is not None
                    or completed.get("error") != error
                    or any(
                        json.dumps(completed.get(field), sort_keys=True)
                        != json.dumps(original.get(field), sort_keys=True)
                        for field in ("type", "id", "server", "tool", "arguments")
                    )
                ):
                    raise ValueError("refused MCP source has an unrelated completion")
            linked.extend(matches)
            continue
        if _decode(artifacts[prefix + "-delivery.json"]) != {
            "handler_entered": True,
            "error_type": None,
        }:
            raise ValueError("MCP delivery is incomplete or uncertain")
        reply = _decode(artifacts[prefix + "-result.json"])
        if (
            set(reply) != {"success", "contentItems"}
            or type(reply["success"]) is not bool
            or type(reply["contentItems"]) is not list
            or any(
                type(item) is not dict
                or set(item) != {"type", "text"}
                or item["type"] != "inputText"
                or type(item["text"]) is not str
                for item in reply["contentItems"]
            )
        ):
            raise ValueError("invalid MCP harness reply")
        completed = completions[key]
        original = params["item"]
        if (
            any(
                completed.get(field) != original[field]
                for field in ("id", "server", "tool", "arguments")
            )
            or completed.get("status") != ("completed" if reply["success"] else "failed")
            or completed.get("error") is not None
            or completed.get("result", {}).get("content")
            != [{"type": "text", "text": item["text"]} for item in reply["contentItems"]]
        ):
            raise ValueError("MCP completion differs from retained harness reply")
    return linked
