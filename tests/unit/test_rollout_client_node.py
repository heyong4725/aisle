"""Node-level tests for the rollout client's reset handshake (TC-6, ADR-34).

The client is the ONLY node that hears a refusal (issue #195): the boundary
is broadcast state, a refusal is a reply to one requester. That makes the
client the single place a refused reset can strand a run — it sits in
`awaiting_reset` until something answers, and after ADR-34 the answer for a
refused request arrives on a different topic than the one it used to.

dora is faked (CON-12 keeps the import inside `main()`); no sim, no models.
"""

import sys
import types

import numpy as np
import pyarrow as pa
import pytest

pytestmark = pytest.mark.unit

TELEPORT = 0


class FakeNode:
    def __init__(self, events):
        self._events = events
        self.sent: list[tuple[str, list, dict]] = []

    def __iter__(self):
        return iter(self._events)

    def send_output(self, topic, value, metadata=None):
        self.sent.append((topic, list(value.to_pylist()), metadata or {}))


def _inp(topic, value, metadata=None):
    return {"type": "INPUT", "id": topic, "value": value, "metadata": metadata or {}}


def tick():
    return _inp("tick", pa.array(np.zeros(1, dtype=np.uint8)))


def run_client(events, monkeypatch, tmp_path):
    node = FakeNode(events)
    fake_dora = types.ModuleType("dora")
    fake_dora.Node = lambda: node
    monkeypatch.setitem(sys.modules, "dora", fake_dora)
    monkeypatch.setenv("AISLE_SEEDS", "0")
    monkeypatch.setenv("AISLE_TIER", "T0")
    monkeypatch.setenv("AISLE_RESULTS", str(tmp_path / "episodes.jsonl"))

    from aisle.harness.rollout_client import main

    main()
    return node


def goals(node) -> list:
    return [v for topic, v, _ in node.sent if topic == "episode_goal"]


def test_the_handshake_advances_on_each_reply_topic(monkeypatch, tmp_path):
    """TC-6/ADR-34 (issue #195): the client waits in `awaiting_reset` until
    its request is ANSWERED, and after the refusal split there are two
    topics an answer can arrive on. If the client listens to only one, a
    refused reset strands the run — it waits forever on a reply that was
    published to a topic nobody reads, which is strictly worse than the
    silent-degradation this split was made to prevent.

    Both arms assert the same observable (an episode_goal was sent), and the
    no-reply arm is what makes that observable meaningful."""
    # the `episode_feedback` is deliberate: it is dispatched AFTER the reply
    # branch, so a client that advanced on any event while awaiting a reset
    # would answer it with a goal. Without it the control only proves ticks
    # do not advance the handshake, which is a weaker claim than it reads as.
    stranded = run_client(
        [tick(), _inp("episode_feedback", pa.array(['{"t": 0.1, "phase": "x"}']))],
        monkeypatch,
        tmp_path,
    )
    assert goals(stranded) == [], "the client sent a goal without any reply to its reset"

    done = run_client(
        [tick(), _inp("reset_done", pa.array(np.array([1], dtype=np.uint32)), {"sim_time_ns": 7})],
        monkeypatch,
        tmp_path,
    )
    assert len(goals(done)) == 1, "a completed reset did not start the episode"

    refused = run_client(
        [
            tick(),
            _inp(
                "reset_refused",
                pa.array(np.array([0], dtype=np.uint32)),
                {"request_id": "r", "error": "reset mode must be 0 or 1, got 9", "t_reset_ms": 0},
            ),
        ],
        monkeypatch,
        tmp_path,
    )
    assert len(goals(refused)) == 1, (
        "a refused reset left the client in awaiting_reset — the run hangs on a reply "
        "published to a topic it does not read (ADR-34)"
    )
