"""CON-4/MON-8/MON-12: bounded data-only transport across the worker boundary."""

import numpy as np
import pytest

pytestmark = pytest.mark.unit


def test_wire_preserves_observation_types():
    """CON-4/MON-12: numeric frames and nested observation values survive Arrow IPC."""
    from aisle.monolith.wire import decode, encode

    value = {
        "frame": np.arange(12, dtype=np.uint16).reshape(3, 4),
        "event": ("tick", 7, True, None, 0.125),
        "nested": [{"empty": []}, {}],
    }
    restored = decode(encode(value))
    np.testing.assert_array_equal(restored.pop("frame"), value.pop("frame"))
    assert restored == value
    assert type(restored["event"][1]) is int
    assert type(restored["event"][2]) is bool


@pytest.mark.parametrize("value", [object(), {1: "bad key"}, np.array([object()])])
def test_wire_refuses_non_data_values(value):
    """MON-8: transport never serializes arbitrary Python objects."""
    from aisle.monolith.wire import WireError, encode

    with pytest.raises(WireError):
        encode(value)


def test_wire_rejects_trailing_bytes_and_resource_overruns():
    """MON-8/MON-12: one bounded message cannot hide trailing data or oversized trees."""
    from aisle.monolith.wire import WireError, decode, encode

    payload = encode({"ok": True})
    with pytest.raises(WireError):
        decode(payload + b"unconsumed")
    with pytest.raises(WireError):
        decode(payload, max_bytes=16)
    with pytest.raises(WireError):
        encode(list(range(20)), max_nodes=10)
    with pytest.raises(WireError):
        decode(encode(list(range(20))), max_nodes=10)
    with pytest.raises(WireError):
        encode(np.ones(1000), max_bytes=100)


def test_wire_rejects_cycles_and_excessive_depth():
    """MON-8: recursive or deeply nested values fail without exhausting the worker."""
    from aisle.monolith.wire import WireError, decode, encode

    cycle = []
    cycle.append(cycle)
    with pytest.raises(WireError):
        encode(cycle)
    nested = [[[[None]]]]
    with pytest.raises(WireError):
        encode(nested, max_depth=2)
    with pytest.raises(WireError):
        decode(encode(nested), max_depth=2)


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_key",
        "scalar_parent",
        "forward_parent",
        "wrong_field",
        "wrong_shape",
        "object_dtype",
    ],
)
def test_wire_rejects_malformed_peer_trees(mutation):
    """MON-8: an untrusted peer cannot invent object types or ambiguous tree structure."""
    import pyarrow as pa

    from aisle.monolith.wire import WireError, decode, encode

    reader = pa.ipc.open_stream(encode({"first": 1, "second": np.ones(2)}))
    batch = reader.read_next_batch()
    rows = batch.to_pylist()
    if mutation == "duplicate_key":
        rows[2]["key"] = "first"
    elif mutation == "scalar_parent":
        rows[2]["parent"] = 1
    elif mutation == "forward_parent":
        rows[1]["parent"] = 2
    elif mutation == "wrong_field":
        rows[1]["text"] = "hidden value"
    elif mutation == "wrong_shape":
        rows[2]["shape"] = [2**62]
    else:
        rows[2]["dtype"] = "O"
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, batch.schema) as writer:
        writer.write_batch(pa.RecordBatch.from_pylist(rows, schema=batch.schema))
    with pytest.raises(WireError):
        decode(sink.getvalue().to_pybytes())


@pytest.mark.parametrize("payload", [b"", b"not arrow", b"\xff\xff\xff\xff"])
def test_wire_normalizes_truncated_input_errors(payload):
    """MON-12: broken worker bytes produce a protocol error, never a partial value."""
    from aisle.monolith.wire import WireError, decode

    with pytest.raises(WireError):
        decode(payload)


def test_wire_framing_handles_short_reads_and_rejects_truncation():
    """MON-12: stream framing consumes exactly one message across short pipe reads."""
    import io

    from aisle.monolith.wire import WireError, receive, send

    stream = io.BytesIO()
    send(stream, {"sequence": 1})
    send(stream, {"sequence": 2})
    raw = stream.getvalue()

    class ShortReads(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 7))

    stream = ShortReads(raw)
    assert receive(stream) == {"sequence": 1}
    assert receive(stream) == {"sequence": 2}
    with pytest.raises(WireError):
        receive(stream)
    with pytest.raises(WireError):
        receive(io.BytesIO(raw[:20]))
    with pytest.raises(WireError):
        receive(io.BytesIO((2**63).to_bytes(8, "big")))


def test_wire_crosses_a_real_child_pipe():
    """CON-4/MON-12: a real child exchanges a numeric observation through framed Arrow."""
    import io
    import subprocess
    import sys

    from aisle.monolith.wire import receive, send

    request = io.BytesIO()
    send(request, {"sequence": 3, "frame": np.arange(6, dtype=np.float32).reshape(2, 3)})
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from aisle.monolith.wire import receive, send; "
            "value = receive(sys.stdin.buffer); "
            "send(sys.stdout.buffer, {'sequence': value['sequence'], 'frame': value['frame'] + 1})",
        ],
        input=request.getvalue(),
        capture_output=True,
        timeout=10,
        check=True,
    )
    result = receive(io.BytesIO(child.stdout))
    assert result["sequence"] == 3
    np.testing.assert_array_equal(result["frame"], np.arange(6, dtype=np.float32).reshape(2, 3) + 1)


def test_wire_preserves_ieee_observation_values():
    """CON-4/MON-4: transport preserves sensor values for the existing primitive validators."""
    from aisle.monolith.wire import decode, encode

    value = {
        "frame": np.array([np.nan, np.inf, -np.inf, -0.0], dtype=np.float32),
        "scalar": float("nan"),
    }
    result = decode(encode(value))
    assert np.isnan(result["scalar"])
    assert result["frame"].dtype == value["frame"].dtype
    assert result["frame"].tobytes() == value["frame"].tobytes()
