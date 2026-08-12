"""Realistic verifier node core (SPEC 040 VER-5 increment 1b), no dora.

The node's job is to hand `judge_frames` the SAME judged frames a replay of
the recording would, and to decide episode end WITHOUT the oracle. Both are
tested here; the dora event loop itself is a thin shell over these.
"""

import numpy as np
import pytest

from aisle.harness.trace_recorder import CaptureSchedule
from aisle.nodes.verifier_realistic import EpisodeBuffer, EpisodeRouter, episode_result

pytestmark = pytest.mark.unit

S = 1_000_000_000  # 1 sim second in ns


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


@pytest.mark.parametrize("start_ms", [0, 40], ids=["first-half-phase", "second-half-phase"])
def test_live_selector_retains_the_matched_pair_before_each_boundary(start_ms):
    """Issue #136 / VER-6/VER-7: the live verifier shares the recorder's
    30 Hz RGB / 15 Hz depth interleave and must make the same at-or-before
    choice even when an RGB-only tick precedes every checkpoint."""
    period = int(5e9)
    start = start_ms * 10**6
    buf = _buffer(start_ns=start)
    half = 33_333_333
    full_stamps = []
    for k in range(320):
        stamp = k * half
        buf.observe_frame("rgb_overhead", stamp, np.zeros((4, 4, 3), np.uint8))
        if k % 2 == 0:
            full_stamps.append(stamp)
            buf.observe_frame("depth_overhead", stamp, np.zeros((4, 4), np.float32))
            buf.observe_frame("rgb_wrist", stamp, np.zeros((3, 3, 3), np.uint8))

    boundaries = list(range(start, 320 * half, period))
    expected = [
        max((stamp for stamp in full_stamps if stamp <= boundary), default=full_stamps[0])
        for boundary in boundaries
    ]
    assert sorted(buf.frames.get("overhead", {})) == expected


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
    # joints arrive at 100 Hz, so each judged frame has one within ~10 ms
    for stamp in (1_000_000_000, 6_000_000_000):
        buf.observe_joints(stamp - 5_000_000, np.zeros(9))
        _feed(buf, stamp)
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


def _router(period_s=5.0, timeout_s=60.0):
    finished = []

    def finish(buf, end_ns, failure):
        finished.append((buf, end_ns, failure))

    return EpisodeRouter(int(period_s * 1e9), int(timeout_s * 1e9), finish), finished


def _frame(router, stamp_ns, wrist=False):
    router.on_frame("rgb_overhead", stamp_ns, np.zeros((4, 4, 3), np.uint8))
    router.on_frame("depth_overhead", stamp_ns, np.zeros((4, 4), np.float32))
    if wrist:
        router.on_frame("rgb_wrist", stamp_ns, np.zeros((3, 3, 3), np.uint8))


def test_deferred_close_judges_the_true_terminal_frame():
    """Issue #120 mechanism (VER-7/VER-9): the node runs seconds behind, so
    the NEXT goal is processed while the ended episode's last camera frames
    still sit in its queues. Judging at that moment reads a stale terminal
    frame — live judged systematically worse frames than a replay of the
    SAME run (23 stage votes over 19 episodes). The router must keep the
    ended episode open for stamp-routed late frames and judge only once
    both overhead streams have delivered past its end."""
    router, finished = _router()
    router.on_goal("ep-0000", "omeprazole", 0)
    for stamp in (1 * S, 2 * S, 3 * S):
        _frame(router, stamp)
    # the next goal arrives FIRST (queue backlog) ...
    router.on_goal("ep-0001", "cetirizine", 6 * S)
    assert finished == []  # not judged yet: the streams haven't caught up
    # ... then the ended episode's remaining frames drain from the queues
    for stamp in (4 * S, 5 * S):
        _frame(router, stamp)
    assert finished == []
    # a frame past the boundary on BOTH streams proves ep-0000 is complete
    _frame(router, 6 * S + 500_000_000)
    assert [(buf.goal_id, end, failure) for buf, end, failure in finished] == [
        ("ep-0000", 6 * S, "never_delivered")
    ]
    # the judged terminal frame is the TRUE last frame of the episode
    assert max(finished[0][0].frames["overhead"]) == 5 * S


