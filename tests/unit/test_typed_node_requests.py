"""MON-6/MON-13: authored typed nodes cannot own the turn scheduler's capabilities."""

from datetime import UTC

import numpy as np
import pyarrow as pa
import pytest
from test_turn_node import Raw

from aisle.turn_node import Node

pytestmark = pytest.mark.unit


def _node():
    raw = Raw(
        [
            {
                "type": "INPUT",
                "id": "turn",
                "value": pa.array([3], type=pa.uint64()),
                "metadata": {
                    "turn_epoch": 2,
                    "turn_id": 3,
                    "sim_time_ns": 30,
                    "target_node": "worker",
                    "expected_inputs": [],
                    "expected_counts": [],
                },
            }
        ]
    )
    return raw, Node(
        raw,
        {
            "AISLE_LOCKSTEP": "1",
            "AISLE_TURN_NODE": "worker",
            "AISLE_TURN_OUTPUTS": "result,turn_done",
        },
    )


def test_remote_policy_output_is_stamped_and_acknowledged_only_by_host():
    """MON-6: trusted turn accounting survives an ordinary remote policy loop."""
    from aisle.harness.typed_node_requests import RemoteNode, TypedNodeRequests
    from aisle.monolith.wire import decode, encode

    raw, node = _node()
    host = TypedNodeRequests(node, outputs={"result"})
    remote = RemoteNode(lambda request: decode(encode(host.handle(decode(encode(request))))))
    for event in remote:
        assert event["id"] == "turn"
        assert event["value"].type == pa.uint64()
        remote.send_output("result", pa.array([1.25], type=pa.float32()), {"seq": 1})
        assert [row[0] for row in raw.sent] == ["result"]
    assert [row[0] for row in raw.sent] == ["result", "turn_done"]
    assert raw.sent[0][2]["turn_id"] == 3
    assert raw.sent[1][2]["emitted_counts"] == [1, 1]


@pytest.mark.parametrize("topic", ["turn_done", "undeclared"])
def test_reserved_or_undeclared_output_terminates_requests(topic):
    """MON-6/MON-13: rejected output requests cannot resume and manufacture completion."""
    from aisle.harness.typed_node_requests import NodeRequestError, RemoteNode, TypedNodeRequests

    raw, node = _node()
    host = TypedNodeRequests(node, outputs={"result"})
    remote = RemoteNode(host.handle)
    next(iter(remote))
    with pytest.raises(NodeRequestError):
        remote.send_output(topic, pa.array([1]), {})
    with pytest.raises(NodeRequestError):
        next(iter(remote))
    assert not raw.sent


def test_payload_roundtrip_preserves_dtype_nulls_and_large_unsigned_values():
    """MON-2/CON-3: the relay does not narrow payload dtype, validity or uint64 range."""
    from aisle.harness.typed_node_requests import pack_array, unpack_array
    from aisle.monolith.wire import decode, encode

    for array in (
        pa.array([2**64 - 1, None, 0], type=pa.uint64()),
        pa.array([1.25, None, -0.0], type=pa.float32()),
        pa.array([True, None, False]),
        pa.array(["hello", None, ""], type=pa.large_string()),
    ):
        restored = unpack_array(decode(encode(pack_array(array))))
        assert restored.equals(array)
        assert restored.type == array.type
    floating = pa.array([float("nan"), float("inf"), -0.0])
    restored = unpack_array(decode(encode(pack_array(floating)))).to_numpy()
    assert np.isnan(restored[0]) and np.isposinf(restored[1]) and np.signbit(restored[2])


