"""Admit provider tool items before forwarding their executable SSE events.

This layer consumes an already bounded, complete Responses API stream. It does
not own HTTP acquisition, authenticate the provider, or establish confinement.
Delegation is supplied only by the owning controller for routes it mediates at
a later boundary. Hosted work has already happened upstream and is not made
subject to local admission by observing its response.
"""

from __future__ import annotations

import hashlib
import json
import threading

from aisle.harness.frontend_dispatch import MAX_FRAME_BYTES


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate provider JSON field")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("non-finite provider JSON value")


def _identity(value):
    return type(value) is str and 0 < len(value) <= 256


def _events(raw):
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_FRAME_BYTES, "unbounded provider stream")
    # Preserve the exact bytes that the frontend receives, including CRLF.
    start = 0
    event_name = None
    data = []
    offset = 0
    for line in raw.splitlines(keepends=True):
        offset += len(line)
        _require(line.endswith(b"\n"), "truncated provider event")
        line = line.removesuffix(b"\n").removesuffix(b"\r")
        if not line:
            if data:
                event = json.loads(
                    b"\n".join(data).decode("utf-8"),
                    object_pairs_hook=_object,
                    parse_constant=_constant,
                )
                _require(type(event) is dict, "provider event is not an object")
                _require(_identity(event.get("type")), "missing provider event type")
                _require(event_name in (None, event["type"]), "provider SSE type differs")
                yield raw[start:offset], event
            else:
                # Comments/heartbeats do not carry executable items.
                yield raw[start:offset], None
            start, event_name, data = offset, None, []
        elif line.startswith(b":"):
            continue
        else:
            field, separator, value = line.partition(b":")
            _require(separator and field in (b"event", b"data"), "unsupported SSE field")
            value = value.removeprefix(b" ")
            if field == b"event":
                _require(event_name is None, "duplicate SSE event field")
                event_name = value.decode("utf-8")
            else:
                data.append(value)
    _require(start == len(raw), "unterminated provider event")


def _response(raw):
    events = list(_events(raw))
    _require(len(events) <= 65536, "too many provider events")
    response_id = None
    completed = False
    items = {}
    added = {}
    call_ids = set()
    item_ids = set()
    calls = {}
    for position, (_, event) in enumerate(events):
        if event is None:
            continue
        _require(not completed, "provider event follows completion")
        kind = event["type"]
        if kind == "response.created":
            _require(response_id is None, "duplicate response creation")
            response = event.get("response")
            _require(type(response) is dict and _identity(response.get("id")), "invalid response")
            response_id = response["id"]
            continue
        _require(response_id is not None, "provider event precedes response creation")
        if kind in ("response.output_item.added", "response.output_item.done"):
            index, item = event.get("output_index"), event.get("item")
            _require(type(index) is int and 0 <= index < 65536, "invalid output index")
            _require(type(item) is dict and _identity(item.get("type")), "invalid output item")
            if kind.endswith("added"):
                _require(index not in added and index not in items, "duplicate added item")
                added[index] = item
                continue
            _require(index not in items, "duplicate completed output item")
            _require(
                _identity(item.get("id")) and item["id"] not in item_ids, "invalid item identity"
            )
            item_ids.add(item["id"])
            if index in added:
                for key in ("id", "type", "call_id", "name", "namespace"):
                    _require(added[index].get(key) == item.get(key), "added item identity changed")
            items[index] = item
            if item["type"] in ("function_call", "custom_tool_call"):
                _require(_identity(item.get("call_id")), "invalid provider call identity")
                _require(item["call_id"] not in call_ids, "duplicate provider call identity")
                call_ids.add(item["call_id"])
                name, namespace = item.get("name"), item.get("namespace")
                _require(_identity(name), "invalid provider tool name")
                _require(namespace is None or _identity(namespace), "invalid provider namespace")
                payload = "arguments" if item["type"] == "function_call" else "input"
                _require(type(item.get(payload)) is str, "invalid provider call payload")
                calls[position] = (item["call_id"], namespace, name, item["type"])
            elif item["type"] == "tool_search_call" and item.get("execution") == "client":
                _require(_identity(item.get("call_id")), "invalid provider call identity")
                _require(item["call_id"] not in call_ids, "duplicate provider call identity")
                _require(type(item.get("arguments")) is dict, "invalid tool search arguments")
                call_ids.add(item["call_id"])
                calls[position] = (item["call_id"], None, "tool_search", "tool_search_call")
        elif kind == "response.completed":
            response = event.get("response")
            _require(type(response) is dict, "invalid response completion")
            _require(response.get("id") == response_id, "response identity changed")
            _require(response.get("status") == "completed", "response did not complete")
            output = response.get("output")
            _require(type(output) is list, "missing completed output")
            _require(set(items) == set(range(len(output))), "completed output inventory differs")
            _require(
                all(items[i] == item for i, item in enumerate(output)), "completed output differs"
            )
            _require(set(added) <= set(items), "unfinished added item")
            completed = True
        elif kind in ("response.failed", "response.incomplete", "error"):
            raise ValueError("provider response failed")
    _require(completed, "provider response missing completion")
    return response_id, events, calls


class ProviderResponseAuthority:
    """Serialize response admission with one session's shared dispatch authority.

    The entire stream is validated before release, then each executable item is
    reserved separately. If a later item is refused, the already authorized
    prefix remains consumed; response.completed is never forwarded on refusal.
    Any stream failure retires this adapter. No upstream response retry can
    acquire fresh identities within this lifetime.
    """

    def __init__(self, dispatch, *, delegated_tools=()):
        self.dispatch = dispatch
        self.delegated_tools = frozenset(delegated_tools)
        _require(
            all(
                type(tool) is tuple
                and len(tool) == 3
                and (tool[0] is None or _identity(tool[0]))
                and _identity(tool[1])
                and tool[2] in ("function_call", "custom_tool_call", "tool_search_call")
                for tool in self.delegated_tools
            ),
            "invalid delegated provider tools",
        )
        self._seen = set()
        self._call_ids = set()
        self._closed = False
        self._lock = threading.Lock()

    def forward(self, raw, deliver):
        """Write validated events, reserving native items immediately before delivery."""
        with self._lock:
            _require(not self._closed, "provider authority is closed")
            try:
                response_id, events, calls = _response(raw)
                _require(response_id not in self._seen, "provider response replay")
                _require(len(self._seen) < 65536, "provider response limit exceeded")
                call_ids = {call[0] for call in calls.values()}
                _require(not call_ids & self._call_ids, "provider call identity replay")
                _require(
                    len(self._call_ids) + len(call_ids) <= 65536, "provider call limit exceeded"
                )
                self._seen.add(response_id)
                self._call_ids.update(call_ids)
                turn_id = hashlib.sha256(
                    json.dumps(
                        ["provider-response", self.dispatch.session_id, response_id]
                    ).encode()
                ).hexdigest()
                results = []
                for position, (frame, _) in enumerate(events):
                    call = calls.get(position)
                    if call is None or call[1:] in self.delegated_tools:
                        deliver(frame)
                        continue
                    call_id, namespace, name, kind = call
                    tool_name = json.dumps(
                        [namespace, name, kind], separators=(",", ":"), ensure_ascii=False
                    )
                    results.append(
                        self.dispatch.dispatch(
                            {"turn_id": turn_id, "call_id": call_id, "tool_name": tool_name},
                            frame,
                            deliver,
                        )
                    )
                return results
            except BaseException:
                self._closed = True
                raise