def test_stale_frames_do_not_pollute_the_next_episode():
    """The other half of issue #120: old frames processed after the next
    goal must not enter the new episode's buffer — the previous delivery
    would sit in its tray and poison the wrong-object latch on frame one,
    the live twin of the offline reset-boundary bug."""
    router, finished = _router()
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 1 * S)
    router.on_goal("ep-0001", "cetirizine", 6 * S)
    _frame(router, 5 * S)  # late old frame, drains after the new goal
    _frame(router, 6 * S)  # boundary render: still the OLD scene — nobody's
    _frame(router, 7 * S)  # first frame of the new episode
    router.flush(8 * S)

    by_id = {buf.goal_id: buf for buf, _, _ in finished}
    assert set(by_id["ep-0000"].frames["overhead"]) == {1 * S, 5 * S, 6 * S}
    assert set(by_id["ep-0001"].frames["overhead"]) == {7 * S}


def test_reset_request_bounds_the_window_before_reset_motion():
    """RST-2: frames between the client's reset request and the next goal
    show the behavioral reset picking the med back OUT of the tray. The
    stamped reset request ends the episode there, so neither the ended
    episode nor the next one judges reset motion (issue #120)."""
    router, finished = _router()
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 5 * S)
    router.on_reset(5 * S + 300_000_000)  # stamped with the result's sim time
    _frame(router, 5 * S + 600_000_000)  # reset motion: belongs to nobody
    router.on_goal("ep-0001", "cetirizine", 6 * S)
    _frame(router, 7 * S)
    router.flush(8 * S)

    by_id = {buf.goal_id: (buf, end) for buf, end, _ in finished}
    buf0, end0 = by_id["ep-0000"]
    assert end0 == 5 * S + 300_000_000
    assert set(buf0.frames["overhead"]) == {5 * S}
    assert set(by_id["ep-0001"][0].frames["overhead"]) == {7 * S}


def test_expiry_verdict_waits_for_both_overhead_streams():
    """A7 ends an episode on its own sim budget, but the verdict must still
    wait for the lagging half of the overhead pair: the terminal judged
    frame is the last COMPLETE pair at or before the budget (VER-9)."""
    router, finished = _router(timeout_s=60.0)
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 55 * S)
    router.on_frame("rgb_overhead", 59 * S, np.zeros((4, 4, 3), np.uint8))
    # rgb passes the budget -> the episode closes, but depth still lags
    router.on_frame("rgb_overhead", 61 * S, np.zeros((4, 4, 3), np.uint8))
    assert finished == []
    router.on_frame("depth_overhead", 59 * S, np.zeros((4, 4), np.float32))
    assert finished == []
    router.on_frame("depth_overhead", 61 * S, np.zeros((4, 4), np.float32))
    assert [(buf.goal_id, end, failure) for buf, end, failure in finished] == [
        ("ep-0000", 60 * S, "timeout")
    ]
    assert max(finished[0][0].frames["overhead"]) == 59 * S


def test_flush_judges_closing_and_current_in_episode_order():
    """Teardown judges everything still open, oldest first — the LAST
    episode has no next goal, and losing it (or an unresolved closing one)
    was 19 sidecar records for 20 episodes (issue #120)."""
    router, finished = _router()
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 1 * S)
    router.on_goal("ep-0001", "cetirizine", 6 * S)  # ep-0000 closing, unresolved
    _frame(router, 7 * S)
    router.flush(8 * S)

    assert [buf.goal_id for buf, _, _ in finished] == ["ep-0000", "ep-0001"]
    assert finished[1][1] == 8 * S  # the last episode ends at the last stamp


def test_late_close_is_capped_at_the_sim_budget():
    """A goal that arrives long after the previous episode's budget expired
    (camera stall, extreme backlog) must not stretch its window past the
    budget — the offline judge ends a timeout episode there too."""
    router, finished = _router(timeout_s=60.0)
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 55 * S)
    router.on_goal("ep-0001", "cetirizine", 70 * S)
    _frame(router, 71 * S)
    router.flush(72 * S)

    by_id = {buf.goal_id: (end, failure) for buf, end, failure in finished}
    assert by_id["ep-0000"] == (60 * S, "timeout")


