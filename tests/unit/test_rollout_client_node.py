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
        self.stopped_after_turn = False

    def stop_after_turn(self) -> None:
        # `turn_node.Node`'s lockstep-safe exit: it closes the open turn and
        # emits turn_done before iteration ends. A bare `break` skips that.
        self.stopped_after_turn = True

    def __iter__(self):
        return iter(self._events)

    def send_output(self, topic, value, metadata=None):
        self.sent.append((topic, list(value.to_pylist()), metadata or {}))


def _inp(topic, value, metadata=None):
    return {"type": "INPUT", "id": topic, "value": value, "metadata": metadata or {}}


def tick():
    return _inp("tick", pa.array(np.zeros(1, dtype=np.uint8)))


def run_client(events, monkeypatch, tmp_path, *, lockstep=False, lifecycle=None):
    node = FakeNode(events)
    fake_dora = types.ModuleType("dora")
    fake_dora.Node = lambda: node
    monkeypatch.setitem(sys.modules, "dora", fake_dora)
    # substitute the lockstep wrapper itself: this file tests the CLIENT's
    # decisions (does it advance? does it close its turn?), not turn_node's
    # protocol, and the real wrapper refuses to construct without a full
    # AISLE_TURN_* participant config plus barrier-stamped events.
    import aisle.turn_node as turn_node_mod

    monkeypatch.setattr(turn_node_mod, "Node", lambda: node)
    monkeypatch.setenv("AISLE_SEEDS", "0")
    monkeypatch.setenv("AISLE_TIER", "T0")
    monkeypatch.setenv("AISLE_RESULTS", str(tmp_path / "episodes.jsonl"))
    monkeypatch.setenv("AISLE_LOCKSTEP", "1" if lockstep else "0")
    if lifecycle is not None:
        monkeypatch.setenv("AISLE_EPISODE_LIFECYCLE", lifecycle)
        monkeypatch.setenv("AISLE_TIER", "T1")
        monkeypatch.setenv("AISLE_TIMEOUT_S", "30")

    from aisle.harness.rollout_client import main

    main()
    return node


@pytest.mark.parametrize(
    "result_stamp,status", [(1_000_000_000, "success"), (20_000_000_000, "timeout")]
)
def test_pilot_result_cannot_end_attempt_or_publish_record_before_deadline(
    monkeypatch, tmp_path, result_stamp, status
):
    """BND-3/MON-4: verdict timing cannot advance the graph or completion-file reader."""

    def turn(stamp):
        return _inp("turn", pa.array([0]), {"sim_time_ns": stamp})

    def events():
        yield turn(0)
        yield _inp("reset_done", pa.array([1]), {"sim_time_ns": 0})
        yield _inp(
            "episode_result",
            pa.array([json.dumps({"goal_id": "ep-0000", "status": status})]),
            {"sim_time_ns": result_stamp},
        )
        yield turn(29_999_999_999)
        assert (tmp_path / "episodes.jsonl").read_text() == ""
        yield turn(30_000_000_000)
        record = json.loads((tmp_path / "episodes.jsonl").read_text())
        assert record["status"] == status

    node = run_client(
        events(), monkeypatch, tmp_path, lockstep=True, lifecycle="fixed-horizon-t1-v1"
    )
    assert node.stopped_after_turn
    assert len(goals(node)) == 1


def test_unknown_episode_lifecycle_is_refused(monkeypatch, tmp_path):
    """BND-3: a mistyped lifecycle cannot silently use oracle-triggered resets."""
    with pytest.raises(SystemExit, match="lifecycle"):
        run_client([], monkeypatch, tmp_path, lockstep=True, lifecycle="fixed-horizon-typo")


def test_episode_lifecycle_must_be_graph_declared():
    """BND-3/CON-5: an ambient shell cannot change the attested episode lifecycle."""
    from aisle.harness.rollout import scrub_bringup_env

    assert scrub_bringup_env(
        {"AISLE_EPISODE_LIFECYCLE": "fixed-horizon-t1-v1", "PATH": "/bin"}
    ) == {"PATH": "/bin"}


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


def turn():
    return _inp("turn", pa.array(np.zeros(1, dtype=np.uint64)))


def test_a_refusal_ends_the_run_without_stranding_the_turn_barrier(monkeypatch, tmp_path):
    """ADR-30 + ADR-34 (cross-review of #223): ending the run must not become
    HANGING the run.

    Under lockstep this node is a turn participant. A bare `break` from
    inside the yielded turn raises GeneratorExit at the yield, so
    `turn_node` never emits `turn_done` and the terminal barrier blocks
    every other node until the ADR-23 wall clamp. The client's normal
    termination already knew this and calls `stop_after_turn()`; the refusal
    path added in #208 did not, and every shipped graph runs this node in
    lockstep.

    Latent until #206 made a refusal reachable from the shipped client — the
    reason ADR-34 gave for the route being safe was that nothing could
    produce one, and a cold model cache now can."""
    refused = run_client(
        [
            turn(),
            _inp(
                "reset_refused",
                pa.array(np.array([0], dtype=np.uint32)),
                {"request_id": "r", "error": "behavioral runtime unavailable: no model cache"},
            ),
        ],
        monkeypatch,
        tmp_path,
        lockstep=True,
    )
    assert goals(refused) == [], "the client started an episode on an un-reset scene"
    assert refused.stopped_after_turn, (
        "the client broke out of an open turn instead of closing it — turn_done never "
        "leaves and the terminal barrier hangs every other node (ADR-30)"
    )
