"""SPEC 210 MOB-3 acceptance: the arm/base mutual exclusion through the
budget guard in a LIVE dataflow. With the arm in motion, a base_cmd above
creep MUST be clamped to creep and a `base_arm_exclusion` violation emitted.
No genesis here — the guard is a pure node, so this graph is fast."""

import shutil
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.graph,
    pytest.mark.skipif(shutil.which("dora") is None, reason="dora CLI not installed"),
]

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "src" / "aisle" / "nodes" / "budget_guard.py"
FIXTURES = REPO / "tests" / "fixtures" / "nodes"


def _write_graph(tmp: Path, rec_out: Path) -> Path:
    graph = {
        "nodes": [
            {
                "id": "driver",
                "path": str(FIXTURES / "guard_mutex_driver.py"),
                "inputs": {"tick": "dora/timer/millis/20"},
                "outputs": ["joint_cmd", "base_cmd"],
            },
            {
                "id": "guard",
                "path": str(GUARD),
                "inputs": {
                    "joint_cmd": {"source": "driver/joint_cmd", "queue_size": 100},
                    "base_cmd": {"source": "driver/base_cmd", "queue_size": 100},
                },
                "outputs": ["base_cmd_safe", "violation", "joint_cmd_safe", "gripper_cmd_safe"],
                "env": {"AISLE_EMBODIMENT": "mobile"},
            },
            {
                "id": "rec",
                "path": str(FIXTURES / "base_recorder.py"),
                "inputs": {
                    "base_cmd_safe": {"source": "guard/base_cmd_safe", "queue_size": 400},
                    "violation": {"source": "guard/violation", "queue_size": 400},
                },
                "env": {"REC_OUT": str(rec_out)},
            },
        ]
    }
    import yaml

    path = tmp / "guard_mutex.yaml"
    path.write_text(yaml.safe_dump(graph))
    return path


def test_arm_base_exclusion(tmp_path, dataflow):
    """MOB-3: while the arm is in motion (the driver oscillates a joint every
    tick), a forward base_cmd of 0.5 m/s (> v_creep) is clamped to creep and
    a base_arm_exclusion violation is published — the mutex holds live."""
    import json

    from aisle.mobility.guard import load_base_limits

    creep = load_base_limits("mobile").v_creep
    rec_out = tmp_path / "guard.jsonl"
    graph = _write_graph(tmp_path, rec_out)
    # pure-python nodes: no genesis build, so a short window suffices
    dataflow.run(graph, timeout_s=45)
    rows = dataflow.read(rec_out)

    safes = [r["value"] for r in rows if r["id"] == "base_cmd_safe"]
    viols = [json.loads(r["value"][0]) for r in rows if r["id"] == "violation"]
    assert len(safes) > 5, f"few base_cmd_safe samples: {len(safes)}"

    # the mutex fired: base_arm_exclusion violations, each clamping to creep
    exclusion_v = [v for v in viols if v["reason"] == "base_arm_exclusion" and v.get("axis") == "v"]
    assert exclusion_v, f"no base_arm_exclusion violation; reasons={[v['reason'] for v in viols]}"
    for v in exclusion_v:
        assert v["clamped"] == pytest.approx(creep, abs=1e-6)

    # and the emitted safe command respects creep once the mutex engages
    assert any(abs(s[0]) <= creep + 1e-6 for s in safes), "base was never clamped to creep"


def _write_keepout_graph(tmp: Path, rec_out: Path) -> Path:
    graph = {
        "nodes": [
            {
                "id": "driver",
                "path": str(FIXTURES / "keepout_driver.py"),
                "inputs": {"tick": "dora/timer/millis/20"},
                "outputs": ["joint_cmd", "base_pose", "base_cmd"],
            },
            {
                "id": "guard",
                "path": str(GUARD),
                "inputs": {
                    "joint_cmd": {"source": "driver/joint_cmd", "queue_size": 100},
                    "base_pose": {"source": "driver/base_pose", "queue_size": 100},
                    "base_cmd": {"source": "driver/base_cmd", "queue_size": 100},
                },
                "outputs": ["base_cmd_safe", "violation", "joint_cmd_safe"],
                "env": {"AISLE_EMBODIMENT": "mobile"},
            },
            {
                "id": "rec",
                "path": str(FIXTURES / "base_recorder.py"),
                "inputs": {
                    "base_cmd_safe": {"source": "guard/base_cmd_safe", "queue_size": 400},
                    "violation": {"source": "guard/violation", "queue_size": 400},
                },
                "env": {"REC_OUT": str(rec_out)},
            },
        ]
    }
    import yaml

    path = tmp / "keepout.yaml"
    path.write_text(yaml.safe_dump(graph))
    return path


