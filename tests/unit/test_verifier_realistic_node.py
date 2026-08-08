"""Realistic verifier node core (SPEC 040 VER-5 increment 1b), no dora.

The node's job is to hand `judge_frames` the SAME judged frames a replay of
the recording would, and to decide episode end WITHOUT the oracle. Both are
tested here; the dora event loop itself is a thin shell over these.
"""

import numpy as np
import pytest

from aisle.harness.trace_recorder import CaptureSchedule
from aisle.nodes.verifier_realistic import EpisodeBuffer, episode_result

pytestmark = pytest.mark.unit


def _buffer(period_s=5.0, timeout_s=60.0, start_ns=0):
    schedule = CaptureSchedule(int(period_s * 1e9))
    schedule.start(start_ns)
    return EpisodeBuffer(
        goal_id="ep-0000",
        target_med="omeprazole",
        start_ns=start_ns,
        timeout_ns=int(timeout_s * 1e9),
        schedule=schedule,
    )


def _feed(buf, stamp_ns, wrist=True):
    buf.observe_frame("rgb_overhead", stamp_ns, np.zeros((4, 4, 3), np.uint8))
    buf.observe_frame("depth_overhead", stamp_ns, np.zeros((4, 4), np.float32))
    if wrist:
        buf.observe_frame("rgb_wrist", stamp_ns, np.zeros((3, 3, 3), np.uint8))


def test_retains_the_frame_at_or_before_each_checkpoint():
    """VER-9's selector, shared with the recorder: with renders bracketing a
    5.000 s boundary the judged frame is 4.967 s, not 5.033 s. A live verdict
    and a replay of the recording must judge the same pixels (VER-6/VER-7)."""
    buf = _buffer()
    for stamp in (4_933_000_000, 4_967_000_000, 5_033_000_000, 5_100_000_000):
        _feed(buf, stamp)

    assert 4_967_000_000 in buf.frames["overhead"]
    assert 5_033_000_000 not in buf.frames["overhead"]


def test_judged_set_matches_the_production_selector():
    """The strongest form: the node's ONLINE judged set equals what
    `checkpoint_stamps()` picks OFFLINE over the same rendered stamps, so a
    live verdict and a replay of the recording cannot judge different pixels
    (VER-6/VER-7)."""
    from aisle.verifier.realistic import checkpoint_stamps

    stamps = [70_000_000 + i * 33_000_000 for i in range(400)]  # ~30 Hz
    buf = _buffer(start_ns=stamps[0])
    for stamp in stamps:
        _feed(buf, stamp)
    buf.promote_terminal()

    expected = checkpoint_stamps(stamps[0], stamps[-1], 5.0, stamps)
    assert sorted(buf.frames["overhead"]) == expected


def test_judged_frames_have_the_shape_judge_frames_consumes():
    """`frames[camera][sim_time_ns] -> {"rgb", "depth"}` — the same mapping
    the offline replay builds, so the two paths cannot drift."""
    buf = _buffer()
    _feed(buf, 1_000_000_000)
    _feed(buf, 6_000_000_000)
    buf.promote_terminal()

    overhead = buf.frames["overhead"][6_000_000_000]
    assert sorted(overhead) == ["depth", "rgb"]
    assert overhead["rgb"].shape == (4, 4, 3)
    assert overhead["depth"].shape == (4, 4)
    assert list(buf.frames["wrist"][6_000_000_000]) == ["rgb"]


def test_overhead_pair_from_two_ticks_is_not_promoted():
    """BRG-2 renders rgb and depth in one pass; a pair from different ticks
    would make the geometry stages fuse pixels from two scenes."""
    buf = _buffer()
    buf.observe_frame("rgb_overhead", 6_000_000_000, np.zeros((4, 4, 3), np.uint8))
    buf.observe_frame("depth_overhead", 5_900_000_000, np.zeros((4, 4), np.float32))
    buf.observe_frame("rgb_overhead", 7_000_000_000, np.zeros((4, 4, 3), np.uint8))

    assert not buf.judgeable()


