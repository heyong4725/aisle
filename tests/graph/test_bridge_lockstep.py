"""ADR-30 closed-turn protocol acceptance without importing Genesis.

The state transition is deliberately tiny; the property under test is the
orchestration boundary that decides which command reaches which physics step.
Live expert graph tests exercise the same bridge loop with Genesis.
"""

from __future__ import annotations

import pytest

from aisle.turns import BridgeTurn, ProtocolError, TurnBarrier, TurnStamp, watermark_metadata

pytestmark = [pytest.mark.unit, pytest.mark.accept]


def _wm(stamp: TurnStamp, outputs: dict[str, int]) -> dict:
    return watermark_metadata(stamp, outputs)


def _run(delay_schedule: str) -> tuple[list[tuple[int, int]], list[tuple[int, int, int]]]:
    plan = {
        "bridge": "bridge",
        "bridge_outputs": ["sim_turn", "state"],
        "participants": {
            "policy": {
                "inputs": {"state": {"source": "bridge", "output": "state", "edge": "forward"}},
                "outputs": ["command", "turn_done"],
            }
        },
    }
    barrier = TurnBarrier(plan)
    x = 0
    assignments: list[tuple[int, int]] = []
    states: list[tuple[int, int, int]] = []
    epoch = 11

    # Turn zero consumes the mandatory reset and takes no physics step.
    stamp = TurnStamp(epoch, 0, 0)
    assert barrier.open_bridge(_wm(stamp, {"sim_turn": 1, "state": 1})) == {"policy": {"state": 1}}
    reset = BridgeTurn(stamp)
    reset.accept("reset", "seed-100", {**stamp.metadata(), "seq": 1})
    barrier.close("policy", _wm(stamp, {"command": 0, "turn_done": 1}))
    assert barrier.complete
    assert reset.commit(stamp.metadata()) == [("reset", "seed-100")]
    assert reset.advances_physics is False

    # One full CON-5 comparison window: 100 x 10 ms steps.
    for turn_id in range(1, 101):
        stamp = TurnStamp(epoch, turn_id, (turn_id - 1) * 10_000_000)
        ready = barrier.open_bridge(_wm(stamp, {"sim_turn": 1, "state": 1}))
        assert ready == {"policy": {"state": 1}}
        bridge_turn = BridgeTurn(stamp)
        command = turn_id % 3 - 1
        metadata = {**stamp.metadata(), "seq": turn_id}

        if delay_schedule == "command-first":
            bridge_turn.accept("joint_cmd", command, metadata)
        barrier.close("policy", _wm(stamp, {"command": 1, "turn_done": 1}))
        if delay_schedule == "watermark-first":
            bridge_turn.accept("joint_cmd", command, metadata)
        assert barrier.complete

        actions = bridge_turn.commit(stamp.metadata())
        applied = actions[0][1]
        assignments.append((turn_id, applied))
        x += applied
        states.append((turn_id, stamp.sim_time_ns + 10_000_000, x))

    return assignments, states


def test_two_wall_delay_schedules_assign_identical_commands_and_physics():
    """BRG-1/CON-5: latency changes neither turn assignment nor 1 s physics."""
    fast = _run("command-first")
    delayed = _run("watermark-first")
    assert fast == delayed
    assert fast[1][-1][1] == 1_000_000_000


def test_hung_or_invalid_turn_never_reaches_a_physics_transition():
    """BRG-1: missing closure and invalid commits cannot manufacture a step."""
    stamp = TurnStamp(4, 8, 80_000_000)
    barrier = TurnBarrier(
        {
            "bridge": "bridge",
            "bridge_outputs": ["sim_turn", "state"],
            "participants": {
                "policy": {
                    "inputs": {"state": {"source": "bridge", "output": "state", "edge": "forward"}},
                    "outputs": ["command", "turn_done"],
                }
            },
        }
    )
    barrier.open_bridge(_wm(stamp, {"sim_turn": 1, "state": 1}))
    assert not barrier.complete  # hung: there is no legal commit to send

    turn = BridgeTurn(stamp)
    with pytest.raises(ProtocolError):
        turn.commit(TurnStamp(4, 9, 80_000_000).metadata())
    assert turn.advances_physics is None
