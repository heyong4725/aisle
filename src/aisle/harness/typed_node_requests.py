"""Typed policy requests against a trusted turn-accounted Node.

This is a data/request boundary, not a process sandbox. The launcher must confine
workers, bind their code and restrict their outputs from the admitted graph.
The payload codec covers primitive Arrow arrays used by the current typed nodes;
it never deserializes an Arrow extension or imports a type named by the worker.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pyarrow as pa

from aisle.monolith.wire import MAX_BYTES, MAX_NODES

_NUMERIC = {
    str(arrow): (arrow, np.dtype(dtype))
    for arrow, dtype in (
        (pa.bool_(), "bool"),
        (pa.int8(), "int8"),
        (pa.int16(), "int16"),
        (pa.int32(), "int32"),
        (pa.int64(), "int64"),
        (pa.uint8(), "uint8"),
        (pa.uint16(), "uint16"),
        (pa.uint32(), "uint32"),
        (pa.uint64(), "uint64"),
        (pa.float16(), "float16"),
        (pa.float32(), "float32"),
        (pa.float64(), "float64"),
    )
}
_TEXT = {"string": pa.string(), "large_string": pa.large_string()}


class NodeRequestError(ValueError):
    """A typed worker requested an undeclared operation or malformed value."""


def pack_array(value):
    """Detach primitive array values and validity without executable type metadata."""
    if not isinstance(value, pa.Array) or value.nbytes > MAX_BYTES:
        raise NodeRequestError("node payload is not a bounded Arrow array")
    name = str(value.type)
    if name in _NUMERIC and value.type == _NUMERIC[name][0]:
        fill = False if name == "bool" else 0
        return {
            "type": name,
            "values": value.fill_null(fill).to_numpy(zero_copy_only=False).copy(),
            "nulls": value.is_null().to_numpy(zero_copy_only=False),
        }
    if name in _TEXT and value.type == _TEXT[name] and len(value) <= MAX_NODES:
        return {"type": name, "values": value.to_pylist()}
    if value.type == pa.null() and len(value) <= MAX_BYTES:
        return {"type": "null", "length": len(value)}
    raise NodeRequestError("node payload has an unsupported Arrow type or length")


def unpack_array(record):
    """Construct only explicit primitive types from validated data fields."""
    if type(record) is not dict or type(record.get("type")) is not str:
        raise NodeRequestError("node payload envelope is invalid")
    name = record["type"]
    if name in _NUMERIC and set(record) == {"type", "values", "nulls"}:
        arrow, dtype = _NUMERIC[name]
        values, nulls = record["values"], record["nulls"]
        if (
            type(values) is not np.ndarray
            or type(nulls) is not np.ndarray
            or values.ndim != 1
            or nulls.ndim != 1
            or values.shape != nulls.shape
            or values.dtype != dtype
            or nulls.dtype != np.dtype("bool")
            or values.nbytes + nulls.nbytes > MAX_BYTES
        ):
            raise NodeRequestError("node numeric payload shape, dtype or size differs")
        return pa.array(values.copy(), mask=nulls.copy(), type=arrow)
    if name in _TEXT and set(record) == {"type", "values"}:
        values = record["values"]
        if (
            type(values) is not list
            or len(values) > MAX_NODES
            or any(value is not None and type(value) is not str for value in values)
        ):
            raise NodeRequestError("node text payload is invalid")
        if sum(len(value.encode()) for value in values if value is not None) > MAX_BYTES:
            raise NodeRequestError("node text payload is oversized")
        return pa.array(values, type=_TEXT[name])
    if name == "null" and set(record) == {"type", "length"}:
        length = record["length"]
        if type(length) is int and 0 <= length <= MAX_BYTES:
            return pa.nulls(length)
    raise NodeRequestError("node payload type or fields are undeclared")


def pack_metadata(metadata):
    """Separate Dora's datetime timestamp from ordinary data without mutating the event."""
    if type(metadata) is not dict:
        raise NodeRequestError("node metadata must be a mapping")
    values = dict(metadata)
    timestamp = None
    if type(values.get("timestamp")) is datetime:
        value = values.pop("timestamp")
        timestamp = {"iso": value.isoformat(), "fold": value.fold}
    return {"values": values, "datetime_timestamp": timestamp}


def unpack_metadata(record):
    """Reconstruct only the declared built-in datetime; no peer-selected types are loaded."""
    if (
        type(record) is not dict
        or set(record) != {"values", "datetime_timestamp"}
        or type(record["values"]) is not dict
    ):
        raise NodeRequestError("node metadata envelope is invalid")
    values = dict(record["values"])
    timestamp = record["datetime_timestamp"]
    if timestamp is not None:
        if (
            "timestamp" in values
            or type(timestamp) is not dict
            or set(timestamp) != {"iso", "fold"}
            or type(timestamp["iso"]) is not str
            or len(timestamp["iso"]) > 64
            or type(timestamp["fold"]) is not int
            or timestamp["fold"] not in (0, 1)
        ):
            raise NodeRequestError("node datetime timestamp is invalid")
        try:
            value = datetime.fromisoformat(timestamp["iso"]).replace(fold=timestamp["fold"])
        except ValueError as exc:
            raise NodeRequestError("node datetime timestamp is invalid") from exc
        if value.isoformat() != timestamp["iso"]:
            raise NodeRequestError("node datetime timestamp is noncanonical")
        values["timestamp"] = value
    return values