@pytest.mark.parametrize(
    "message",
    [
        {"id": True, "op": "next"},
        {"id": 2, "op": "next"},
        {"id": 1, "op": "next", "extra": 1},
        {"id": 1, "op": "getattr", "name": "raw"},
    ],
)
def test_malformed_or_reflective_requests_cannot_advance_the_host(message):
    """MON-6/MON-13: request parsing grants no reflective access or sequence repair."""
    from aisle.harness.typed_node_requests import NodeRequestError, TypedNodeRequests

    raw, node = _node()
    host = TypedNodeRequests(node, outputs={"result"})
    with pytest.raises(NodeRequestError):
        host.handle(message)
    with pytest.raises(NodeRequestError):
        host.handle({"id": 1, "op": "next"})
    assert not raw.sent


def test_remote_stop_retains_trusted_shutdown_acknowledgment():
    """MON-6: the worker requests shutdown; only the trusted wrapper emits its watermark."""
    from aisle.harness.typed_node_requests import RemoteNode, TypedNodeRequests

    raw, node = _node()
    remote = RemoteNode(TypedNodeRequests(node, outputs={"result"}).handle)
    next(remote)
    remote.stop_after_turn()
    assert not raw.sent
    with pytest.raises(StopIteration):
        next(remote)
    assert raw.sent[-1][0] == "turn_done"
    assert raw.sent[-1][2]["shutdown"] is True


def test_real_child_uses_only_remote_node_while_host_keeps_turn_accounting():
    """MON-6/MON-12: real pipes carry policy events and outputs without sharing the Dora node.

    The fixed child fixture establishes transport integration, not OS confinement.
    """
    import subprocess
    import sys
    import time
    from pathlib import Path

    from aisle.harness.typed_node_requests import TypedNodeRequests
    from aisle.monolith.supervisor import _DeadlinePipe
    from aisle.monolith.wire import receive, send

    source = """
import sys
import pyarrow as pa
from aisle.harness.typed_node_requests import RemoteNode
from aisle.monolith.wire import send, receive
from aisle.topics import make_sender

def exchange(request):
    send(sys.stdout.buffer, request)
    return receive(sys.stdin.buffer)

node = RemoteNode(exchange)
sender = make_sender(node)
for event in node:
    sender("result", pa.array([7], type=pa.int16()), event["metadata"])
assert "aisle.turn_node" not in sys.modules
assert "dora" not in sys.modules
"""
    root = Path(__file__).resolve().parents[2] / "src"
    command = [
        sys.executable,
        "-I",
        "-B",
        "-c",
        "import sys; sys.path.insert(0, sys.argv[1]); exec(sys.argv[2])",
        str(root),
        source,
    ]
    raw, node = _node()
    host = TypedNodeRequests(node, outputs={"result"})
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        try:
            deadline = time.monotonic() + 10
            incoming = _DeadlinePipe(process.stdout, deadline, time.monotonic)
            outgoing = _DeadlinePipe(process.stdin, deadline, time.monotonic)
            while not host.closed:
                send(outgoing, host.handle(receive(incoming)))
            assert process.wait(timeout=10) == 0, process.stderr.read().decode()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert [row[0] for row in raw.sent] == ["result", "turn_done"]
    assert raw.sent[0][1].type == pa.int16()
    assert raw.sent[1][2]["emitted_counts"] == [1, 1]


@pytest.mark.parametrize("change", ["type", "dtype", "shape", "nulls"])
def test_untrusted_payload_declaration_cannot_select_types_or_mismatched_buffers(change):
    """MON-6/MON-13: decode constructs only closed primitive types with matching validity."""
    from aisle.harness.typed_node_requests import NodeRequestError, pack_array, unpack_array

    payload = pack_array(pa.array([1, None], type=pa.int16()))
    if change == "type":
        payload["type"] = "extension:participant.Serializer"
    elif change == "dtype":
        payload["values"] = payload["values"].astype(object)
    elif change == "shape":
        payload["values"] = payload["values"].reshape(1, 2)
    else:
        payload["nulls"] = np.array([False], dtype=bool)
    with pytest.raises(NodeRequestError):
        unpack_array(payload)


