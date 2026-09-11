"""Bind nested callbacks to owned host sessions and admitted execution catalogs.

This state machine is a transport component. Its caller must acquire and retain
RPC frames, authenticate the host connection, propagate failure, and close leases.
It does not attest complete frontend coverage or external confinement.
"""

from __future__ import annotations

import hashlib
import json
import threading

from google.protobuf.message import DecodeError

from aisle.harness._code_mode_protocol import message
from aisle.harness.frontend_dispatch import MAX_FRAME_BYTES


class CallbackRetired(ValueError):
    """A valid late callback belongs to an already cancelled invocation or cell."""


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _identity(value):
    _require(type(value) is str and 0 < len(value) <= 256, "invalid Code Mode identity")
    return value


def _name(value):
    name = _identity(value.name)
    _require("." not in name, "ambiguous tool identity")
    if value.HasField("namespace"):
        namespace = _identity(value.namespace)
        _require("." not in namespace, "ambiguous tool identity")
        return namespace + "." + name
    return name


def _decode(kind, raw, limit=4 * 1024 * 1024):
    _require(type(raw) is bytes and len(raw) <= limit, "invalid Code Mode frame size")
    try:
        value = message(kind, raw)
    except DecodeError as exc:
        raise ValueError("malformed Code Mode frame") from exc
    known = message(kind)
    known.CopyFrom(value)
    known.DiscardUnknownFields()
    _require(
        known.SerializeToString(deterministic=True) == raw,
        "unknown or noncanonical Code Mode fields",
    )
    return value


def _object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "duplicate tool argument")
        value[key] = item
    return value


def _constant(value):
    raise ValueError("non-finite tool argument")


