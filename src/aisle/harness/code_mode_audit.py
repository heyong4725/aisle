"""Replay controller-acquired RPC frames and identify their exact dispatch entries.

The caller authenticates the closed reference and bounds snapshot acquisition.
This audit establishes only the nested callback subset, never full frontend
coverage, external confinement, or a fabricated host-to-App-Server call ID link.
"""

from __future__ import annotations

import base64
import hashlib
import json

from aisle.harness._code_mode_protocol import DESCRIPTOR, message
from aisle.harness.code_mode_authority import CallbackRetired, CodeModeAuthority, _decode, _require
from aisle.harness.frontend_dispatch import DispatchRefused
from aisle.harness.frontend_dispatch_audit import _constant, _object, verify_dispatch_journal


class _Reservations:
    def __init__(self, dispatch, *, empty_rpc=False):
        report = verify_dispatch_journal(**dispatch)
        _require(
            report["ok"] and (empty_rpc or report["delivery_uncertain"] is False),
            "invalid nested dispatch evidence",
        )
        self.rows = []
        self.used = set()
        for number in range(1, dispatch["expected"]["attempts"] + 1):
            prefix = f"{number:08d}"
            self.rows.append(
                (
                    number,
                    json.loads(dispatch["artifacts"][prefix + "-reservation.json"]),
                    dispatch["artifacts"][prefix + ".frame"],
                )
            )
        _require(
            not empty_rpc
            or not any(row["call"]["turn_id"].startswith("code-mode:") for _, row, _ in self.rows),
            "empty RPC evidence hides nested reservations",
        )

    def dispatch(self, call, frame, deliver):
        matches = [
            (number, row) for number, row, raw in self.rows if row["call"] == call and raw == frame
        ]
        _require(len(matches) == 1, "nested call lacks a unique exact reservation frame")
        number, row = matches[0]
        _require(number not in self.used, "nested reservation reused")
        self.used.add(number)
        if row["decision"] == "refused":
            _require(row["reason"] == "dispatch budget exhausted", "invalid nested refusal")
            raise DispatchRefused(row["reason"])
        deliver(frame)
        return {"reservation": row}


def verify_code_mode_sources(
    artifacts, *, expected, delegated_tools, dispatch, byte_limit, host_failure=None
):
    """Return the disjoint native reservation numbers after semantic RPC replay."""
    report = {
        "ok": False,
        "errors": [],
        "native_attempts": [],
        "delegated_calls": [],
        "complete_coverage": False,
        "confinement_verified": False,
    }
    try:
        _require(host_failure is None, "nested host did not finish cleanly")
        _require(type(byte_limit) is int and byte_limit > 0, "invalid RPC audit byte limit")
        _require(
            type(artifacts) is dict
            and all(type(k) is str and type(v) is bytes for k, v in artifacts.items()),
            "invalid RPC snapshot",
        )
        _require(sum(map(len, artifacts.values())) <= byte_limit, "RPC snapshot exceeds limit")
        _require(
            type(expected) is dict
            and set(expected)
            == {"artifacts", "bytes", "failure", "complete_coverage", "confinement_verified"},
            "invalid RPC reference",
        )
        _require(
            expected["failure"] is None
            and expected["complete_coverage"] is False
            and expected["confinement_verified"] is False,
            "failed or promoted RPC reference",
        )
        _require(
            type(expected["bytes"]) is int
            and expected["bytes"] == sum(map(len, artifacts.values()))
            and expected["artifacts"]
            == {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()},
            "RPC snapshot differs from closed controller reference",
        )
        _require(
            0 <= len(artifacts) <= 10000
            and set(artifacts) == {f"{n:08d}.json" for n in range(1, len(artifacts) + 1)},
            "RPC record sequence differs",
        )
        # An unused nested route has no delivery to certify. A separately
        # audited native interruption must not invalidate that empty closure;
        # any nested reservation still requires the strict delivery audit.
        reservations = _Reservations(dispatch, empty_rpc=not artifacts)
        authority = CodeModeAuthority(reservations, delegated_tools=delegated_tools)
        methods = DESCRIPTOR.services_by_name["CodeModeHost"].methods_by_name
        rpcs = {}
        delegated_calls = {}
        for filename in sorted(artifacts):
            row = json.loads(
                artifacts[filename], object_pairs_hook=_object, parse_constant=_constant
            )
            _require(
                type(row) is dict and set(row) == {"rpc", "method", "phase", "frame"},
                "invalid RPC record fields",
            )
            rpc, method, phase = row["rpc"], row["method"], row["phase"]
            _require(
                type(rpc) is int and rpc > 0 and method in methods and type(row["frame"]) is str,
                "invalid RPC identity",
            )
            raw = base64.b64decode(row["frame"], validate=True)
            definition = methods[method]
            if phase == "request":
                _require(rpc == len(rpcs) + 1, "RPC request identity replay or gap")
                request = _decode(definition.input_type.name, raw)
                if method == "Execute":
                    authority.execute(raw)
                elif method == "CompleteToolCall":
                    authority.complete(raw)
                    key = (request.session_id, request.invocation_id)
                    if key in delegated_calls and request.WhichOneof("outcome") == "succeeded":
                        delegated_calls[key]["output_json"] = request.succeeded.output_json
                elif method != "OpenSession":
                    authority._session(request.session_id)
                rpcs[rpc] = {
                    "method": method,
                    "request": request,
                    "pending": None,
                    "lease": None,
                    "ended": False,
                    "response": None,
                }
                continue
            _require(rpc in rpcs and rpcs[rpc]["method"] == method, "unbound RPC event")
            state = rpcs[rpc]
            request = state["request"]
            _require(not state["ended"], "RPC event after stream termination")
            if phase == "response":
                _require(state["pending"] is None, "missing RPC delivery disposition")
                value = _decode(definition.output_type.name, raw)
                disposition = "forwarded" if definition.server_streaming else "returned"
                if method == "OpenSession":
                    if state["lease"] is None:
                        _require(
                            value.WhichOneof("event") == "opened", "missing host lease opening"
                        )
                        state["lease"] = value.opened.session_id
                        authority.open_session(state["lease"])
                    else:
                        authority.session_event(state["lease"], raw)
                elif method == "Execute":
                    authority.execution_event(request.session_id, request.execution_id, raw)
                elif method == "SubscribeToToolCalls":
                    _require(value.session_id == request.session_id, "subscription session differs")
                    try:
                        result = authority.callback(raw, lambda _: None)
                        if result.get("delegated"):
                            delegated_calls[(value.session_id, value.invocation_id)] = {
                                "session_id": value.session_id,
                                "invocation_id": value.invocation_id,
                                "runtime_call_id": value.runtime_tool_call_id,
                                "tool_name": value.tool_name.namespace + "." + value.tool_name.name,
                                "arguments": json.loads(
                                    value.input_json,
                                    object_pairs_hook=_object,
                                    parse_constant=_constant,
                                ),
                                "output_json": None,
                            }
                    except CallbackRetired:
                        disposition = "retired"
                    except DispatchRefused:
                        disposition = "refused"
                elif method == "CloseSession":
                    authority.close_session(request.session_id)
                state["pending"] = disposition
                state["response"] = value
            elif phase in {"forwarded", "returned", "refused", "retired"}:
                _require(state["pending"] == phase, "RPC disposition differs from authorization")
                if phase == "refused":
                    call = state["response"]
                    refusal = message(
                        "CompleteToolCallRequest",
                        session_id=call.session_id,
                        invocation_id=call.invocation_id,
                        failed={"message": "AISLE nested dispatch budget exhausted"},
                    )
                    _require(raw == refusal.SerializeToString(), "nested refusal identity differs")
                else:
                    _require(raw == b"", "unexpected disposition frame")
                state["pending"] = None
                if phase == "returned":
                    state["ended"] = True
            elif phase in {"finished", "cancelled"}:
                _require(raw == b"" and state["pending"] is None, "unfinished RPC delivery")
                if phase == "finished":
                    _require(definition.server_streaming, "unary RPC lacks response")
                    if method == "Execute":
                        authority.execution_finished(request.session_id, request.execution_id)
                    if method == "OpenSession":
                        _require(state["lease"] is not None, "host stream ended before opening")
                if state["lease"] is not None and authority._sessions[state["lease"]]:
                    authority.close_session(state["lease"])
                state["ended"] = True
            else:
                raise ValueError("unsupported RPC evidence phase")
        _require(all(state["ended"] for state in rpcs.values()), "incomplete RPC evidence")
        report.update(
            ok=True,
            native_attempts=sorted(reservations.used),
            delegated_calls=list(delegated_calls.values()),
        )
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        report["errors"].append(str(exc))
    return report


