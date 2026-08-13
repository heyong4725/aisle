"""Drop-in participant Node wrapper tests."""

from __future__ import annotations

import pyarrow as pa
import pytest

from aisle.turn_node import Node
from aisle.turns import ProtocolError

pytestmark = pytest.mark.unit


class Raw:
    def __init__(self, events):
        self.events = events
        self.sent = []

    def __iter__(self):
        return iter(self.events)

    def send_output(self, topic, value, metadata=None):
        self.sent.append((topic, value, metadata or {}))


def test_drop_in_node_closes_one_complete_turn():
    """BRG-1/CAP-1: unmodified policy loops receive one canonical turn batch."""
    stamp = {"turn_epoch": 1, "turn_id": 0, "sim_time_ns": 0, "seq": 0}
    raw = Raw(
        [
            {"type": "INPUT", "id": "input", "value": pa.array([1]), "metadata": stamp},
            {
                "type": "INPUT",
                "id": "turn",
                "value": pa.array([0], type=pa.uint64()),
                "metadata": {
                    **stamp,
                    "target_node": "worker",
                    "expected_inputs": ["input"],
                    "expected_counts": [1],
                },
            },
        ]
    )
    node = Node(
        raw,
        {
            "AISLE_LOCKSTEP": "1",
            "AISLE_TURN_NODE": "worker",
            "AISLE_TURN_OUTPUTS": "result,turn_done",
        },
    )
    assert [event["id"] for event in node] == ["input", "turn"]
    done = raw.sent[-1]
    assert done[0] == "turn_done"
    assert done[2]["source_node"] == "worker"


def test_direct_output_is_stamped_and_counted():
    """TC-2: direct node.send_output users obey the same turn contract."""
    stamp = {"turn_epoch": 2, "turn_id": 3, "sim_time_ns": 30, "seq": 0}
    raw = Raw(
        [
            {
                "type": "INPUT",
                "id": "turn",
                "value": pa.array([3], type=pa.uint64()),
                "metadata": {
                    **stamp,
                    "target_node": "worker",
                    "expected_inputs": [],
                    "expected_counts": [],
                },
            }
        ]
    )
    node = Node(
        raw,
        {
            "AISLE_LOCKSTEP": "1",
            "AISLE_TURN_NODE": "worker",
            "AISLE_TURN_OUTPUTS": "result,turn_done",
        },
    )
    for event in node:
        if event["id"] == "turn":
            node.send_output("result", pa.array([1]), {"seq": 1})
    result = raw.sent[0]
    assert result[2]["turn_epoch"] == 2 and result[2]["turn_id"] == 3
    assert raw.sent[-1][2]["emitted_counts"] == [1, 1]


def test_legacy_topic_sender_default_sim_stamp_is_replaced_by_open_turn():
    """TC-2: make_sender's legacy sim_time=0 default must not reject valid work."""
    from aisle.topics import make_sender

    stamp = {"turn_epoch": 2, "turn_id": 3, "sim_time_ns": 30, "seq": 0}
    raw = Raw(
        [
            {
                "type": "INPUT",
                "id": "turn",
                "value": pa.array([3], type=pa.uint64()),
                "metadata": {
                    **stamp,
                    "target_node": "worker",
                    "expected_inputs": [],
                    "expected_counts": [],
                },
            }
        ]
    )
    node = Node(
        raw,
        {
            "AISLE_LOCKSTEP": "1",
            "AISLE_TURN_NODE": "worker",
            "AISLE_TURN_OUTPUTS": "result,turn_done",
        },
    )
    send = make_sender(node)
    for event in node:
        if event["id"] == "turn":
            send("result", pa.array([1]), {})
    assert raw.sent[0][2]["sim_time_ns"] == 30


def test_unstamped_wall_input_cannot_emit_functional_output():
    """BRG-1: wall callbacks abort rather than inject an out-of-turn command."""
    raw = Raw(
        [{"type": "INPUT", "id": "tick", "value": pa.array([], type=pa.uint8()), "metadata": {}}]
    )
    node = Node(
        raw,
        {
            "AISLE_LOCKSTEP": "1",
            "AISLE_TURN_NODE": "guard",
            "AISLE_TURN_OUTPUTS": "command,turn_done",
        },
    )
    with pytest.raises(ProtocolError):
        for _ in node:
            node.send_output("command", pa.array([0.0]), {})


def test_unstamped_non_wall_input_fails_before_policy_can_consume_it():
    """TC-2: missing turn fields are malformed data, not a bootstrap event."""
    raw = Raw([{"type": "INPUT", "id": "state", "value": pa.array([1]), "metadata": {"seq": 1}}])
    node = Node(
        raw,
        {
            "AISLE_LOCKSTEP": "1",
            "AISLE_TURN_NODE": "worker",
            "AISLE_TURN_OUTPUTS": "command,turn_done",
        },
    )
    with pytest.raises(ProtocolError, match="unstamped"):
        list(node)


def test_stop_after_turn_emits_watermark_before_iterator_ends():
    """BRG-1: a finite participant cannot exit while its turn remains open."""
    stamp = {"turn_epoch": 1, "turn_id": 4, "sim_time_ns": 40, "seq": 0}
    raw = Raw(
        [
            {
                "type": "INPUT",
                "id": "turn",
                "value": pa.array([4], type=pa.uint64()),
                "metadata": {
                    **stamp,
                    "target_node": "client",
                    "expected_inputs": [],
                    "expected_counts": [],
                },
            }
        ]
    )
    node = Node(
        raw,
        {
            "AISLE_LOCKSTEP": "1",
            "AISLE_TURN_NODE": "client",
            "AISLE_TURN_OUTPUTS": "reset,turn_done",
        },
    )
    seen = []
    for event in node:
        seen.append(event["id"])
        node.stop_after_turn()
    assert seen == ["turn"]
    assert raw.sent[-1][0] == "turn_done"
    assert raw.sent[-1][2]["shutdown"] is True