class CodeModeAuthority:
    """Authorize each nested native call once; delegate harness admission explicitly.

    ``dispatch`` is shared with the App Server request/grant boundary. Only the
    launcher-declared dynamic or owned MCP harness tools may defer charging to that boundary.
    Their callbacks cannot execute controller work without that later reservation.
    """

    def __init__(self, dispatch, *, delegated_tools):
        _require(
            type(delegated_tools) is set
            and delegated_tools
            <= {
                "harness.check",
                "harness.run",
                "mcp__aisle_harness.check",
                "mcp__aisle_harness.run",
            },
            "unverified delegated tool boundary",
        )
        self.dispatch = dispatch
        self.delegated_tools = frozenset(delegated_tools)
        self._lock = threading.RLock()
        self._sessions = {}
        self._executions = {}
        self._invocations = set()
        self._cancelled = set()
        self._forwarded = set()
        self._completed = set()
        self._notifications = set()

    def open_session(self, session_id):
        with self._lock:
            _identity(session_id)
            _require(session_id not in self._sessions, "host session replay")
            self._sessions[session_id] = True

    def _session(self, session_id):
        _require(session_id in self._sessions, "unbound host session")
        _require(self._sessions[session_id], "host session is closed")

    def execute(self, raw):
        with self._lock:
            request = _decode("ExecuteRequest", raw)
            self._session(request.session_id)
            _identity(request.execution_id)
            _identity(request.tool_call_id)
            key = (request.session_id, request.execution_id)
            _require(key not in self._executions, "execution identity replay")
            tools = {}
            aliases = set()
            for tool in request.enabled_tools:
                name = _name(tool.tool_name)
                alias = _identity(tool.name)
                _require(name not in tools and alias not in aliases, "duplicate execution tool")
                _require(tool.kind in (1, 2), "unsupported execution tool kind")
                tools[name] = tool.kind
                aliases.add(alias)
            self._executions[key] = {
                "tools": tools,
                "sequence": 0,
                "cell_id": None,
                "runtime_ids": set(),
                "started": False,
                "outcome": False,
                "closed": False,
                "final_sequence": None,
            }

    def _execution(self, session_id, execution_id):
        self._session(session_id)
        key = (session_id, execution_id)
        _require(key in self._executions, "unbound execution")
        return self._executions[key]

    def _cell(self, state, cell_id):
        cell = _identity(cell_id)
        _require(state["cell_id"] in (None, cell), "execution cell identity differs")
        state["cell_id"] = cell

    def execution_event(self, session_id, execution_id, raw):
        with self._lock:
            state = self._execution(session_id, execution_id)
            event = _decode("ExecuteEvent", raw)
            kind = event.WhichOneof("event")
            if kind == "started":
                _require(not state["started"], "duplicate execution start")
                _require(event.started.execution_id == execution_id, "execution identity differs")
                self._cell(state, event.started.cell_id)
                state["started"] = True
            elif kind == "outcome":
                _require(state["started"] and not state["outcome"], "unexpected execution outcome")
                self._cell(state, event.outcome.cell_id)
                _require(
                    event.outcome.WhichOneof("outcome") is not None, "missing execution outcome"
                )
                state["outcome"] = True
            else:
                raise ValueError("missing execution event")

    def execution_finished(self, session_id, execution_id):
        with self._lock:
            state = self._execution(session_id, execution_id)
            _require(state["started"] and state["outcome"], "incomplete execution stream")

    def session_event(self, session_id, raw):
        with self._lock:
            self._session(session_id)
            event = _decode("SessionEvent", raw)
            kind = event.WhichOneof("event")
            if kind == "tool_call_cancelled":
                self.cancel(session_id, event.tool_call_cancelled.invocation_id)
            elif kind == "cell_closed":
                closed = event.cell_closed
                state = self._execution(session_id, closed.execution_id)
                self._cell(state, closed.cell_id)
                _require(not state["closed"], "duplicate cell closure")
                _require(
                    closed.final_tool_call_sequence >= state["sequence"], "final sequence regressed"
                )
                state["closed"] = True
                state["final_sequence"] = closed.final_tool_call_sequence
            elif kind == "notification":
                notice = event.notification
                state = self._execution(session_id, notice.execution_id)
                self._cell(state, notice.cell_id)
                key = (session_id, _identity(notice.notification_id))
                _require(key not in self._notifications, "notification replay")
                self._notifications.add(key)
            elif kind == "notification_cancelled":
                # As with tool cancellation, this may precede the notification.
                _identity(event.notification_cancelled.notification_id)
            else:
                raise ValueError("unexpected session event")

    def complete(self, raw):
        with self._lock:
            request = _decode("CompleteToolCallRequest", raw)
            self._session(request.session_id)
            key = (request.session_id, _identity(request.invocation_id))
            _require(
                key in self._forwarded and key not in self._completed,
                "unbound or repeated invocation completion",
            )
            _require(request.WhichOneof("outcome") is not None, "missing invocation outcome")
            self._completed.add(key)

    def callback(self, raw, deliver):
        """Validate the owned callback before any forwarding callback can run."""
        with self._lock:
            call = _decode("ToolCall", raw, MAX_FRAME_BYTES)
            self._session(call.session_id)
            key = (call.session_id, call.execution_id)
            _require(key in self._executions, "unbound execution")
            state = self._executions[key]
            invocation = (call.session_id, _identity(call.invocation_id))
            _require(invocation not in self._invocations, "invocation identity replay")
            runtime_id = _identity(call.runtime_tool_call_id)
            _require(runtime_id not in state["runtime_ids"], "runtime identity replay")
            _require(call.sequence == state["sequence"] + 1, "callback sequence differs")
            _require(
                state["final_sequence"] is None or call.sequence <= state["final_sequence"],
                "callback exceeds final sequence",
            )
            cell = _identity(call.cell_id)
            _require(state["cell_id"] in (None, cell), "execution cell identity differs")
            name = _name(call.tool_name)
            _require(state["tools"].get(name) == call.tool_kind, "unadmitted tool or kind")
            _require(call.HasField("input_json"), "missing tool arguments")
            json.loads(call.input_json, object_pairs_hook=_object, parse_constant=_constant)
            state["sequence"] = call.sequence
            state["cell_id"] = cell
            state["runtime_ids"].add(runtime_id)
            self._invocations.add(invocation)
            if state["closed"] or invocation in self._cancelled:
                raise CallbackRetired("nested invocation cancelled or cell closed")
            if name in self.delegated_tools:
                _require(call.tool_kind == 1, "delegated harness tool is not a function")
                deliver(raw)
                self._forwarded.add(invocation)
                return {"delegated": True}
            result = self.dispatch.dispatch(
                {
                    "turn_id": "code-mode:"
                    + hashlib.sha256(json.dumps(key, separators=(",", ":")).encode()).hexdigest(),
                    "call_id": runtime_id,
                    "tool_name": name,
                },
                raw,
                deliver,
            )
            self._forwarded.add(invocation)
            return result

    def cancel(self, session_id, invocation_id):
        with self._lock:
            self._session(session_id)
            self._cancelled.add((session_id, _identity(invocation_id)))

    def close_session(self, session_id):
        with self._lock:
            # Explicit CloseSession and lease-stream retirement can race.
            _require(session_id in self._sessions, "unbound host session")
            self._sessions[session_id] = False