def verify_code_mode_hosted_refusal(
    artifacts,
    *,
    expected,
    delegated_tools,
    dispatch,
    byte_limit,
    host_failure,
    provider,
    provider_delegated_tools,
):
    """Replay a closed nested refusal alongside a terminal hosted denial.

    This verifies source records, not successful host execution or the cause of
    cancellation. Both original references remain unchanged; callers still bind
    launch identities and join controller results before qualifying a scenario.
    """
    from aisle.harness.frontend_dispatch_audit import verify_dispatch_journal
    from aisle.harness.provider_source_audit import verify_hosted_refusal_prefix

    report = {
        "ok": False,
        "errors": [],
        "host_failure": host_failure,
        "host_cancelled": host_failure == "CancelledError: ",
        "complete_coverage": False,
        "confinement_verified": False,
    }
    try:
        _require(
            host_failure in {"CancelledError: ", "ValueError: app-server turn failed"},
            "unexpected nested host failure",
        )
        upstream = verify_hosted_refusal_prefix(
            **provider, dispatch=dispatch, delegated_tools=provider_delegated_tools
        )
        _require(
            upstream["ok"] and bool(upstream["linked_requests"]),
            "cancelled nested host lacks a verified hosted refusal",
        )
        nested = verify_code_mode_sources(
            artifacts,
            expected=expected,
            delegated_tools=delegated_tools,
            dispatch=dispatch,
            byte_limit=byte_limit,
        )
        _require(nested["ok"], "cancelled nested host lacks a closed replayable RPC journal")
        journal = verify_dispatch_journal(**dispatch)
        _require(
            journal["ok"] and not journal["delivery_uncertain"],
            "cancelled nested host has uncertain dispatch",
        )
        refused = [
            int(row["reservation"].removesuffix("-reservation.json"))
            for row in journal["budget_refusals"]
            if row["kind"] == "local" and row["call"]["turn_id"].startswith("code-mode:")
        ]
        _require(
            bool(refused) and set(refused).issubset(nested["native_attempts"]),
            "cancelled nested host lacks a replayed nested refusal",
        )
        _require(
            not set(nested["native_attempts"]) & set(upstream["native_attempts"]),
            "nested and provider reservations overlap",
        )
        report.update(
            ok=True,
            native_attempts=nested["native_attempts"],
            delegated_calls=nested["delegated_calls"],
            refused_nested_attempts=sorted(refused),
            linked_hosted_requests=upstream["linked_requests"],
        )
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, RecursionError) as exc:
        report["errors"].append(str(exc))
    return report
