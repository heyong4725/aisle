"""Topic-contract helpers shared by every AISLE node (SPEC 010 TC-2)."""

from __future__ import annotations


def stamp(metadata: dict, seq: int) -> dict:
    """TC-2 mandatory output keys on every node output: defaults for
    sim_time_ns/env_id when the upstream message carries none, upstream
    values preserved when it does, and the sender's OWN per-topic
    monotonic seq."""
    return {"sim_time_ns": 0, "env_id": 0, **metadata, "seq": seq}


def make_sender(node):
    """TC-2 sender: per-topic monotonic seq + stamp around node.send_output.
    Every AISLE node's send path in one place (six copies before this)."""
    seq: dict[str, int] = {}

    def send(topic: str, value, metadata: dict) -> None:
        seq[topic] = seq.get(topic, 0) + 1
        node.send_output(topic, value, stamp(metadata, seq[topic]))

    return send