def test_dead_gating_stream_does_not_block_verdicts_forever(capsys):
    """Liveness (PR review): if one overhead stream dies, its high-water
    freezes and no episode would ever be judged — in A7 the client then
    hangs until the wall clamp. Once the sibling stream runs well past the
    end, the episode is judged with the pairs it has, and the operator's
    stderr names the dead stream."""
    router, finished = _router()
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 3 * S)
    router.on_goal("ep-0001", "cetirizine", 6 * S)
    # depth dies; rgb alone keeps flowing past end + slack (5 s)
    for stamp in (7 * S, 9 * S, 12 * S):
        router.on_frame("rgb_overhead", stamp, np.zeros((4, 4, 3), np.uint8))
    assert [buf.goal_id for buf, _, _ in finished] == ["ep-0000"]
    assert max(finished[0][0].frames["overhead"]) == 3 * S  # last complete pair
    err = capsys.readouterr().err
    assert "depth_overhead" in err and "ep-0000" in err and "liveness net" in err


def test_both_streams_frozen_still_resolves_off_the_sim_clock():
    """PR review: renderer death freezes BOTH gating streams together (they
    come from the same renderer), so the one-laggard net never fires — in
    `both` mode goals keep arriving and closings would pile up for a whole
    campaign. A routed event stamp (joint_state, 100 Hz) passing end +
    SIM_CLOCK_STALL_SLACK_NS proves the sim ran on: judge with what there
    is. The slack sits ABOVE anything the camera queues can hold (~13 s at
    400 deep), because the latest-wins joint clock legitimately runs ahead
    of healthy backlogged cameras — a smaller slack judged early with
    stale frames (Codex P1)."""
    router, finished = _router()
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 3 * S)
    router.on_goal("ep-0001", "cetirizine", 6 * S)
    # sim alive, cameras silent — but still within what a healthy backlog
    # could explain: must NOT judge yet
    router.on_joints(20 * S, np.zeros(9))
    assert finished == []
    router.on_joints(37 * S, np.zeros(9))  # past end + 30 s: cameras are dead
    assert [buf.goal_id for buf, _, _ in finished] == ["ep-0000"]


def test_expiry_fires_from_joints_when_cameras_stop():
    """PR review: the budget expiry must not depend on camera events — if
    every camera stream stops while the sim runs on, joint_state still
    closes the episode at its budget and the sim-clock net resolves it."""
    router, finished = _router(timeout_s=60.0)
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 55 * S)
    router.on_joints(61 * S, np.zeros(9))  # budget exceeded, cameras dead
    assert finished == []  # closed, awaiting proof or the sim-clock net
    router.on_joints(91 * S, np.zeros(9))  # past end + 30 s slack
    assert [(buf.goal_id, end, failure) for buf, end, failure in finished] == [
        ("ep-0000", 60 * S, "timeout")
    ]


def test_delayed_reset_tightens_the_closed_window():
    """Codex P1: under backlog the next goal can be dequeued BEFORE the
    reset that preceded it (independent inputs). The goal closes the old
    episode at the new start — a window that still contains RST-2 reset
    motion. The delayed reset must tighten that closing window to its own
    stamp and evict everything routed beyond it."""
    router, finished = _router()
    router.on_goal("ep-0000", "omeprazole", 0)
    for stamp in (1 * S, 2 * S, 3 * S):
        _frame(router, stamp)
    router.on_goal("ep-0001", "cetirizine", 6 * S)  # closes ep-0000 at 6 s
    _frame(router, 4 * S)  # late old frames drain...
    _frame(router, 5 * S + 500_000_000)  # ...including reset MOTION at 5.5 s
    router.on_reset(5 * S)  # the delayed reset: true end was 5 s
    _frame(router, 7 * S)  # arrival proof for the tightened window
    assert [(buf.goal_id, end, failure) for buf, end, failure in finished] == [
        ("ep-0000", 5 * S, "never_delivered")
    ]
    # the 5.5 s reset-motion frame was evicted; the terminal is 4 s
    assert max(finished[0][0].frames["overhead"]) == 4 * S


