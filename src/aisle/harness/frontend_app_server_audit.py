"""Bind normalized request grants to retained messages from an owned frontend pipe.

The caller supplies the transport's write-time reference from trusted memory or
trusted retention, not a reference rebuilt from the files being audited. This
checks successful dynamic-tool sessions, not coverage of built-in execution.
"""

from __future__ import annotations

import hashlib
import json
import re

from aisle.harness.frontend_app_server import MAX_MESSAGE_BYTES, parse_dynamic_call


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
            and set(expected) == {"thread_id", "turn_id", "dynamic_calls", "artifacts"},
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
        complete = False
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
                call = parse_dynamic_call(row, thread_id=thread, turn_id=turn)
                identity = (call["turn_id"], call["call_id"])
                wire = (type(row["id"]), row["id"])
                _require(identity not in calls and wire not in wire_ids, "duplicate source call")
                calls[identity] = call
                call_frames[identity] = raw
                wire_ids.add(wire)
            else:
                _require("id" not in row, "unsupported server request")
                if row["method"] == "turn/completed":
                    params = row["params"]
                    _require(
                        params["threadId"] == thread
                        and params["turn"]["id"] == turn
                        and params["turn"]["status"] == "completed",
                        "unsuccessful terminal turn",
                    )
                    complete = True
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
            linked == set(calls) and len(linked) == expected["dynamic_calls"],
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
                upstream = verify_provider_sources(
                    **provider, dispatch=dispatch, delegated_tools=delegated
                )
                _require(upstream["ok"], "provider audit failed: " + "; ".join(upstream["errors"]))
                provider_links = set()
                for delegated_call in upstream["delegated_calls"]:
                    if delegated_call["namespace"] != "harness":
                        continue
                    identity = (turn, delegated_call["call_id"])
                    _require(
                        identity in calls
                        and identity not in provider_links
                        and calls[identity]["tool_name"] == "harness." + delegated_call["name"]
                        and delegated_call["kind"] == "function_call"
                        and delegated_call["payload"]
                        == json.loads(call_frames[identity])["params"]["arguments"],
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