def test_keepout_blocks_extended_arm_near_shelf(tmp_path, dataflow):
    """MOB-3 keep-out (PR #14 re-review): with the arm reaching (flange past
    the reach threshold) and base_pose wired 0.32 m from a shelf, a forward
    base_cmd is clamped to a stop and a base_keepout violation is published —
    the keep-out holds live with pose feedback wired."""
    import json

    rec_out = tmp_path / "keepout.jsonl"
    graph = _write_keepout_graph(tmp_path, rec_out)
    dataflow.run(graph, timeout_s=45)
    rows = dataflow.read(rec_out)

    safes = [r["value"] for r in rows if r["id"] == "base_cmd_safe"]
    viols = [json.loads(r["value"][0]) for r in rows if r["id"] == "violation"]
    assert len(safes) > 5, f"few base_cmd_safe samples: {len(safes)}"
    assert any(v["reason"] == "base_keepout" for v in viols), (
        f"no base_keepout violation; reasons={sorted({v['reason'] for v in viols})}"
    )
    # the base is held (v clamped to ~0) inside the keep-out zone
    assert any(abs(s[0]) <= 1e-6 for s in safes), "base was never stopped by keep-out"


def _write_watchdog_graph(
    tmp: Path,
    rec_out: Path,
    *,
    stamp_poses: bool = True,
    pose_count: int = 0,
    keep_commanding: bool = False,
    sweep_ms: int | None = None,
    recorder_s: float | None = None,
    await_spec: str | None = None,
    await_tail_s: float | None = None,
) -> Path:
    """One-command-then-silent driver + guard. stamp_poses=False exercises
    the ADR-29 wall net (unstamped poses blind the sim clock); pose_count=N
    stops the pose stream after N ticks (a hung sim — only the stats-tick
    sweep can act); keep_commanding=True never goes silent, which is the
    issue #182 shape (see the fixture docstring). Both need a `tick`
    (sweep_ms) wired; guard_stats then
    also feeds the recorder so the capture window can settle after the
    command stream dies. await_spec ("topic:count") holds the recorder
    window open until the awaited row lands (issue #160: the wall-net
    assertions await a discrete LATE event inside a wall-only window —
    the issue #94 truncation class — so under suite load the window must
    not close before the backstop stop)."""
    guard_inputs = {
        "base_cmd": {"source": "driver/base_cmd", "queue_size": 100},
        # the watchdog clock (ADR-29)
        "base_pose": {"source": "driver/base_pose", "queue_size": 100},
    }
    rec_inputs = {
        "base_cmd_safe": {"source": "guard/base_cmd_safe", "queue_size": 400},
        "violation": {"source": "guard/violation", "queue_size": 400},
    }
    guard_outputs = ["base_cmd_safe", "violation"]
    if sweep_ms is not None:
        guard_inputs["tick"] = f"dora/timer/millis/{sweep_ms}"
        guard_outputs.append("guard_stats")
        rec_inputs["guard_stats"] = {"source": "guard/guard_stats", "queue_size": 100}
    graph = {
        "nodes": [
            {
                "id": "driver",
                "path": str(FIXTURES / "latch_driver.py"),
                "inputs": {"tick": "dora/timer/millis/20"},
                "outputs": ["base_cmd", "base_pose"],
                "env": {
                    "LATCH_STAMP_POSES": "1" if stamp_poses else "0",
                    "LATCH_POSE_COUNT": str(pose_count),
                    "LATCH_KEEP_COMMANDING": "1" if keep_commanding else "0",
                },
            },
            {
                "id": "guard",
                "path": str(GUARD),
                "inputs": guard_inputs,
                "outputs": guard_outputs,
                "env": {"AISLE_EMBODIMENT": "mobile"},
            },
            {
                "id": "rec",
                "path": str(FIXTURES / "base_recorder.py"),
                "inputs": rec_inputs,
                "env": {"REC_OUT": str(rec_out)}
                | ({"RECORDER_DURATION_S": str(recorder_s)} if recorder_s else {})
                | ({"RECORDER_AWAIT": await_spec} if await_spec else {})
                | ({"RECORDER_AWAIT_TAIL_S": str(await_tail_s)} if await_tail_s else {}),
            },
        ]
    }
    import yaml

    name = "watchdog" if stamp_poses else "wall_net"
    if keep_commanding:
        name += "_blind_drive"
    path = tmp / (f"{name}_hung.yaml" if pose_count else f"{name}.yaml")
    path.write_text(yaml.safe_dump(graph))
    return path


