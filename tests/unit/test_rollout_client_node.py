"""Node-level tests for the rollout client's reset handshake (TC-6, ADR-34).

The client is the ONLY node that hears a refusal (issue #195): the boundary
is broadcast state, a refusal is a reply to one requester. That makes the
client the single place a refused reset can strand a run — it sits in
`awaiting_reset` until something answers, and after ADR-34 the answer for a
refused request arrives on a different topic than the one it used to.

dora is faked (CON-12 keeps the import inside `main()`); no sim, no models.
"""

import json
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


def test_a_completed_reset_starts_the_episode_on_the_reset_stamp(monkeypatch, tmp_path):
    """TC-7/BRG-4: the goal carries `reset_sim_ns` from the reply, and the
    verifier captures the episode's initial poses only at or after it — a
    pre-reset frame becoming the baseline reads as a mass collision (issue
    #120). Asserting the VALUE, not just that a goal was sent: the field is
    the whole reason the reply's stamp is threaded through here."""
    stranded = run_client(
        # `episode_feedback` is deliberate: it dispatches AFTER the reply
        # branch, so a client that advanced on any event while awaiting a
        # reset would answer it with a goal. A tick cannot reach that branch,
        # so a tick-only control proves less than it appears to.
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
    assert json.loads(done.sent[-1][1][0])["reset_sim_ns"] == 7, done.sent[-1]


def test_a_refused_reset_ends_the_run_instead_of_advancing(monkeypatch, tmp_path):
    """ADR-34 / issue #209: the client must NOT start an episode on a
    refusal.

    While refusals rode the boundary topic, every episode-state consumer
    received them and cleared in step with this node — the comment deleted
    from `budget_guard.py` recorded that dependency explicitly. Once
    refusals reach only the requester, advancing here puts this node in
    episode N+1 while `ik-trajectory` still holds a stale plan, `s1-expert`
    drops the new plan as a duplicate, and nav carries a leg across the
    boundary: issue #179's class, arrived at by policy rather than wiring.

    It is also the honest answer on its own: the scene was never reset, so
    the episode would measure nothing. Ending the loop is the client's
    normal termination — completed episodes are already flushed."""
    refused = run_client(
        [
            tick(),
            _inp(
                "reset_refused",
                pa.array(np.array([0], dtype=np.uint32)),
                {"request_id": "r", "error": "reset mode must be 0 or 1, got 9", "t_reset_ms": 0},
            ),
            # would be consumed if the client kept running
            _inp("reset_done", pa.array(np.array([1], dtype=np.uint32)), {"sim_time_ns": 9}),
        ],
        monkeypatch,
        tmp_path,
    )
    assert goals(refused) == [], (
        "the client started an episode on a scene that was never reset, leaving every "
        "other boundary consumer an episode behind (ADR-34, issue #209)"
    )