def test_node_request_budget_exhaustion_cannot_complete_an_unfinished_turn():
    """MON-8/MON-13: a finite request budget refuses further effects before acknowledgment."""
    from aisle.harness.typed_node_requests import NodeRequestError, RemoteNode, TypedNodeRequests

    raw, node = _node()
    host = TypedNodeRequests(node, outputs={"result"}, max_calls=1)
    remote = RemoteNode(host.handle)
    next(remote)
    with pytest.raises(NodeRequestError, match="budget"):
        remote.send_output("result", pa.array([1]), {})
    assert host.closed
    assert not raw.sent


def test_real_event_timestamp_crosses_rpc_without_changing_its_type():
    """MON-4/MON-12: Dora's datetime metadata survives incoming and forwarded events."""
    from datetime import datetime

    from aisle.harness.typed_node_requests import RemoteNode, TypedNodeRequests
    from aisle.monolith.wire import decode, encode

    stamp = datetime(2026, 9, 7, 12, 34, 56, 123456, tzinfo=UTC)
    metadata = {"timestamp": stamp, "seq": 1, "semantic": "fixture"}
    raw = Raw(
        [{"type": "INPUT", "id": "observation", "value": pa.array([1.25]), "metadata": metadata}]
    )
    host = TypedNodeRequests(Node(raw, {}), outputs={"result"})
    remote = RemoteNode(lambda request: decode(encode(host.handle(decode(encode(request))))))
    event = next(remote)
    assert event["metadata"] == metadata
    assert type(event["metadata"]["timestamp"]) is datetime
    remote.send_output("result", event["value"], event["metadata"])
    assert raw.sent[0][2] == metadata
    assert metadata["timestamp"] is stamp


@pytest.mark.parametrize("timestamp", ["ordinary text", {"iso": "not a datetime", "fold": 1}, None])
def test_timestamp_data_does_not_collide_with_datetime_encoding(timestamp):
    """MON-4: data-valued timestamp fields are never interpreted as executable type tags."""
    from aisle.harness.typed_node_requests import pack_metadata, unpack_metadata
    from aisle.monolith.wire import decode, encode

    metadata = {"timestamp": timestamp, "datetime_timestamp": "ordinary metadata"}
    assert unpack_metadata(decode(encode(pack_metadata(metadata)))) == metadata


def test_datetime_metadata_preserves_offset_microseconds_and_fold():
    """MON-4: explicit encoding preserves timestamp value and built-in datetime semantics."""
    from datetime import datetime, timedelta, timezone

    from aisle.harness.typed_node_requests import pack_metadata, unpack_metadata
    from aisle.monolith.wire import decode, encode

    for zone in (None, timezone(timedelta(hours=5, minutes=30))):
        value = datetime(2026, 9, 7, 12, 34, 56, 123456, tzinfo=zone, fold=1)
        result = unpack_metadata(decode(encode(pack_metadata({"timestamp": value}))))["timestamp"]
        assert type(result) is datetime
        assert result == value and result.fold == value.fold
        assert result.isoformat() == value.isoformat()


@pytest.mark.parametrize("fault", ["conflict", "date_only", "invalid", "fold", "extra"])
def test_malformed_datetime_metadata_is_refused(fault):
    """MON-13: ambiguous, noncanonical or malformed peer timestamp records fail closed."""
    from aisle.harness.typed_node_requests import NodeRequestError, unpack_metadata

    record = {"values": {}, "datetime_timestamp": {"iso": "2026-09-07T12:34:56+00:00", "fold": 0}}
    if fault == "conflict":
        record["values"]["timestamp"] = "second timestamp"
    elif fault == "date_only":
        record["datetime_timestamp"]["iso"] = "2026-09-07"
    elif fault == "invalid":
        record["datetime_timestamp"]["iso"] = "invalid"
    elif fault == "fold":
        record["datetime_timestamp"]["fold"] = True
    else:
        record["datetime_timestamp"]["type"] = "peer.module.Class"
    with pytest.raises(NodeRequestError):
        unpack_metadata(record)