def test_wrist_frames_carry_the_ee_pose_at_their_own_stamp():
    """VER-8: the wrist ROI composes `cam_to_ee` with the EE pose from FK at
    the frame's stamp, so the buffer must sample joints per judged frame."""
    buf = _buffer()
    buf.observe_joints(900_000_000, np.zeros(9))
    _feed(buf, 1_000_000_000)
    _feed(buf, 6_000_000_000)
    buf.promote_terminal()

    assert 6_000_000_000 in buf.ee_poses
    pos, quat = buf.ee_poses[6_000_000_000]
    assert len(pos) == 3 and len(quat) == 4


def test_terminal_frame_is_promoted_even_between_checkpoints():
    """VER-9 always judges the terminal frame, and an episode that ends
    mid-period would otherwise drop exactly the frame that decides it."""
    buf = _buffer()
    _feed(buf, 1_000_000_000)  # snaps to the first checkpoint
    _feed(buf, 3_000_000_000)  # mid-period: no boundary crossed
    assert sorted(buf.frames["overhead"]) == [1_000_000_000]

    assert buf.promote_terminal()
    assert 3_000_000_000 in buf.frames["overhead"]


def test_episode_ends_on_its_own_sim_budget_not_the_oracle():
    """A7 holds the oracle out, so this node cannot wait for the oracle's
    verdict — agreement would then be true by construction and VER-6 would
    measure nothing."""
    buf = _buffer(timeout_s=60.0, start_ns=10_000_000_000)

    assert not buf.expired(60_000_000_000)
    assert buf.expired(70_000_000_000)


def test_result_uses_the_oracle_schema_with_a_realistic_tag():
    """TC-7 unchanged: same field names, so the rollout runner and
    `harness/fidelity.py` need no special case (VER-5)."""
    ok = episode_result("ep-0003", True, None, 20.434)
    bad = episode_result("ep-0003", False, "timeout", 60.0)

    assert ok == {
        "status": "success",
        "failure": None,
        "t_end": 20.43,
        "goal_id": "ep-0003",
        "verifier": "realistic",
    }
    assert bad["status"] == "fail" and bad["failure"] == "timeout"


def test_sidecar_node_never_subscribes_to_oracle_state(tmp_path):
    """A7's premise is that the realistic verdict never saw privileged
    state. The node is injected into the rollout's INSTRUMENTED graph, which
    VAL-6's oracle-isolation check does not police (ADR-11 clause 1), so
    nothing structural stops a future edit from wiring `oracle_state` in.
    This is that guard."""
    import yaml

    from aisle.harness.rollout import instrumented_graph

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    out = instrumented_graph(
        root / "graphs" / "expert_t0.yaml", root, run_dir, verifier="both", episode_timeout_s=60.0
    )
    node = next(
        n for n in yaml.safe_load(out.read_text())["nodes"] if n["id"] == "verifier-realistic"
    )

    assert "oracle_state" not in node["inputs"]
    assert not any("oracle" in src["source"] for src in node["inputs"].values())
    # and it must actually receive what it needs to judge
    assert {"bridge_info", "episode_goal", "joint_state", "rgb_overhead", "depth_overhead"} <= set(
        node["inputs"]
    )


def test_sidecar_node_is_absent_unless_asked_for():
    """`--verifier oracle` is the default and must produce the graph it
    always produced — the judge costs seconds per episode."""
    import yaml

    from aisle.harness.rollout import instrumented_graph

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        run_dir = __import__("pathlib").Path(d) / "run"
        run_dir.mkdir()
        out = instrumented_graph(root / "graphs" / "expert_t0.yaml", root, run_dir)
        ids = [n["id"] for n in yaml.safe_load(out.read_text())["nodes"]]

    assert "verifier-realistic" not in ids
    assert "trace-recorder" in ids