def _assert_latched_command_stopped(rows, reason: str) -> None:
    """The shared watchdog oracle: the one command passed (v>0), a stop
    ([0,0]) followed, and the violation carries the expected reason."""
    import json

    safes = [r["value"] for r in rows if r["id"] == "base_cmd_safe"]
    viols = [json.loads(r["value"][0]) for r in rows if r["id"] == "violation"]
    assert any(s[0] > 0.0 for s in safes), "the initial base command never passed"
    assert any(s[0] == 0.0 for s in safes), "the watchdog never emitted a stop"
    assert any(v["reason"] == reason for v in viols), (
        f"no {reason} violation; reasons={sorted({v['reason'] for v in viols})}"
    )


def test_watchdog_stops_latched_base_command(tmp_path, dataflow):
    """MOB-3 (PR #14 re-review; sim-anchored per ADR-29): the driver sends
    ONE forward base_cmd then goes silent while sim-stamped base_pose keeps
    flowing. The bridge would integrate that latched command forever, so
    the guard's pose-driven watchdog emits [0,0] + a base_stale violation
    once the command is base_staleness_s of SIM time old (CON-5)."""
    rec_out = tmp_path / "watchdog.jsonl"
    graph = _write_watchdog_graph(tmp_path, rec_out)
    dataflow.run(graph, timeout_s=45)
    _assert_latched_command_stopped(dataflow.read(rec_out), "base_stale")


def test_wall_net_stops_latched_command_without_sim_stamps(tmp_path, dataflow):
    """MOB-3 fail-closed (ADR-29 wall net, PR review): a pose source that
    carries NO sim stamps blinds the sim-time staleness check (every stamp
    reads 0), and the producer dies after one forward base_cmd. The wall
    net swept on the stats tick must still stop the latched command within
    base_wall_backstop_s wall seconds, under its own distinct reason (the
    net firing is an ops alarm, not the sim mechanism working) — the
    watchdog never fails open."""
    from aisle.mobility.guard import load_base_limits

    rec_out = tmp_path / "wall_net.jsonl"
    # recorder window: backstop + sweep + margin; guard_stats (1 Hz) keeps
    # events flowing after the stop so the settle sentinel can land. The
    # window additionally AWAITS the violation row (issue #160): the stop
    # is a discrete late event, and under suite load a wall-only window
    # of backstop+5 could close before it — the issue #94 truncation class
    backstop = load_base_limits("mobile").base_wall_backstop_s
    graph = _write_watchdog_graph(
        tmp_path,
        rec_out,
        stamp_poses=False,
        sweep_ms=1000,
        recorder_s=backstop + 5,
        await_spec="violation:1",
        await_tail_s=2.0,
    )
    dataflow.run_until_settled(graph, rec_out, deadline_s=int(backstop * 3 + 30))
    _assert_latched_command_stopped(dataflow.read(rec_out), "base_stale_wall")


