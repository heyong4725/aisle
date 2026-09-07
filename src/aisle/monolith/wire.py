"""Data-only Arrow values for the planned monolithic worker protocol.

No object reconstruction, import names, or executable serializers cross this
boundary. This codec is not itself a process sandbox or a complete RPC policy.
"""

from __future__ import annotations

import math

import numpy as np
import pyarrow as pa


class WireError(ValueError):
    """A worker message does not satisfy the bounded value protocol."""


SCHEMA = pa.schema(
    [
        ("parent", pa.int64()),
        ("key", pa.string()),
        ("kind", pa.string()),
        ("integer", pa.int64()),
        ("real", pa.float64()),
        ("boolean", pa.bool_()),
        ("text", pa.string()),
        ("data", pa.binary()),
        ("dtype", pa.string()),
        ("shape", pa.list_(pa.int64())),
    ],
    metadata={b"protocol": b"aisle.monolith.values.v1"},
)
MAX_BYTES = 16 * 1024 * 1024
MAX_NODES = 16384
MAX_DEPTH = 64


def _limits(max_bytes, max_nodes, max_depth):
    if any(type(n) is not int or n <= 0 for n in (max_bytes, max_nodes, max_depth)):
        raise WireError("wire limits must be positive integers")


def encode(value, *, max_bytes=MAX_BYTES, max_nodes=MAX_NODES, max_depth=MAX_DEPTH):
    """Encode a tree of built-in values and numeric arrays as one Arrow batch."""
    _limits(max_bytes, max_nodes, max_depth)
    rows = []
    active = set()
    payload_size = 0

    def visit(item, parent, key, depth):
        nonlocal payload_size
        if depth > max_depth or len(rows) >= max_nodes:
            raise WireError("wire tree limit exceeded")
        row = dict.fromkeys(SCHEMA.names)
        row.update(parent=parent, key=key)
        index = len(rows)
        rows.append(row)
        if item is None:
            row["kind"] = "null"
        elif type(item) is bool:
            row.update(kind="boolean", boolean=item)
        elif type(item) is int:
            if not -(2**63) <= item < 2**63:
                raise WireError("wire integer exceeds int64")
            row.update(kind="integer", integer=item)
        elif type(item) is float:
            row.update(kind="real", real=item)
        elif type(item) is str:
            payload_size += len(item.encode("utf-8"))
            row.update(kind="text", text=item)
        elif type(item) is np.ndarray:
            if item.dtype.kind not in "biuf" or item.dtype.itemsize not in (1, 2, 4, 8):
                raise WireError("wire array must have a supported numeric dtype")
            if item.ndim > 32 or item.nbytes > max_bytes - payload_size:
                raise WireError("wire array limit exceeded")
            payload_size += item.nbytes
            row.update(
                kind="array",
                data=item.tobytes(order="C"),
                dtype=item.dtype.str,
                shape=list(item.shape),
            )
        elif type(item) in (dict, list, tuple):
            if id(item) in active:
                raise WireError("wire tree contains a cycle")
            row["kind"] = {dict: "dict", list: "list", tuple: "tuple"}[type(item)]
            active.add(id(item))
            entries = item.items() if type(item) is dict else ((None, v) for v in item)
            for child_key, child in entries:
                if type(item) is dict:
                    if type(child_key) is not str:
                        raise WireError("wire dictionary keys must be strings")
                    payload_size += len(child_key.encode("utf-8"))
                visit(child, index, child_key, depth + 1)
            active.remove(id(item))
        else:
            raise WireError("unsupported wire value")
        if payload_size > max_bytes:
            raise WireError("wire payload limit exceeded")

    visit(value, -1, None, 0)
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, SCHEMA) as writer:
        writer.write_batch(pa.RecordBatch.from_pylist(rows, schema=SCHEMA))
    result = sink.getvalue().to_pybytes()
    if len(result) > max_bytes:
        raise WireError("wire message limit exceeded")
    return result


