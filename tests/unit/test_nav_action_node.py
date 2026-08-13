"""Node-level tests for the nav action's dora wiring (SPEC 210 MOB-2).

The pure lifecycle lives in `aisle.mobility.nav` and is tested in
test_mobility.py. This file tests the WIRING in `nodes/nav_action.py`: which
events the node dispatches on and what it emits. dora is faked (CON-12
keeps the import inside `main()`, which is exactly what makes this
possible), so there is no daemon, no sim, and no wall-clock dependence.

Why this file exists (issue #179 review): the #179 fix had three separately
tested parts — the pure `on_episode_boundary`, the graph YAML wiring, and
the registry manifest port — and DELETING THE NODE'S HANDLER ENTIRELY left
all 1181 unit tests green. Each piece was necessary, none was sufficient,
and nothing covered the connective tissue. A graph can be wired and a
manifest can declare a port while the node quietly ignores the event.
"""

import json
import sys
import types

import numpy as np
import pyarrow as pa
import pytest

pytestmark = pytest.mark.unit


class FakeNode:
    """Yields a scripted event list; records every send_output."""

    def __init__(self, events):
        self._events = events
        self.sent: list[tuple[str, list, dict]] = []

    def __iter__(self):
        return iter(self._events)

    def send_output(self, topic, value, metadata=None):
        self.sent.append((topic, list(value.to_pylist()), metadata or {}))


def _input(topic: str, value, metadata: dict | None = None) -> dict:
    return {"type": "INPUT", "id": topic, "value": value, "metadata": metadata or {}}


def nav_goal(pose, goal_id: str) -> dict:
    return _input("nav_goal", pa.array([json.dumps({"pose": pose})]), {"goal_id": goal_id})


def base_pose(xy_yaw, sim_ns: int) -> dict:
    return _input(
        "base_pose",
        pa.array(np.asarray(xy_yaw, dtype=np.float32)),
        {"sim_time_ns": sim_ns},
    )


def reset_done(seq: int | None = None) -> dict:
    """`seq` is the TC-2 per-topic counter dora-genesis stamps; nav uses it
    as the episode epoch (issue #179 review)."""
    return _input(
        "reset_done", pa.array(np.zeros(1, dtype=np.uint32)), {} if seq is None else {"seq": seq}
    )


def nav_goal_at(pose, goal_id: str, epoch) -> dict:
    """A nav_goal stamped with the episode its producer believed it was in."""
    ev = nav_goal(pose, goal_id)
    ev["metadata"]["episode_epoch"] = epoch
    return ev


def run_node(events, monkeypatch) -> FakeNode:
    """Drive nav_action.main() over `events` with dora faked out."""
    node = FakeNode(events)
    fake_dora = types.ModuleType("dora")
    fake_dora.Node = lambda: node
    monkeypatch.setitem(sys.modules, "dora", fake_dora)
    monkeypatch.setenv("AISLE_EMBODIMENT", "mobile")

    from aisle.nodes.nav_action import main

    main()
    return node


STEP = 20_000_000  # 50 Hz sim cadence (MOB-1)


def results(node: FakeNode) -> list[dict]:
    return [json.loads(v[0]) for topic, v, _ in node.sent if topic == "nav_result"]


def base_cmds(node: FakeNode) -> list[list]:
    return [v for topic, v, _ in node.sent if topic == "base_cmd"]


def test_the_node_dispatches_the_episode_boundary_to_the_machine(monkeypatch):
    """MOB-2, TC-7 (issue #179), end to end through the node: a goal still in
    flight when the episode ends must not block the NEXT episode's first
    goal. Deleting the node's `reset_done` branch must fail here — with the
    branch gone, nav stays latched on nav-001 and refuses nav-002, which is
    the whole bug."""
    node = run_node(
        [
            nav_goal([5.0, 0.0, 0.0], "nav-001"),
            base_pose([0.0, 0.0, 0.0], STEP),  # mid-leg, far from target
            reset_done(),  # the episode ends with the leg in flight
            nav_goal([1.0, 0.0, 0.0], "nav-002"),  # the NEXT episode's goal
            base_pose([0.0, 0.0, 0.0], 2 * STEP),
        ],
        monkeypatch,
    )
    # the new goal was ACCEPTED: nav is driving for nav-002, not nav-001
    goal_ids = {m.get("goal_id") for topic, _, m in node.sent if topic == "base_cmd"}
    assert "nav-002" in goal_ids, f"the next episode's goal never took effect; saw {goal_ids}"