def test_blind_drive_stop_sticks_against_a_producer_that_keeps_commanding(tmp_path, dataflow):
    """MOB-3 (issue #182 + its review): the regression test for the actual
    bug. The driver commands forward on EVERY tick while its poses carry no
    sim stamps — nav_action's real shape. Both older nets are structurally
    disarmed: command-silence never arms (the command is always fresh) and
    sim-time cannot advance (no clock). Only `base_blind_wall` can act.

    The oracle is deliberately STRICTER than the shared one. The first
    revision of this fix emitted the stop from the pose handler only, and
    the producer's next command re-latched a nonzero base_cmd_safe behind
    it — the bridge is last-write-wins, so the base never actually slowed.
    `any(v == 0)` passes on that; "no nonzero after the stop" does not.

    It also pins the violation as an EDGE: the un-throttled version emitted
    one violation and one stderr line PER POSE (~250 per nav goal), which
    floods the recorded topic under drop-oldest backpressure (issue #183)."""
    import json

    from aisle.mobility.guard import load_base_limits

    rec_out = tmp_path / "blind_drive.jsonl"
    backstop = load_base_limits("mobile").base_wall_backstop_s
    graph = _write_watchdog_graph(
        tmp_path,
        rec_out,
        stamp_poses=False,
        keep_commanding=True,
        sweep_ms=1000,
        recorder_s=backstop + 8,
        await_spec="violation:1",
        await_tail_s=4.0,
    )
    dataflow.run_until_settled(graph, rec_out, deadline_s=int(backstop * 3 + 30))
    rows = dataflow.read(rec_out)
    safes = [r["value"] for r in rows if r["id"] == "base_cmd_safe"]
    viols = [json.loads(r["value"][0]) for r in rows if r["id"] == "violation"]

    assert any(s[0] > 0.0 for s in safes), "the base never drove; the setup proves nothing"
    blind = [v for v in viols if v["reason"] == "base_blind_wall"]
    assert blind, f"no base_blind_wall; reasons={sorted({v['reason'] for v in viols})}"

    # STICKY: once stopped, no nonzero command may reach the bridge again --
    # the producer is still commanding forward the whole time
    first_stop = next(i for i, s in enumerate(safes) if s[0] == 0.0)
    resumed = [s for s in safes[first_stop + 1 :] if s[0] != 0.0]
    assert not resumed, (
        f"{len(resumed)} nonzero commands forwarded AFTER the stop -- "
        f"the base is stuttering, not stopped: {safes[first_stop : first_stop + 8]}"
    )

    # EDGE-triggered: one violation for the blind stretch, not one per pose
    assert len(blind) <= 2, f"blind-drive violation flooded: {len(blind)} emitted"


def test_tick_sweep_stops_latched_command_when_poses_cease(tmp_path, dataflow):
    """MOB-3 fail-closed (ADR-29 wall net, sweep path): a HUNG sim — the
    driver sends validly-stamped poses for ~0.5 s, one forward base_cmd,
    then the pose stream dies. The pose handler never runs again, sim
    staleness cannot advance, so ONLY the stats-tick sweep can stop the
    latched command once the pose silence exceeds the backstop. Deleting
    the sweep loop must fail this test (PR #156 review: the wall-net test
    alone fires from the pose handler and would not notice)."""
    from aisle.mobility.guard import load_base_limits

    rec_out = tmp_path / "hung_sim.jsonl"
    backstop = load_base_limits("mobile").base_wall_backstop_s
    # await the late stop's violation row (issue #160, same rationale as
    # the wall-net test above)
    graph = _write_watchdog_graph(
        tmp_path,
        rec_out,
        stamp_poses=True,
        pose_count=25,
        sweep_ms=1000,
        recorder_s=backstop + 5,
        await_spec="violation:1",
        await_tail_s=2.0,
    )
    dataflow.run_until_settled(graph, rec_out, deadline_s=int(backstop * 3 + 30))
    _assert_latched_command_stopped(dataflow.read(rec_out), "base_stale_wall")


def test_capture_helper_fails_on_stall(tmp_path, dataflow):
    """PR #14 re-review: run_dataflow_until_settled MUST NOT accept a stalled
    capture. latch_driver emits ONE base_cmd then goes silent, so the recorder
    (a 30 s window) never sees a post-window event and writes no
    __recorder_done__ sentinel — the helper raises instead of returning a
    truncated pre-stall slice after the deadline."""
    import yaml

    rec_out = tmp_path / "stall.jsonl"
    graph = {
        "nodes": [
            {
                "id": "driver",
                "path": str(FIXTURES / "latch_driver.py"),
                "inputs": {"tick": "dora/timer/millis/20"},
                "outputs": ["base_cmd"],
            },
            {
                "id": "rec",
                "path": str(FIXTURES / "base_recorder.py"),
                "inputs": {"base_cmd": {"source": "driver/base_cmd", "queue_size": 100}},
                # 30 s window never completes: the stream stops after one event
                "env": {"REC_OUT": str(rec_out), "RECORDER_DURATION_S": "30"},
            },
        ]
    }
    path = tmp_path / "stall.yaml"
    path.write_text(yaml.safe_dump(graph))
    with pytest.raises(AssertionError, match="sentinel"):
        dataflow.run_until_settled(path, rec_out, deadline_s=12)