def test_finish_error_retries_once_then_drops_loudly(capsys):
    """Codex P2: a finishing error (sidecar IO, publish failure) must not
    LOSE the episode — it stays queued for one retry — and a persistent
    error must not wedge every later verdict behind it: after the second
    failure the episode is dropped with a stderr note and the next episode
    still publishes."""
    calls = {"n": 0}
    finished = []

    def flaky_finish(buf, end_ns, failure):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("sidecar disk full")
        finished.append((buf.goal_id, end_ns, failure))

    from aisle.nodes.verifier_realistic import EpisodeRouter

    router = EpisodeRouter(int(5e9), int(60e9), flaky_finish)
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 1 * S)
    router.on_goal("ep-0001", "cetirizine", 6 * S)
    with pytest.raises(OSError):
        _frame(router, 7 * S)  # first finish attempt fails
    assert finished == []
    _frame(router, 8 * S)  # retry succeeds; episode was not lost
    assert [g for g, _, _ in finished] == ["ep-0000"]

    # persistent failure: dropped after the second attempt, loudly
    always = {"n": 0}
    kept = []

    def broken_finish(buf, end_ns, failure):
        always["n"] += 1
        if buf.goal_id == "ep-0000":
            raise OSError("permanently broken")
        kept.append(buf.goal_id)

    router2 = EpisodeRouter(int(5e9), int(60e9), broken_finish)
    router2.on_goal("ep-0000", "omeprazole", 0)
    _frame(router2, 1 * S)
    router2.on_goal("ep-0001", "cetirizine", 6 * S)
    for stamp in (7 * S, 8 * S):
        with pytest.raises(OSError):
            _frame(router2, stamp)
    assert "dropping ep-0000" in capsys.readouterr().err
    router2.on_goal("ep-0002", "loratadine", 12 * S)
    _frame(router2, 13 * S)  # ep-0001 now finishes: the pipeline moved on
    assert kept == ["ep-0001"]


def test_resolve_drains_multiple_closings_in_episode_order():
    """PR review: a backlog burst can close several episodes before any
    proof arrives; one frame past ALL their ends must judge them oldest
    first, and a frame past only the FIRST end judges only the first."""
    router, finished = _router(timeout_s=60.0)
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 1 * S)
    router.on_goal("ep-0001", "cetirizine", 6 * S)
    _frame(router, 7 * S)  # proves ep-0000 complete, ep-0001 still open
    assert [buf.goal_id for buf, _, _ in finished] == ["ep-0000"]
    router.on_goal("ep-0002", "loratadine", 12 * S)
    router.on_goal("ep-0003", "ibuprofen", 18 * S)  # ep-0002 closes unproven
    assert [buf.goal_id for buf, _, _ in finished] == ["ep-0000"]
    _frame(router, 19 * S)  # past BOTH remaining ends
    assert [buf.goal_id for buf, _, _ in finished] == ["ep-0000", "ep-0001", "ep-0002"]


def test_reset_with_no_episode_or_stale_stamp_is_ignored():
    """PR review: the run's FIRST reset (no episode yet) and a reset whose
    stamp does not exceed the episode's start (an unstamped request under
    backlog would collapse the window to empty and drop every frame) are
    both no-ops — the next goal bounds the episode instead."""
    router, finished = _router()
    router.on_reset(0)  # before any goal: no phantom closing
    router.on_goal("ep-0000", "omeprazole", 6 * S)
    _frame(router, 8 * S)
    router.on_reset(6 * S)  # stale stamp at the start bound: ignored
    assert router.current is not None and finished == []
    router.on_goal("ep-0001", "cetirizine", 10 * S)  # the goal bounds it
    _frame(router, 11 * S)
    assert [(buf.goal_id, end) for buf, end, _ in finished] == [("ep-0000", 10 * S)]
    assert 8 * S in finished[0][0].frames["overhead"]


def test_arrival_proof_is_strictly_past_the_end():
    """A gating frame stamped EXACTLY at the end does not prove arrival —
    same-stamp frames can still follow on the sibling stream; only a stamp
    strictly past the end closes the window (PR review pins the `<=`)."""
    router, finished = _router()
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 1 * S)
    router.on_goal("ep-0001", "cetirizine", 6 * S)
    _frame(router, 6 * S)  # exactly at the boundary: not proof
    assert finished == []
    _frame(router, 6 * S + 1)
    assert [buf.goal_id for buf, _, _ in finished] == ["ep-0000"]


def test_episode_with_no_frames_finishes_without_crashing():
    """A goal whose episode produced no frames at all (total camera outage)
    must still finish — unjudgeable, published as a failure — and a fresh
    router's flush is a no-op."""
    empty_router, empty_finished = _router()
    empty_router.flush(0)
    assert empty_finished == []

    router, finished = _router()
    router.on_goal("ep-0000", "omeprazole", 0)
    router.on_goal("ep-0001", "cetirizine", 6 * S)
    _frame(router, 7 * S)
    assert [buf.goal_id for buf, _, _ in finished] == ["ep-0000"]
    assert finished[0][0].frames == {}  # judgeable() False -> fail publishes