def test_the_boundary_zeroes_the_base(monkeypatch):
    """The abandoned leg must not leave a live command as the last thing nav
    said. (The guard emits its own zero at reset_done, MOB-3 — this is nav
    owning its own output, and it is what the node adds on top of the pure
    machine, which emits nothing.)"""
    node = run_node(
        [
            nav_goal([5.0, 0.0, 0.0], "nav-001"),
            base_pose([0.0, 0.0, 0.0], STEP),
            reset_done(),
        ],
        monkeypatch,
    )
    assert base_cmds(node), "nav never commanded the base; the setup proves nothing"
    assert base_cmds(node)[-1] == pytest.approx([0.0, 0.0]), (
        f"last command was not a stop: {base_cmds(node)[-1]}"
    )


def test_the_boundary_emits_no_nav_result(monkeypatch):
    """A result here would carry the OLD goal_id into a fresh episode — the
    very confusion #179 is about — and "the episode ended" is not one of
    MOB-2's failure values."""
    node = run_node(
        [
            nav_goal([5.0, 0.0, 0.0], "nav-001"),
            base_pose([0.0, 0.0, 0.0], STEP),
            reset_done(),
        ],
        monkeypatch,
    )
    assert results(node) == [], results(node)


def test_an_idle_boundary_is_quiet(monkeypatch):
    """MOB-2: resets arrive every episode, most with no leg in flight. A
    boundary with nothing to abandon must not emit a spurious stop — the
    guard is already holding the base, and an extra base_cmd is both noise
    on a topic the trace tooling reads and a needless refresh of the MOB-3
    watchdog's command clock."""
    node = run_node([reset_done(), reset_done()], monkeypatch)
    assert node.sent == [], node.sent


def test_a_goal_that_crossed_the_boundary_in_flight_is_refused(monkeypatch):
    """MOB-2, TC-7 (issue #179 review). The regression the boundary itself
    opened: a goal emitted just BEFORE the reset but delivered just after.
    Clearing `target` stopped it being refused as "nav active", so nav
    accepted it as fresh and drove the PREVIOUS episode's target through
    the whole new episode while refusing the real goal behind it —
    reproduced against this node before the epoch check existed.

    Both halves are asserted: the stale goal never steers, and the real one
    still does. Correlating only nav_result left this direction open."""
    node = run_node(
        [
            reset_done(seq=7),  # episode 7 begins
            nav_goal_at([5.0, 0.0, 0.0], "nav-old", epoch=6),  # emitted in episode 6
            base_pose([0.0, 0.0, 0.0], STEP),
            nav_goal_at([1.0, 0.0, 0.0], "nav-new", epoch=7),  # episode 7's real goal
            base_pose([0.0, 0.0, 0.0], 2 * STEP),
        ],
        monkeypatch,
    )
    steered = [m.get("goal_id") for topic, _, m in node.sent if topic == "base_cmd"]
    assert "nav-old" not in steered, f"drove the previous episode's goal: {steered}"
    assert "nav-new" in steered, f"the live goal never took effect: {steered}"


def test_a_goal_without_an_epoch_is_still_accepted(monkeypatch):
    """MOB-2: producers that do not track episodes (the acceptance harness,
    any planner with no reset_done input) must keep working — the epoch
    check degrades the way parse_sim_stamp does, refusing only a goal that
    states an epoch and states the wrong one."""
    node = run_node(
        [
            reset_done(seq=3),
            nav_goal([1.0, 0.0, 0.0], "nav-unstamped"),  # no episode_epoch at all
            base_pose([0.0, 0.0, 0.0], STEP),
        ],
        monkeypatch,
    )
    assert "nav-unstamped" in [m.get("goal_id") for t, _, m in node.sent if t == "base_cmd"]


def test_a_completed_goal_still_reports_and_stops(monkeypatch):
    """MOB-2 lifecycle, unchanged by #179: guards the happy path against a
    boundary handler that over-clears — arrival must still produce a
    nav_result and a terminal zero."""
    node = run_node(
        [
            nav_goal([1.0, 0.0, 0.0], "nav-001"),
            base_pose([0.0, 0.0, 0.0], STEP),
            base_pose([1.0, 0.0, 0.0], 2 * STEP),  # arrived
        ],
        monkeypatch,
    )
    assert [r["status"] for r in results(node)] == ["success"], results(node)
    assert base_cmds(node)[-1] == pytest.approx([0.0, 0.0])