class TypedNodeRequests:
    """Keep the Dora connection, turn iteration and acknowledgment authority in the host."""

    def __init__(self, node, *, outputs, max_calls=100000):
        from aisle.turn_node import Node

        if type(node) is not Node:
            raise NodeRequestError("typed requests require the trusted turn wrapper")
        if type(outputs) not in (set, frozenset) or any(
            type(name) is not str or not name or name == "turn_done" for name in outputs
        ):
            raise NodeRequestError("policy outputs are invalid or contain reserved acknowledgments")
        if type(max_calls) is not int or max_calls <= 0:
            raise NodeRequestError("node request budget must be a positive integer")
        self.node, self.events = node, iter(node)
        self.outputs = frozenset(outputs)
        self.max_calls, self.next_id = max_calls, 1
        self.closed = False

    def handle(self, request):
        """Dispatch exact requests; any refusal terminates this worker's capabilities."""
        if self.closed:
            raise NodeRequestError("node request boundary is closed")
        try:
            if (
                type(request) is not dict
                or type(request.get("id")) is not int
                or request["id"] != self.next_id
                or self.next_id > self.max_calls
            ):
                raise NodeRequestError("node request sequence or budget differs")
            self.next_id += 1
            op = request.get("op")
            if op == "next" and set(request) == {"id", "op"}:
                try:
                    event = dict(next(self.events))
                except StopIteration:
                    self.closed = True
                    result = {"end": True, "event": None}
                else:
                    if "value" in event and event["value"] is not None:
                        event["value"] = pack_array(event["value"])
                    if event.get("metadata") is not None:
                        event["metadata"] = pack_metadata(event["metadata"])
                    result = {"end": False, "event": event}
            elif op == "send" and set(request) == {"id", "op", "topic", "value", "metadata"}:
                if type(request["topic"]) is not str or request["topic"] not in self.outputs:
                    raise NodeRequestError("node output is undeclared or reserved")
                if type(request["metadata"]) is not dict:
                    raise NodeRequestError("node output metadata is invalid")
                self.node.send_output(
                    request["topic"],
                    unpack_array(request["value"]),
                    unpack_metadata(request["metadata"]),
                )
                result = None
            elif op == "stop" and set(request) == {"id", "op"}:
                self.node.stop_after_turn()
                result = None
            else:
                raise NodeRequestError("node operation or fields are undeclared")
            return {"id": request["id"], "result": result}
        except BaseException:
            self.closed = True
            raise


class RemoteNode:
    """Worker-side policy interface; exchange must use the admitted supervised channel."""

    def __init__(self, exchange):
        self.exchange, self.next_id = exchange, 1
        self.closed = False
        self.exhausted = False

    def _call(self, operation, **fields):
        if self.closed:
            raise NodeRequestError("remote node is closed")
        request_id = self.next_id
        self.next_id += 1
        try:
            reply = self.exchange({"id": request_id, "op": operation, **fields})
            if (
                type(reply) is not dict
                or set(reply) != {"id", "result"}
                or type(reply["id"]) is not int
                or reply["id"] != request_id
            ):
                raise NodeRequestError("node reply sequence or fields differ")
            return reply["result"]
        except BaseException:
            self.closed = True
            raise

    def __iter__(self):
        return self

    def __next__(self):
        if self.closed:
            if self.exhausted:
                raise StopIteration
            raise NodeRequestError("remote node failed")
        result = self._call("next")
        if (
            type(result) is not dict
            or set(result) != {"end", "event"}
            or type(result["end"]) is not bool
        ):
            self.closed = True
            raise NodeRequestError("node event reply is invalid")
        if result["end"]:
            self.closed = True
            if result["event"] is not None:
                raise NodeRequestError("node end reply contains an event")
            self.exhausted = True
            raise StopIteration
        if type(result["event"]) is not dict:
            self.closed = True
            raise NodeRequestError("node event is not a mapping")
        event = dict(result["event"])
        if "value" in event and event["value"] is not None:
            try:
                event["value"] = unpack_array(event["value"])
            except BaseException:
                self.closed = True
                raise
        if event.get("metadata") is not None:
            try:
                event["metadata"] = unpack_metadata(event["metadata"])
            except BaseException:
                self.closed = True
                raise
        return event

    def send_output(self, topic, value, metadata=None):
        if (
            self._call(
                "send",
                topic=topic,
                value=pack_array(value),
                metadata=pack_metadata(dict(metadata or {})),
            )
            is not None
        ):
            self.closed = True
            raise NodeRequestError("node send reply is invalid")

    def stop_after_turn(self):
        if self._call("stop") is not None:
            self.closed = True
            raise NodeRequestError("node stop reply is invalid")