def test_wrist_never_gates_the_verdict():
    """VER-13: the wrist is corroborating evidence — its stream neither
    proves arrival (wrist frames past the end must not close the window)
    nor blocks it (a frozen wrist must not delay the verdict)."""
    router, finished = _router()
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 1 * S, wrist=True)
    router.on_goal("ep-0001", "cetirizine", 6 * S)
    for stamp in (7 * S, 8 * S):
        router.on_frame("rgb_wrist", stamp, np.zeros((3, 3, 3), np.uint8))
    assert finished == []  # wrist past the end proves nothing
    _frame(router, 7 * S)  # overheads past the end (wrist frozen would be fine)
    assert [buf.goal_id for buf, _, _ in finished] == ["ep-0000"]


def test_result_metadata_carries_the_tc2_keys():
    """PR review: the published verdict's metadata is what the A7 client
    reads to stamp the next reset — a regression here silently reverts
    issue #120's reset bounding to a zero stamp."""
    from aisle.nodes.verifier_realistic import result_metadata

    meta = result_metadata({"goal_id": "ep-0007"}, end_ns=42 * S, seq=3)
    assert meta["sim_time_ns"] == 42 * S
    assert meta["goal_id"] == "ep-0007"
    assert meta["seq"] == 3
    assert meta["env_id"] == 0  # stamp() fills TC-2's remaining default


def test_undecodable_frame_still_advances_the_clocks():
    """PR review: a camera payload that fails to decode must still advance
    the sim-budget expiry and the arrival proof — an undecodable stream
    would otherwise stall the loop forever (the old code checked expiry on
    every camera event, decoded or not)."""
    router, finished = _router(timeout_s=60.0)
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 55 * S)
    # payloads stop decoding, but their stamps keep arriving
    router.on_frame("rgb_overhead", 61 * S, None)
    router.on_frame("depth_overhead", 61 * S, None)
    assert [(buf.goal_id, end, failure) for buf, end, failure in finished] == [
        ("ep-0000", 60 * S, "timeout")
    ]
    assert max(finished[0][0].frames["overhead"]) == 55 * S


def test_first_episode_start_accepts_a_zero_reset_stamp():
    """PR review: under ADR-25 the first reset lands at sim 0, so the goal's
    reset_sim_ns is legitimately 0 — presence decides, not truthiness, or
    episode 0's window opens at the node's stale clock instead."""
    from aisle.nodes.verifier_realistic import goal_start_ns

    assert goal_start_ns({"reset_sim_ns": 0}, fallback_ns=4 * S) == 0
    assert goal_start_ns({"reset_sim_ns": 6 * S}, fallback_ns=4 * S) == 6 * S
    assert goal_start_ns({}, fallback_ns=4 * S) == 4 * S  # pre-field goals


def test_joints_route_to_the_episode_their_stamp_belongs_to():
    """VER-8/VER-12: the ended episode's terminal joints (home stage) must
    come from ITS window, not from whatever arrived while the node was
    processing the backlog."""
    router, finished = _router()
    router.on_goal("ep-0000", "omeprazole", 0)
    _frame(router, 5 * S)
    router.on_goal("ep-0001", "cetirizine", 6 * S)
    router.on_joints(5 * S + 100_000_000, np.ones(9))  # late, old episode's
    router.on_joints(7 * S, np.zeros(9))  # new episode's
    router.flush(8 * S)

    by_id = {buf.goal_id: buf for buf, _, _ in finished}
    assert by_id["ep-0000"].joints[0] == 5 * S + 100_000_000
    assert by_id["ep-0001"].joints[0] == 7 * S


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
    # and it must actually receive what it needs to judge — including the
    # client's reset request, the episode-end bound (issue #120)
    assert {
        "bridge_info",
        "episode_goal",
        "reset",
        "joint_state",
        "rgb_overhead",
        "depth_overhead",
    } <= set(node["inputs"])
    assert node["inputs"]["reset"]["source"] == "rollout-client/reset"
    # the router's arrival proof only holds if camera queues never drop
    # during a 3-5 s judge (issue #120): 100 deep was ~3.3 s at 30 Hz
    from aisle.harness.rollout import CAMERA_QUEUE_DEPTH

    for stream in ("rgb_overhead", "depth_overhead", "rgb_wrist"):
        assert node["inputs"][stream]["queue_size"] == CAMERA_QUEUE_DEPTH, stream
    assert CAMERA_QUEUE_DEPTH >= 400


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