def decode(payload, *, max_bytes=MAX_BYTES, max_nodes=MAX_NODES, max_depth=MAX_DEPTH):
    """Decode one strictly typed tree, rejecting ambiguous or unused fields."""
    _limits(max_bytes, max_nodes, max_depth)
    if type(payload) is not bytes or len(payload) > max_bytes:
        raise WireError("wire message limit exceeded")
    try:
        source = pa.BufferReader(payload)
        with pa.ipc.open_stream(source) as reader:
            if not reader.schema.equals(SCHEMA, check_metadata=True):
                raise WireError("wire schema differs")
            batch = reader.read_next_batch()
            if not 0 < batch.num_rows <= max_nodes:
                raise WireError("wire node limit exceeded")
            if next(reader, None) is not None or source.tell() != len(payload):
                raise WireError("wire message has trailing data")
        rows = batch.to_pylist()
        children = [[] for _ in rows]
        depths = [0] * len(rows)
        fields = {
            "null": set(),
            "dict": set(),
            "list": set(),
            "tuple": set(),
            "integer": {"integer"},
            "real": {"real"},
            "boolean": {"boolean"},
            "text": {"text"},
            "array": {"data", "dtype", "shape"},
        }
        for index, row in enumerate(rows):
            kind = row["kind"]
            if kind not in fields:
                raise WireError("unknown wire kind")
            populated = {k for k, v in row.items() if v is not None} - {"parent", "key", "kind"}
            if populated != fields[kind]:
                raise WireError("wire fields disagree with kind")
            parent = row["parent"]
            if index == 0:
                if parent != -1 or row["key"] is not None:
                    raise WireError("invalid wire root")
                continue
            if type(parent) is not int or not 0 <= parent < index:
                raise WireError("invalid wire parent")
            parent_kind = rows[parent]["kind"]
            if parent_kind not in ("dict", "list", "tuple"):
                raise WireError("wire scalar has children")
            if (row["key"] is not None) != (parent_kind == "dict"):
                raise WireError("invalid wire child key")
            depths[index] = depths[parent] + 1
            if depths[index] > max_depth:
                raise WireError("wire depth limit exceeded")
            children[parent].append(index)

        def restore(index):
            row = rows[index]
            kind = row["kind"]
            if kind == "null":
                return None
            if kind == "dict":
                result = {}
                for child in children[index]:
                    key = rows[child]["key"]
                    if key in result:
                        raise WireError("duplicate wire dictionary key")
                    result[key] = restore(child)
                return result
            if kind in ("list", "tuple"):
                result = [restore(child) for child in children[index]]
                return tuple(result) if kind == "tuple" else result
            if kind == "array":
                dtype = np.dtype(row["dtype"])
                shape = row["shape"]
                if dtype.kind not in "biuf" or dtype.itemsize not in (1, 2, 4, 8):
                    raise WireError("unsupported wire dtype")
                if len(shape) > 32 or any(type(n) is not int or n < 0 for n in shape):
                    raise WireError("invalid wire shape")
                if math.prod(shape) * dtype.itemsize != len(row["data"]):
                    raise WireError("wire shape disagrees with array bytes")
                result = np.frombuffer(row["data"], dtype=dtype).reshape(shape).copy()
                return result
            result = row[kind]
            return result

        return restore(0)
    except WireError:
        raise
    except (pa.ArrowException, ValueError, TypeError, StopIteration, OverflowError) as exc:
        raise WireError("invalid Arrow wire message") from exc


def send(stream, value, *, max_bytes=MAX_BYTES, max_nodes=MAX_NODES, max_depth=MAX_DEPTH):
    """Write one length-prefixed Arrow value to a dedicated binary pipe."""
    payload = encode(value, max_bytes=max_bytes, max_nodes=max_nodes, max_depth=max_depth)
    pending = memoryview(len(payload).to_bytes(8, "big") + payload)
    while pending:
        written = stream.write(pending)
        if type(written) is not int or not 0 < written <= len(pending):
            raise WireError("worker pipe did not accept message bytes")
        pending = pending[written:]
    stream.flush()


def receive(stream, *, max_bytes=MAX_BYTES, max_nodes=MAX_NODES, max_depth=MAX_DEPTH):
    """Read exactly one bounded message; the process supervisor owns deadlines."""
    _limits(max_bytes, max_nodes, max_depth)

    def exact(size):
        chunks = bytearray()
        while len(chunks) < size:
            chunk = stream.read(size - len(chunks))
            if not isinstance(chunk, bytes) or not chunk or len(chunk) > size - len(chunks):
                raise WireError("worker pipe ended inside a message")
            chunks.extend(chunk)
        return bytes(chunks)

    size = int.from_bytes(exact(8), "big")
    if not 0 < size <= max_bytes:
        raise WireError("worker frame exceeds message limit")
    return decode(exact(size), max_bytes=max_bytes, max_nodes=max_nodes, max_depth=max_depth)
