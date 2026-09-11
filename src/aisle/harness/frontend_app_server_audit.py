"""Bind normalized request grants to retained messages from an owned frontend pipe.

The caller supplies the transport's write-time reference from trusted memory or
trusted retention, not a reference rebuilt from the files being audited. This
checks successful dynamic-tool sessions, not coverage of built-in execution.
"""

from __future__ import annotations

import hashlib
import json
import re

from aisle.harness.frontend_app_server import MAX_MESSAGE_BYTES, AppServerScope


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "duplicate protocol field")
        value[key] = item
    return value


def _constant(value):
    raise ValueError("non-finite protocol value")


def verify_app_server_sources(
    artifacts,
    *,
    expected,
    grants,
    byte_limit,
    dispatch=None,
    dispatch_ceiling=None,
    code_mode=None,
    provider=None,
    mcp_harness=None,
):
    """Recompute one source call and one reply per grant, without adding counts."""
    result = {
        "ok": False,
        "errors": [],
        "linked_calls": None,
        "reservations_verified": False,
        "complete_coverage": False,
        "confinement_verified": False,
    }
    try:
        _require(type(byte_limit) is int and byte_limit > 0, "invalid byte limit")
        _require(
            type(artifacts) is dict
            and all(type(k) is str and type(v) is bytes for k, v in artifacts.items()),
            "invalid protocol snapshot",
        )
        _require(sum(map(len, artifacts.values())) <= byte_limit, "protocol exceeds byte limit")
        _require(
            type(expected) is dict
            and set(expected) - {"mcp_calls"}
            == {"thread_id", "turn_id", "dynamic_calls", "artifacts"},
            "invalid transport reference",
        )
        _require(
            expected["artifacts"]
            == {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
            "protocol differs from write-time reference",
        )
        thread, turn = expected["thread_id"], expected["turn_id"]
        _require(
            all(type(v) is str and 0 < len(v) <= 256 for v in (thread, turn)),
            "invalid thread or turn identity",
        )
        _require(
            type(expected["dynamic_calls"]) is int and expected["dynamic_calls"] >= 0,
            "invalid dynamic-call count",
        )
        rows = {}
        for name, raw in artifacts.items():
            _require(
                len(raw) <= MAX_MESSAGE_BYTES and raw.endswith(b"\n"), "invalid protocol frame"
            )
            rows[name] = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
            _require(type(rows[name]) is dict, "protocol frame must be an object")
        invocation = rows.pop("invocation.json")
        _require(
            invocation.get("transport") == "owned_stdio"
            and invocation.get("complete_coverage") is False
            and invocation.get("confinement_verified") is False,
            "unverified protocol transport or coverage promotion",
        )
        sequences = {}
        frames = {}
        for direction in ("received", "sent"):
            names = sorted(
                name for name in rows if re.fullmatch(r"[0-9]{8}-" + direction + r"\.json", name)
            )
            _require(
                names == [f"{i:08d}-{direction}.json" for i in range(1, len(names) + 1)],
                "protocol sequence has missing frames",
            )
            sequences[direction] = [rows.pop(name) for name in names]
            frames[direction] = [artifacts[name] for name in names]
        _require(not rows, "unmatched protocol artifacts")
        received, sent = sequences["received"], sequences["sent"]
        _require(
            len(sent) >= 4
            and sent[0].get("method") == "initialize"
            and sent[0].get("id") == "initialize"
            and sent[0]["params"]["capabilities"]["experimentalApi"] is True
            and sent[1] == {"method": "initialized"}
            and sent[2]
            == {"id": "thread", "method": "thread/start", "params": invocation["thread_params"]}
            and sent[3]
            == {
                "id": "turn",
                "method": "turn/start",
                "params": {"threadId": thread, "input": invocation["input_items"]},
            },
            "thread initialization differs from retained invocation",
        )
        calls, wire_ids, setup = {}, set(), {}
        mcp_sources, mcp_results = {}, {}
        hosted_sources, hosted_results = {}, {}
        hosted_bound = (
            provider is not None
            and "hosted_tool_contract"
            in json.loads(
                provider["artifacts"]["invocation.json"],
                object_pairs_hook=_object,
                parse_constant=_constant,
            )["binding"]
        )
        complete = False
        scope = AppServerScope(thread, turn)
        call_frames = {}
        for row, raw in zip(received, frames["received"], strict=True):
            _require(not complete, "protocol continues after terminal turn")
            if "method" not in row:
                identity = row.get("id")
                _require(
                    type(identity) is str
                    and identity in ("initialize", "thread", "turn")
                    and identity not in setup
                    and "error" not in row
                    and "result" in row,
                    "invalid setup response",
                )
                setup[identity] = row["result"]
            elif row["method"] == "item/tool/call":
                _require(
                    setup.get("thread", {}).get("thread", {}).get("id") == thread
                    and setup.get("turn", {}).get("turn", {}).get("id") == turn,
                    "tool call precedes bound thread or turn",
                )
                call = scope.parse_call(row)
                identity = (call["turn_id"], call["call_id"])
                wire = (type(row["id"]), row["id"])
                _require(identity not in calls and wire not in wire_ids, "duplicate source call")
                calls[identity] = call
                call_frames[identity] = raw
                wire_ids.add(wire)
            else:
                _require("id" not in row, "unsupported server request")
                complete = scope.feed(row)
                params = row.get("params", {})
                item = params.get("item", {})
                if (
                    hosted_bound
                    and item.get("type") == "webSearch"
                    and row["method"] in {"item/started", "item/completed"}
                ):
                    scope._identity(params.get("threadId"), params.get("turnId"))
                    identity = item.get("id")
                    _require(
                        type(identity) is str and 0 < len(identity) <= 256,
                        "invalid hosted frontend identity",
                    )
                    from aisle.harness.provider_hosted import web_search_action

                    value = {
                        "thread": params["threadId"],
                        "turn": params["turnId"],
                        "action": web_search_action(item.get("action")),
                    }
                    if row["method"] == "item/started":
                        _require(identity not in hosted_sources, "duplicate hosted frontend source")
                        hosted_sources[identity] = value
                    else:
                        _require(
                            identity in hosted_sources
                            and identity not in hosted_results
                            and hosted_sources[identity] == value,
                            "unmatched hosted frontend completion",
                        )
                        hosted_results[identity] = value
                if item.get("type") == "mcpToolCall" and item.get("server") == "aisle_harness":
                    _require(mcp_harness is not None, "MCP harness evidence is missing")
                    scope._identity(params.get("threadId"), params.get("turnId"))
                    identity = (params["turnId"], item["id"])
                    if row["method"] == "item/started":
                        _require(identity not in calls, "duplicate MCP source call")
                        calls[identity] = {
                            "turn_id": identity[0],
                            "call_id": identity[1],
                            "tool_name": "harness." + item["tool"],
                        }
                        call_frames[identity] = raw
                        mcp_sources[identity] = raw
                    elif row["method"] == "item/completed":
                        _require(
                            identity in mcp_sources and identity not in mcp_results,
                            "unmatched MCP completion",
                        )
                        mcp_results[identity] = item
        _require(
            complete and set(setup) == {"initialize", "thread", "turn"},
            "incomplete frontend session",
        )
        replied = set()
        for row in sent[4:]:
            _require(
                set(row) == {"id", "result"} and type(row["id"]) in (str, int),
                "unexpected client message",
            )
            wire = (type(row["id"]), row["id"])
            _require(
                wire in wire_ids and wire not in replied, "missing or duplicate reply identity"
            )
            _require(
                type(row["result"]) is dict
                and set(row["result"]) == {"success", "contentItems"}
                and type(row["result"]["success"]) is bool
                and type(row["result"]["contentItems"]) is list,
                "invalid dynamic reply",
            )
            replied.add(wire)
        _require(replied == wire_ids, "unanswered dynamic call")
        _require(len(wire_ids) == expected["dynamic_calls"], "dynamic call count differs")
        if mcp_harness is not None:
            from aisle.harness.mcp_harness_source import verify_mcp_sources

            _require(
                type(expected.get("mcp_calls")) is int
                and expected["mcp_calls"] == len(mcp_sources),
                "MCP call count differs",
            )
            verify_mcp_sources(**mcp_harness, sources=mcp_sources, completions=mcp_results)
        else:
            _require(expected.get("mcp_calls", 0) == 0, "MCP reference is missing")
        _require(type(grants) is list, "invalid grant list")
        linked = set()
        for grant in grants:
            call = grant["call"]
            _require(
                type(call) is dict
                and type(call.get("turn_id")) is str
                and type(call.get("call_id")) is str,
                "invalid grant identity",
            )
            identity = (call["turn_id"], call["call_id"])
            _require(
                identity not in linked and calls.get(identity) == call,
                "grant has no unique source call",
            )
            linked.add(identity)
        _require(
            linked == set(calls)
            and len(linked) == expected["dynamic_calls"] + expected.get("mcp_calls", 0),
            "unmatched source calls",
        )
        if (
            dispatch is not None
            or dispatch_ceiling is not None
            or code_mode is not None
            or provider is not None
        ):
            from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

            _require(
                type(dispatch_ceiling) is int and dispatch_ceiling > 0,
                "invalid admitted reservation ceiling",
            )
            _require(
                type(dispatch) is dict and set(dispatch) == {"artifacts", "expected", "byte_limit"},
                "trusted dispatch evidence is missing",
            )
            reservation_audit = verify_dispatch_journal(**dispatch)
            _require(
                reservation_audit["ok"],
                "dispatch journal audit failed: " + "; ".join(reservation_audit["errors"]),
            )
            native = set()
            if code_mode is not None:
                from aisle.harness.code_mode_audit import verify_code_mode_sources

                nested = verify_code_mode_sources(**code_mode, dispatch=dispatch)
                _require(nested["ok"], "nested RPC audit failed: " + "; ".join(nested["errors"]))
                native = set(nested["native_attempts"])
            if provider is not None:
                from aisle.harness.provider_source_audit import verify_provider_sources

                delegated = {
                    (namespace["name"], tool["name"], "function_call")
                    for namespace in invocation["thread_params"]["dynamicTools"]
                    for tool in namespace["tools"]
                }
                if code_mode is not None:
                    delegated.update(
                        {
                            (None, "exec", "custom_tool_call"),
                            ("functions", "exec", "custom_tool_call"),
                        }
                    )
                if mcp_harness is not None:
                    delegated.update(
                        ("mcp__aisle_harness", tool["name"], "function_call")
                        for namespace in invocation["thread_params"]["dynamicTools"]
                        for tool in namespace["tools"]
                    )
                upstream = verify_provider_sources(
                    **provider, dispatch=dispatch, delegated_tools=delegated
                )
                _require(upstream["ok"], "provider audit failed: " + "; ".join(upstream["errors"]))
                if "hosted_items" in upstream:
                    hosted_items = upstream["hosted_items"]
                    expected_hosted = {item["id"]: item["action"] for item in hosted_items}
                    _require(
                        len(expected_hosted) == len(hosted_items)
                        and set(expected_hosted) == set(hosted_sources) == set(hosted_results)
                        and all(
                            hosted_results[key]["action"] == action
                            for key, action in expected_hosted.items()
                        ),
                        "hosted provider work differs from owned frontend lifecycle",
                    )
                provider_links = set()
                calls_by_id = {}
                for identity in calls:
                    calls_by_id.setdefault(identity[1], []).append(identity)
                for delegated_call in upstream["delegated_calls"]:
                    if delegated_call["namespace"] not in {"harness", "mcp__aisle_harness"}:
                        continue
                    matches = calls_by_id.get(delegated_call["call_id"], [])
                    _require(len(matches) == 1, "provider call has no unique frontend source")
                    identity = matches[0]
                    _require(
                        (identity in mcp_sources)
                        == (delegated_call["namespace"] == "mcp__aisle_harness"),
                        "provider call uses the wrong harness transport",
                    )
                    source_params = json.loads(call_frames[identity])["params"]
                    arguments = (
                        source_params["item"]["arguments"]
                        if identity in mcp_sources
                        else source_params["arguments"]
                    )
                    _require(
                        identity in calls
                        and identity not in provider_links
                        and calls[identity]["tool_name"] == "harness." + delegated_call["name"]
                        and delegated_call["kind"] == "function_call"
                        and delegated_call["payload"] == arguments,
                        "delegated provider call differs from frontend source",
                    )
                    provider_links.add(identity)
                if code_mode is None:
                    _require(provider_links == set(calls), "frontend call lacks provider source")
                provider_attempts = set(upstream["native_attempts"])
                _require(not provider_attempts & native, "provider and nested reservations overlap")
                native.update(provider_attempts)
            _require(
                dispatch["expected"]["ceiling"] == dispatch_ceiling
                and reservation_audit["attempts"] == len(grants) + len(native)
                and reservation_audit["delivery_uncertain"] is False,
                "reservation ceiling, call count, or delivery differs",
            )
            harness_numbers = [
                number
                for number in range(1, reservation_audit["attempts"] + 1)
                if number not in native
            ]
            for number, grant in zip(harness_numbers, grants, strict=True):
                prefix = f"{number:08d}"
                reservation = json.loads(dispatch["artifacts"][prefix + "-reservation.json"])
                call = grant["call"]
                identity = (call["turn_id"], call["call_id"])
                _require(
                    reservation["decision"] == "authorized"
                    and reservation["call"] == call
                    and grant.get("session_id") == dispatch["expected"]["session_id"]
                    and dispatch["artifacts"][prefix + ".frame"] == call_frames[identity],
                    "reservation does not bind the exact source/grant chain",
                )
            result["reservations_verified"] = True
        result.update(ok=True, linked_calls=len(linked))
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        result["errors"].append(str(exc))
    return result


def read_protocol_prefix(artifacts, *, expected, byte_limit):
    """Recompute protocol ownership for complete or failed sessions, without qualifying work."""
    try:
        _require(type(byte_limit) is int and byte_limit > 0, "invalid protocol byte limit")
        _require(
            type(artifacts) is dict
            and all(type(n) is str and type(v) is bytes for n, v in artifacts.items()),
            "invalid protocol snapshot",
        )
        _require(sum(map(len, artifacts.values())) <= byte_limit, "protocol exceeds byte limit")
        _require(
            type(expected) is dict
            and set(expected) - {"mcp_calls", "stream_complete", "failure"}
            == {"thread_id", "turn_id", "dynamic_calls", "artifacts"},
            "invalid partial protocol reference",
        )
        failed = "failure" in expected or "stream_complete" in expected
        if failed:
            _require(
                expected.get("stream_complete") is False
                and type(expected.get("failure")) is dict
                and set(expected["failure"]) == {"error_type", "error"}
                and all(type(v) is str for v in expected["failure"].values()),
                "invalid partial failure reference",
            )
        _require(
            expected["artifacts"]
            == {n: hashlib.sha256(v).hexdigest() for n, v in artifacts.items()},
            "protocol differs from closed reference",
        )
        rows = {}
        for name, raw in artifacts.items():
            _require(
                len(raw) <= MAX_MESSAGE_BYTES and raw.endswith(b"\n"), "invalid protocol frame"
            )
            rows[name] = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
            _require(type(rows[name]) is dict, "invalid protocol object")
        if failed:
            _require(rows.pop("failure.json") == expected["failure"], "failure receipt differs")
        invocation = rows.pop("invocation.json")
        _require(
            invocation.get("transport") == "owned_stdio"
            and invocation.get("complete_coverage") is False
            and invocation.get("confinement_verified") is False,
            "unverified protocol transport",
        )
        sequences = {}
        for direction in ("sent", "received"):
            names = sorted(n for n in rows if re.fullmatch(r"[0-9]{8}-" + direction + r"\.json", n))
            _require(
                names == [f"{i:08d}-{direction}.json" for i in range(1, len(names) + 1)],
                "incomplete protocol sequence",
            )
            sequences[direction] = [(rows.pop(n), artifacts[n]) for n in names]
        _require(not rows, "unmatched protocol artifacts")
        thread, turn = expected["thread_id"], expected["turn_id"]
        _require(
            all(type(v) is str and 0 < len(v) <= 256 for v in (thread, turn)),
            "invalid thread identity",
        )
        sent = [row for row, _ in sequences["sent"]]
        _require(
            len(sent) >= 4
            and sent[0].get("id") == "initialize"
            and sent[0].get("method") == "initialize"
            and sent[0]["params"]["capabilities"]["experimentalApi"] is True
            and sent[1] == {"method": "initialized"}
            and sent[2]
            == {"id": "thread", "method": "thread/start", "params": invocation["thread_params"]}
            and sent[3]
            == {
                "id": "turn",
                "method": "turn/start",
                "params": {"threadId": thread, "input": invocation["input_items"]},
            },
            "protocol initialization differs",
        )
        setup, calls, identities, wire_ids = {}, [], set(), set()
        scope = AppServerScope(thread, turn)
        mcp_sources, mcp_completions = {}, {}
        failed_turn = None
        for index, (row, raw) in enumerate(sequences["received"]):
            if "method" not in row:
                identity = row.get("id")
                _require(
                    type(identity) is str
                    and identity in {"initialize", "thread", "turn"}
                    and identity not in setup
                    and "error" not in row
                    and "result" in row,
                    "invalid setup reply",
                )
                setup[identity] = row["result"]
            elif row["method"] == "item/tool/call":
                _require(
                    set(setup) == {"initialize", "thread", "turn"}
                    and setup.get("thread", {}).get("thread", {}).get("id") == thread
                    and setup.get("turn", {}).get("turn", {}).get("id") == turn,
                    "unbound setup",
                )
                call = scope.parse_call(row)
                identity = (call["turn_id"], call["call_id"])
                _require(identity not in identities, "replayed dynamic source")
                wire_id = (type(row["id"]), row["id"])
                _require(wire_id not in wire_ids, "replayed dynamic request ID")
                wire_ids.add(wire_id)
                identities.add(identity)
                calls.append((row["id"], call, raw))
            else:
                if row["method"] in {"item/started", "item/completed"}:
                    params = row["params"]
                    item = params["item"]
                    item_thread, item_turn = params.get("threadId"), params.get("turnId")
                    if (
                        item_thread not in scope.active
                        and item.get("type") == "subAgentActivity"
                        and item.get("kind") == "completed"
                    ):
                        # Codex can publish child completion to an already-finished
                        # parent while the owned child turn is still draining.
                        child, path = item.get("agentThreadId"), item.get("agentPath")
                        _require(
                            item_thread in scope.turns
                            and scope.turns[item_thread] == item_turn
                            and child in scope.active
                            and scope.turns[child] is not None
                            and type(path) is str
                            and scope.paths.get(child) == path
                            and path.rsplit("/", 1)[0] == scope.paths[item_thread],
                            "unbound late child completion notification",
                        )
                    else:
                        scope._identity(item_thread, item_turn)
                    if item.get("type") == "mcpToolCall" and item.get("server") == "aisle_harness":
                        key = (params["turnId"], item["id"])
                        target = mcp_sources if row["method"] == "item/started" else mcp_completions
                        _require(key not in target, "duplicate MCP source notification")
                        target[key] = raw if row["method"] == "item/started" else item
                if (
                    failed
                    and row["method"] == "turn/completed"
                    and row.get("params", {}).get("turn", {}).get("status")
                    in {"failed", "interrupted"}
                ):
                    _require(
                        index == len(sequences["received"]) - 1
                        and expected["failure"]
                        in (
                            {"error_type": "ValueError", "error": "app-server turn failed"},
                            {"error_type": "CancelledError", "error": ""},
                        ),
                        "failed turn is not the retained terminal failure",
                    )
                    params = row["params"]
                    scope._identity(params.get("threadId"), params["turn"].get("id"))
                    failed_turn = row
                else:
                    scope.feed(row)
        _require(
            type(expected["dynamic_calls"]) is int and expected["dynamic_calls"] == len(calls),
            "dynamic source count differs",
        )
        _require(
            set(setup) == {"initialize", "thread", "turn"}
            and setup["thread"]["thread"]["id"] == thread
            and setup["turn"]["turn"]["id"] == turn,
            "incomplete bound setup",
        )
        _require(
            type(expected.get("mcp_calls", 0)) is int
            and expected.get("mcp_calls", 0) == len(mcp_sources),
            "MCP source count differs",
        )
        return {
            "scope": scope,
            "sequences": sequences,
            "calls": calls,
            "sent": sent,
            "mcp_sources": mcp_sources,
            "mcp_completions": mcp_completions,
            "invocation": invocation,
            "failed_turn": failed_turn,
        }
    except (TypeError, KeyError, IndexError, AttributeError, RecursionError, UnicodeError) as exc:
        raise ValueError("invalid protocol prefix") from exc


def link_dynamic_refusal_sources(artifacts, *, expected, dispatch, byte_limit):
    """Link exhausted dynamic calls to a closed partial protocol; not full session qualification."""
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal

    report = {
        "ok": False,
        "errors": [],
        "refused_attempts": [],
        "complete_coverage": False,
        "confinement_verified": False,
    }
    try:
        prefix = read_protocol_prefix(artifacts, expected=expected, byte_limit=byte_limit)
        _require(
            expected.get("failure", {}).get("error_type") == "DispatchRefused",
            "not a dynamic dispatch refusal",
        )
        scope, sequences, calls, sent = (
            prefix[key] for key in ("scope", "sequences", "calls", "sent")
        )
        replay = verify_dispatch_journal(**dispatch)
        _require(
            replay["ok"] and not replay["delivery_uncertain"], "invalid refusal dispatch journal"
        )
        linked = []
        for refusal in replay["budget_refusals"]:
            if refusal["kind"] != "local" or not refusal["call"]["tool_name"].startswith(
                "harness."
            ):
                continue
            frame = refusal["reservation"].removesuffix("-reservation.json") + ".frame"
            _require(
                not scope.complete and sequences["received"][-1][1] == dispatch["artifacts"][frame],
                "dynamic protocol continued after refusal",
            )
            matches = [
                (wire, call)
                for wire, call, raw in calls
                if call == refusal["call"] and raw == dispatch["artifacts"][frame]
            ]
            _require(len(matches) == 1, "refused dynamic call lacks exact owned source")
            wire, _ = matches[0]
            _require(
                not any(
                    type(row.get("id")) is type(wire) and row.get("id") == wire for row in sent[4:]
                ),
                "refused dynamic call received a reply",
            )
            linked.append(int(frame.removesuffix(".frame")))
        _require(bool(linked), "no dynamic quota refusal to link")
        report.update(ok=True, refused_attempts=linked)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError) as exc:
        report["errors"].append(str(exc))
    return report