def test_stale_joint_state_is_not_attached_to_a_wrist_frame():
    """VER-8: no EE pose, no trustworthy wrist ROI. The node fell behind
    during a 3-5 s judge on the live run and dropped `joint_state`, so the
    latest pose can describe a different arm configuration than the pixels
    show. A stale pose is dropped rather than used, and `judge_frames` then
    skips that wrist frame instead of projecting in the wrong place."""
    fresh = _buffer()
    fresh.observe_joints(6_000_000_000, np.zeros(9))
    _feed(fresh, 1_000_000_000)
    _feed(fresh, 6_000_000_000)
    fresh.promote_terminal()
    assert 6_000_000_000 in fresh.ee_poses

    stale = _buffer()
    stale.observe_joints(4_000_000_000, np.zeros(9))  # 2 s behind the frame
    _feed(stale, 1_000_000_000)
    _feed(stale, 6_000_000_000)
    stale.promote_terminal()
    assert 6_000_000_000 in stale.frames["wrist"]
    assert 6_000_000_000 not in stale.ee_poses


def test_recorder_subscribes_to_the_realistic_verdicts(tmp_path):
    """The recorder's inputs are built before the sidecar node is appended,
    so the node's own `episode_result` was the one unrecorded endpoint in
    every run (HAR-4 says EVERY wired topic is traced)."""
    import yaml

    from aisle.harness.rollout import instrumented_graph

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    out = instrumented_graph(
        root / "graphs" / "expert_t0.yaml", root, run_dir, verifier="both", episode_timeout_s=60.0
    )
    nodes = {n["id"]: n for n in yaml.safe_load(out.read_text())["nodes"]}

    assert "verifier-realistic__episode_result" in nodes["trace-recorder"]["inputs"]
    assert nodes["verifier-realistic"]["inputs"]["joint_state"]["queue_size"] == 1


def test_a7_mode_rewires_the_loop_to_the_realistic_verdict(tmp_path):
    """A7 (design doc ablation table; Phase-2 DoD): `--verifier realistic`
    drives the LOOP from the realistic verdict — the rollout client and the
    task-state-machine advance on verifier-realistic/episode_result — while
    the ORACLE stays in the graph, held out for scoring: its own
    episode_result endpoint remains recorded so the A7 analysis can compare
    what the loop believed against ground truth. `both` mode is unchanged
    (sidecar only, loop on the oracle)."""
    import yaml

    from aisle.harness.rollout import instrumented_graph

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    out = instrumented_graph(
        root / "graphs" / "expert_t0.yaml",
        root,
        run_dir,
        verifier="realistic",
        episode_timeout_s=60.0,
    )
    doc = yaml.safe_load(out.read_text())
    nodes = {n["id"]: n for n in doc["nodes"]}

    assert "verifier-realistic" in nodes
    assert "verifier-oracle" in nodes  # held out for scoring, not removed
    for consumer in ("rollout-client", "task-state-machine"):
        src = nodes[consumer]["inputs"]["episode_result"]["source"]
        assert src == "verifier-realistic/episode_result", (consumer, src)
    # BOTH verdict streams recorded: the loop's (realistic) and the score's
    recorder = nodes["trace-recorder"]
    assert "verifier-oracle__episode_result" in recorder["inputs"]
    assert "verifier-realistic__episode_result" in recorder["inputs"]
    # the A7 premise inherited from both-mode: no privileged state
    assert "oracle_state" not in nodes["verifier-realistic"]["inputs"]


def test_both_mode_loop_still_advances_on_the_oracle(tmp_path):
    """The A7 rewire must not leak into `both`: its whole design is the
    sidecar judging WITHOUT perturbing control flow."""
    import yaml

    from aisle.harness.rollout import instrumented_graph

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    out = instrumented_graph(
        root / "graphs" / "expert_t0.yaml", root, run_dir, verifier="both", episode_timeout_s=60.0
    )
    nodes = {n["id"]: n for n in yaml.safe_load(out.read_text())["nodes"]}
    for consumer in ("rollout-client", "task-state-machine"):
        src = nodes[consumer]["inputs"]["episode_result"]["source"]
        assert src == "verifier-oracle/episode_result", (consumer, src)
