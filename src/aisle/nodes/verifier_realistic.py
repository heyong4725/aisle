"""Realistic verifier node (SPEC 040 VER-5, increment 1b).

Judges episodes from PIXELS as they run and publishes `episode_result` with
`verifier:"realistic"` in the SAME TC-7 schema the oracle uses. The offline
sibling (`tools/judge_recorded_run.py`) replays recorded frames through the
same `judge_frames`; this is the live path A7 needs, where the loop is
driven by the realistic verdict and the oracle is held out for scoring.

ORACLE-FREE BY CONSTRUCTION, which is the whole point of the ablation: this
node subscribes to camera frames, `joint_state`, `bridge_info`,
`episode_goal` and the client's own `reset` request — never `oracle_state`,
and never the oracle's own `episode_result` (the reset request's TIMING
follows whatever verdict drives the loop, exactly as the next goal's does;
it carries no verdict content). It therefore has to decide when an episode
ENDS by itself: the client's reset request or the next goal bounds it, or
the episode's sim-time budget expires. Waiting for the oracle's verdict
would make the two verifiers agree by construction and VER-6 would measure
nothing.

Frames arrive faster than they are judged, so only VER-9's judged frames
are retained: `CaptureSchedule` picks the last frame at or before each
checkpoint boundary, the same selector the recorder uses, so a live verdict
and a replay of the recorded frames judge the same pixels.

JUDGING IS DEFERRED until the camera streams PROVE the episode's frames
have all arrived (issue #120): this node routinely runs seconds behind
real time (a judge costs 3-5 s and its own queues back up), so the event
that ENDS an episode — the client's reset request, the next episode_goal,
or the sim budget expiring — is regularly processed while the ended
episode's camera frames still sit in the input queues. Judging at that
moment reads a stale terminal frame (measured: 23 stage votes worse than
an offline replay of the same run over 19 episodes), and the still-queued
old frames then land in the NEXT episode's buffer, where the previous
delivery poisons its wrong-object latch — the live twin of the offline
reset-boundary bug this project already fixed once. `EpisodeRouter`
therefore routes every event to its episode BY SIM STAMP and finishes a
closed episode only when both overhead streams have delivered a frame
past its end; dora queues are per-input FIFO, so a later stamp on a
stream proves that stream has no earlier frames left.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from dataclasses import dataclass, field

import numpy as np

from aisle.harness.trace_recorder import CaptureSchedule, decode_frame, retain_capture_frame
from aisle.topics import stamp

# the wrist judged-frame set needs the EE pose at each frame's stamp (VER-8);
# joints arrive at 100 Hz, so only the latest is kept and sampled per frame
# a wrist frame's EE pose must come from a joint_state sampled at
# essentially the SAME instant (VER-8). joint_state is 100 Hz and cameras
# 30 Hz, so a fresh pairing is within ~10 ms; anything beyond this means the
# node fell behind (the live run dropped joint_state during a 3-5 s judge)
# and the pose describes a different arm configuration than the pixels show.
# VER-8's rule is no EE pose, no trustworthy wrist ROI -- so a stale one is
# DROPPED rather than used, and judge_frames skips that wrist frame.
EE_POSE_MAX_SKEW_NS = 50_000_000

# membership is all that matters: routing to buffer keys happens by stream
# name inside retain_capture_frame (review: the old tuple values were dead)
CAMERA_STREAMS = frozenset({"rgb_overhead", "depth_overhead", "rgb_wrist"})


@dataclass
class EpisodeBuffer:
    """One episode's judged frames, accumulated live (VER-9).

    `frames[camera][sim_time_ns]` is exactly the mapping `judge_frames`
    consumes, so the live path and the offline replay share a shape as well
    as a selector.
    """

    goal_id: str
    target_med: str
    start_ns: int
    timeout_ns: int
    schedule: CaptureSchedule
    frames: dict[str, dict[int, dict]] = field(default_factory=dict)
    ee_poses: dict[int, tuple] = field(default_factory=dict)
    # `latest` assembles independently delivered streams; `ready` advances
    # only on a complete overhead pair, so a 30 Hz RGB-only tick cannot evict
    # the 15 Hz pair needed by an at-or-before checkpoint (issue #136).
    latest: dict[str, tuple[int, np.ndarray]] = field(default_factory=dict)
    ready: dict[str, tuple[int, np.ndarray]] = field(default_factory=dict)
    joints: tuple[int, np.ndarray] | None = None

    def observe_joints(self, sim_time_ns: int, qpos: np.ndarray) -> None:
        self.joints = (int(sim_time_ns), np.asarray(qpos, dtype=float))

    def observe_frame(self, stream: str, sim_time_ns: int, frame: np.ndarray) -> bool:
        """Retain a camera payload. Returns True when a boundary was crossed
        and the retained set was promoted to a judged frame."""
        promoted = False
        if self.schedule.crossed(sim_time_ns) and self._promote():
            self.schedule.advance(sim_time_ns)
            promoted = True
        retain_capture_frame(self.latest, self.ready, stream, sim_time_ns, frame)
        return promoted

    def _promote(self) -> bool:
        """Move the retained payloads into the judged set. The overhead pair
        must come from ONE render (BRG-2) or the geometry stages would fuse
        pixels from two ticks — same rule as the recorder."""
        rgb = self.ready.get("rgb_overhead")
        depth = self.ready.get("depth_overhead")
        if rgb is None or depth is None or rgb[0] != depth[0]:
            return False
        if rgb[0] in self.frames.get("overhead", {}):
            # already judged: two checkpoints can legitimately snap to the
            # SAME frame when renders are sparser than the period, and
            # re-adding it would make the promoted/not-promoted return value
            # meaningless. The boundary still advances (the caller does it).
            return True
        self.frames.setdefault("overhead", {})[rgb[0]] = {"rgb": rgb[1], "depth": depth[1]}
        wrist = self.ready.get("rgb_wrist")
        if wrist is not None:
            self.frames.setdefault("wrist", {})[wrist[0]] = {"rgb": wrist[1]}
            if self.joints is not None and abs(self.joints[0] - wrist[0]) <= EE_POSE_MAX_SKEW_NS:
                self.ee_poses[wrist[0]] = ee_pose_from_joints(self.joints[1])
        return True

    def promote_terminal(self) -> bool:
        """VER-9 always judges the terminal frame."""
        return self._promote()

    def trim_after(self, end_ns: int) -> None:
        """Evict everything routed beyond a TIGHTENED end: a delayed reset
        bound can arrive after the next goal already closed this episode
        (cross-input reordering under backlog), and the frames past the
        true end show RST-2 reset motion (PR #168 review)."""
        for per in self.frames.values():
            for stale in [s for s in per if s > end_ns]:
                del per[stale]
        for retained in (self.latest, self.ready):
            for key in [k for k, (s, _) in retained.items() if s > end_ns]:
                del retained[key]
        for stale in [s for s in self.ee_poses if s > end_ns]:
            del self.ee_poses[stale]
        if self.joints is not None and self.joints[0] > end_ns:
            self.joints = None

    def expired(self, sim_time_ns: int) -> bool:
        return sim_time_ns - self.start_ns >= self.timeout_ns

    def judgeable(self) -> bool:
        return bool(self.frames.get("overhead"))


@dataclass
class ClosingEpisode:
    """An ended episode awaiting its judge: frames with stamps inside
    (buffer.start_ns, end_ns] may still be queued behind the event that
    ended it, so it stays open for routing until the streams pass end_ns."""

    buffer: EpisodeBuffer
    end_ns: int
    failure: str
    attempts: int = 0  # finishing attempts (a finish error must not LOSE it)


# the streams whose progress PROVES an ended episode's frames have arrived:
# the terminal judged frame is the last complete overhead pair, so both
# halves must have delivered past the boundary. The wrist never gates —
# it is corroborating evidence (VER-13), and a lagging optional stream
# must not delay every verdict.
GATING_STREAMS = ("rgb_overhead", "depth_overhead")
# liveness net (PR review): if ONE gating stream dies mid-run, its high-water
# freezes and no verdict would ever publish (in A7 the client then hangs
# until the wall clamp kills the run). Once the OTHER stream has advanced
# this far past a closed episode's end, the laggard is presumed dead and the
# episode is judged with the pairs it has — safe at 5 s because both streams
# drain in the SAME event loop: if the consumer processed one stream 5 sim-s
# past the end, the sibling's (fewer) queued events drained with it unless
# that stream genuinely stopped.
STREAM_STALL_SLACK_NS = 5_000_000_000
# second net, for BOTH streams frozen (renderer death): the sim clock is read
# from joint_state, which is latest-wins and therefore runs AHEAD of the
# backlogged camera queues by up to their full depth (~13 s sim at 400 deep,
# 30 Hz) — a 5 s slack here would judge early with stale frames under a
# healthy stacked-judge backlog (review P1). 30 s exceeds any state the
# queues can even HOLD: cameras silent for 30 sim-s are dead or overflowed,
# and overflow voids the arrival proof anyway.
SIM_CLOCK_STALL_SLACK_NS = 30_000_000_000


class EpisodeRouter:
    """Stamp-routed episode lifecycle for the live node (issue #120).

    Events carry sim stamps; episodes own stamp windows. A frame is routed
    to the episode whose window contains it — never to whichever buffer
    happens to be current when the backlogged event is finally processed —
    and an ended episode is judged only once both overhead streams deliver
    a frame past its end (per-input FIFO makes that a proof of arrival).
    Frames between an episode's end and the next episode's start (reset
    motion under RST-2, the boundary render) belong to NO episode and are
    dropped, matching the offline judge's strictly-after-reset windowing.

    `finish_fn(buffer, end_ns, failure)` is injected: production judges and
    publishes; tests record. The router promotes the terminal frame itself,
    so the callback receives a buffer whose judged set is complete."""

    def __init__(self, period_ns: int, timeout_ns: int, finish_fn):
        self.period_ns = int(period_ns)
        self.timeout_ns = int(timeout_ns)
        self.finish_fn = finish_fn
        self.current: EpisodeBuffer | None = None
        self.closing: list[ClosingEpisode] = []
        self.high_water: dict[str, int] = {}
        # the newest sim stamp seen on ANY routed event — the non-camera
        # clock the second liveness net compares against when BOTH gating
        # streams freeze (renderer death: joint_state keeps flowing at
        # 100 Hz while no camera event ever advances high_water; review)
        self.last_seen_ns = 0

    def on_goal(self, goal_id: str, target_med: str, start_ns: int) -> None:
        """A new episode begins at `start_ns` (the goal's reset_sim_ns — the
        teleport's own sim time, not this node's possibly-stale read of the
        goal event). If the reset request was never seen, the previous
        episode closes here, bounded at the new episode's start."""
        if self.current is not None:
            self._close(self.current, start_ns, "never_delivered")
        schedule = CaptureSchedule(self.period_ns)
        schedule.start(start_ns)
        self.current = EpisodeBuffer(
            goal_id=goal_id,
            target_med=target_med,
            start_ns=int(start_ns),
            timeout_ns=self.timeout_ns,
            schedule=schedule,
        )
        self._resolve()

    def on_reset(self, end_ns: int) -> None:
        """The client's reset request ends the running episode — its own
        action signal, not the oracle's verdict, stamped with the sim time
        of the result that triggered it. Bounding here (rather than at the
        next goal) keeps RST-2 behavioral-reset motion out of the ended
        episode's window: those frames show the med being picked back OUT
        of the tray. A stamp at or before the episode's start (an
        unstamped request under backlog) is IGNORED — collapsing the
        window to empty would drop every frame; the next goal bounds the
        episode instead (review). A DELAYED reset — dequeued after the
        next goal already closed the episode (cross-input reordering) —
        TIGHTENS the matching closing window and evicts what was routed
        beyond it, or the reset motion stays in the ended episode
        (review P1)."""
        end_ns = int(end_ns)
        self.last_seen_ns = max(self.last_seen_ns, end_ns)
        if self.current is not None and end_ns > self.current.start_ns:
            self._close(self.current, end_ns, "never_delivered")
        else:
            for closing in self.closing:
                if closing.buffer.start_ns < end_ns < closing.end_ns:
                    closing.end_ns = end_ns
                    closing.buffer.trim_after(end_ns)
                    break
        self._resolve()

    def on_joints(self, sim_ns: int, qpos: np.ndarray) -> None:
        self.last_seen_ns = max(self.last_seen_ns, int(sim_ns))
        owner = self._owner(sim_ns)
        if owner is not None:
            owner.observe_joints(sim_ns, qpos)
        self._maybe_expire(sim_ns)
        self._resolve()

    def on_frame(self, stream: str, sim_ns: int, frame: np.ndarray | None) -> None:
        """A camera event advances the stream clocks even when its payload
        did not decode (frame None): the stamp still proves delivery
        progress, and the sim-budget expiry must keep firing or an
        undecodable stream would stall the loop forever (PR review)."""
        self.last_seen_ns = max(self.last_seen_ns, int(sim_ns))
        if stream in GATING_STREAMS:
            self.high_water[stream] = max(self.high_water.get(stream, 0), int(sim_ns))
        if frame is not None:
            owner = self._owner(sim_ns)
            if owner is not None:
                owner.observe_frame(stream, sim_ns, frame)
        self._maybe_expire(sim_ns)
        self._resolve()

    def _maybe_expire(self, sim_ns: int) -> None:
        """The sim budget is this node's own end signal (A7 cannot wait for
        the oracle). Checked on joints TOO (review): if every camera stream
        stops while the sim runs on, the expiry must still fire or no
        verdict ever publishes."""
        if self.current is not None and self.current.expired(sim_ns):
            self._close(self.current, self.current.start_ns + self.timeout_ns, "timeout")

    def flush(self, last_sim_ns: int) -> None:
        """Teardown: judge everything still open, oldest first — the LAST
        episode has no next goal, so this is its only exit (and losing it
        was a 19-sidecars-for-20-episodes run). `_close` owns the window
        clamping."""
        if self.current is not None:
            self._close(self.current, last_sim_ns, "never_delivered")
        for closing in self.closing:
            try:
                self._finish(closing)
            except Exception as exc:  # noqa: BLE001 — teardown cannot retry;
                # one failed episode must not lose the ones behind it
                print(
                    f"verifier-realistic: flush failed for {closing.buffer.goal_id}: {exc!r}",
                    file=sys.stderr,
                )
        self.closing.clear()

    def _close(self, buffer: EpisodeBuffer, end_ns: int, failure: str) -> None:
        # an episode's window can never outlive its own sim budget: a goal
        # or reset arriving late (camera stall, backlog) must not stretch
        # the window past where the budget — and the offline judge — end it.
        # An episode that REACHED its budget is a timeout, whichever event
        # happened to close it.
        budget_end = buffer.start_ns + self.timeout_ns
        if int(end_ns) >= budget_end:
            failure = "timeout"
        end_ns = max(buffer.start_ns, min(int(end_ns), budget_end))
        self.closing.append(ClosingEpisode(buffer, end_ns, failure))
        self.current = None

    def _owner(self, sim_ns: int) -> EpisodeBuffer | None:
        """The episode whose window strictly-after-start, at-or-before-end
        contains `sim_ns`. The strict lower bound is the offline judge's
        reset-boundary rule, live: the frame AT the reset stamp still shows
        the PREVIOUS scene (the render happens before the teleport)."""
        for closing in self.closing:
            if closing.buffer.start_ns < sim_ns <= closing.end_ns:
                return closing.buffer
        current = self.current
        if current is not None and current.start_ns < sim_ns <= current.start_ns + self.timeout_ns:
            return current
        return None

    def _resolve(self) -> None:
        """Judge closed episodes, oldest first, once both gating streams
        have delivered past their end — publish order is episode order.
        Two liveness nets keep verdicts flowing when the proof cannot
        complete (PR review): if ONE stream's clock froze while the other
        ran STREAM_STALL_SLACK_NS past the end, the laggard is presumed
        dead; if BOTH froze (renderer death) while the sim demonstrably
        ran on — a routed event stamp passed end + SIM_CLOCK_STALL_SLACK_NS,
        a slack sized ABOVE anything the camera queues can hold, since the
        joint-state clock legitimately runs ahead of backlogged cameras —
        the episode is judged with what it has rather than never. Without
        the second net, `both` mode (loop on the oracle, cameras irrelevant
        to it) would pile up unjudged episodes for a whole campaign."""
        while self.closing:
            closing = self.closing[0]
            end_ns = closing.end_ns
            arrived = min(self.high_water.get(stream, 0) for stream in GATING_STREAMS)
            furthest = max(self.high_water.get(stream, 0) for stream in GATING_STREAMS)
            if arrived <= end_ns:
                if (
                    furthest <= end_ns + STREAM_STALL_SLACK_NS
                    and self.last_seen_ns <= end_ns + SIM_CLOCK_STALL_SLACK_NS
                ):
                    return
                stalled = sorted(s for s in GATING_STREAMS if self.high_water.get(s, 0) <= end_ns)
                print(
                    f"verifier-realistic: {', '.join(stalled)} stalled "
                    f"(high water {[self.high_water.get(s, 0) for s in stalled]} ns) while "
                    f"the run reached {max(furthest, self.last_seen_ns)} ns — judging "
                    f"{closing.buffer.goal_id} without waiting (liveness net)",
                    file=sys.stderr,
                )
            # never LOSE an episode to a finishing error (review P2): keep
            # it queued for ONE retry on the next event, then drop loudly so
            # a persistent error cannot wedge every later verdict behind it
            closing.attempts += 1
            try:
                self._finish(closing)
            except Exception:
                if closing.attempts >= 2:
                    self.closing.pop(0)
                    print(
                        f"verifier-realistic: dropping {closing.buffer.goal_id} after "
                        f"{closing.attempts} failed finishing attempts — no verdict "
                        "published for it",
                        file=sys.stderr,
                    )
                raise  # the caller's per-event handler logs the cause
            self.closing.pop(0)

    def _finish(self, closing: ClosingEpisode) -> None:
        closing.buffer.promote_terminal()
        self.finish_fn(closing.buffer, closing.end_ns, closing.failure)


def goal_start_ns(goal: dict, fallback_ns: int) -> int:
    """The episode's authoritative start: the goal payload's reset_sim_ns
    (the teleport's own sim time, BRG-4), presence-checked rather than
    truthiness-checked — under ADR-25 the FIRST reset legitimately lands at
    sim 0, and `or`-chaining discarded it (PR review). The goal EVENT's
    stamp is only the fallback for goals that predate the field."""
    return int(goal["reset_sim_ns"]) if "reset_sim_ns" in goal else int(fallback_ns)


def ee_pose_from_joints(qpos: np.ndarray) -> tuple:
    """(pos, quat_xyzw) of the EE from FK (VER-8), for the wrist ROI. The
    repo's branch-stable conversion, not a local one: a naive
    `w = sqrt(1 + trace)/2` collapses 180-degree rotations to identity and a
    top-down grasp sits at that pose (PR #104 review round 5)."""
    from aisle.nodes.budget_guard import fk_flange
    from aisle.verifier.calibration import quat_xyzw_from_rotation

    pos, rot = fk_flange(np.asarray(qpos, dtype=float)[:7])
    return (pos, quat_xyzw_from_rotation(rot))


def result_metadata(record: dict, end_ns: int, seq: int) -> dict:
    """TC-2's mandatory keys on the published verdict, now that A7
    consumers READ them: the client stamps its next reset request with this
    sim time, which bounds the ended episode's frame window (PR #168
    review — an unstamped result made the reset bounding silently inert in
    exactly the A7 mode issue #120 targets)."""
    return stamp({"sim_time_ns": int(end_ns), "goal_id": record["goal_id"]}, seq)


def episode_result(goal_id: str, success: bool, failure: str | None, t_end_s: float) -> dict:
    """TC-7's schema, unchanged, with `verifier:"realistic"` — the oracle's
    field names so `harness/fidelity.py` and the rollout runner need no
    special case (VER-5). Failure-class attribution stays the ORACLE's
    (VER-3); this field is informative and never compared classwise."""
    return {
        "status": "success" if success else "fail",
        "failure": None if success else failure,
        "t_end": round(t_end_s, 2),
        "goal_id": goal_id,
        "verifier": "realistic",
    }


def main() -> None:  # pragma: no cover — dora runtime
    import pyarrow as pa
    from dora import Node

    from aisle.scenes.pharmacy import load_meds, load_physics, wrist_mount_rotation
    from aisle.verifier.calibration import build_calibration_v1
    from aisle.verifier.oracle import build_judge_cfg, load_thresholds
    from aisle.verifier.realistic import judge_frames

    meds, physics = load_meds(), load_physics()
    cam_cfg = physics["cameras"]
    thresholds = load_thresholds()
    period_ns = int(float(thresholds["realistic"]["checkpoint_period_s"]) * 1e9)
    timeout_ns = int(float(os.environ.get("AISLE_TIMEOUT_S", "60")) * 1e9)
    cfg = build_judge_cfg(
        physics,
        meds,
        os.environ.get("AISLE_EMBODIMENT", "franka"),
        timeout_s=timeout_ns / 1e9,
        initial_positions=[(0.0, 0.0, 0.0)] * len(meds),
        robot_home_error_rad=0.0,
    )
    home = np.asarray(physics["embodiment"]["franka"]["home_qpos"], dtype=float)
    nominal = build_calibration_v1(
        cam_cfg["overhead_pos"],
        cam_cfg["overhead_lookat"],
        (640, 480),
        55.0,
        cam_cfg["wrist_offset_m"],
        (320, 240),
        70.0,
        wrist_mount_rotation(cam_cfg),
    )
    nominal["_overhead_lookat"] = cam_cfg["overhead_lookat"]

    published: dict | None = None
    node = Node()

    result_seq = 0

    def publish(record: dict, end_ns: int) -> None:
        nonlocal result_seq
        result_seq += 1
        node.send_output(
            "episode_result",
            pa.array([json.dumps(record)]),
            result_metadata(record, end_ns, result_seq),
        )

    def finish(ep: EpisodeBuffer, end_ns: int, failure: str) -> None:
        """Judge a COMPLETE buffer (the router already promoted the terminal
        frame and proved the streams passed end_ns) and publish."""
        success = False
        if published is not None and ep.judgeable():
            success, _ = judge_frames(
                goal_id=ep.goal_id,
                target_med=ep.target_med,
                med_names=list(meds),
                med_sizes={name: spec["size"] for name, spec in meds.items()},
                frames=ep.frames,
                calibration=published,
                nominal_calibration=nominal,
                jitter_bound_m=physics["domain_randomization"]["camera_jitter_m"],
                tray_min=cfg.tray_min,
                tray_max=cfg.tray_max,
                joint_state=ep.joints[1] if ep.joints else home,
                home_qpos=home,
                thresholds=thresholds,
                ee_poses=ep.ee_poses,
                run_dir=os.environ.get("AISLE_RESULTS_DIR"),
            )
        publish(
            episode_result(
                ep.goal_id,
                success,
                None if success else failure,
                (end_ns - ep.start_ns) / 1e9,
            ),
            end_ns,
        )

    router = EpisodeRouter(period_ns, timeout_ns, finish)
    last_sim_time_ns = 0

    def dispatch(event: dict) -> None:
        nonlocal published, last_sim_time_ns
        topic, metadata = event["id"], (event.get("metadata") or {})
        sim_time_ns = int(metadata.get("sim_time_ns", 0)) or last_sim_time_ns
        last_sim_time_ns = max(last_sim_time_ns, sim_time_ns)
        if topic == "bridge_info":
            published = json.loads(event["value"][0].as_py())["calibration"]
        elif topic == "episode_goal":
            # goal_id rides the METADATA, not the payload (TC-7's goal
            # pattern, set by the rollout client as ep-NNNN). Reading it
            # from the payload silently produced ids like
            # "ep-21370000000", and VER-6 correlates realistic records to
            # oracle episodes BY goal_id -- so a wrong one makes the
            # comparison quietly EMPTY rather than obviously wrong.
            goal_id = metadata.get("goal_id")
            if not goal_id:
                print("episode_goal without goal_id: cannot correlate (TC-7)", file=sys.stderr)
                return
            goal = json.loads(event["value"][0].as_py())
            router.on_goal(goal_id, goal["target_med"], goal_start_ns(goal, sim_time_ns))
        elif topic == "reset":
            # the client's own reset request (stamped with the sim time
            # of the result that triggered it) ends the running episode
            # BEFORE any reset motion enters the frames (issue #120)
            router.on_reset(sim_time_ns)
        elif topic == "joint_state":
            router.on_joints(sim_time_ns, np.asarray(event["value"].to_numpy(zero_copy_only=False)))
        elif topic in CAMERA_STREAMS:
            # UNCONDITIONAL: a payload that fails to decode still passes
            # None so the router advances its clocks — gating here made
            # the undecodable-stream liveness fix unreachable (review)
            router.on_frame(topic, sim_time_ns, decode_frame(metadata, event["value"]))

    # judging the LAST episode takes seconds, and the runner tears the
    # dataflow down as soon as the client exits -- without this the final
    # episode loses the race and every run silently drops one record from
    # its VER-6 comparison (observed: 2 records for 3 episodes)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        for event in node:
            if event["type"] != "INPUT":
                continue
            try:
                dispatch(event)
            except Exception as exc:  # noqa: BLE001 — the verdict source is
                # a trust boundary (review): one malformed payload (bad
                # JSON, missing target_med, non-numeric stamp) must drop
                # THAT event loudly, never kill the node — a dead verifier
                # hangs the A7 loop until the wall clamp
                print(
                    f"verifier-realistic: dropping malformed {event['id']} event: {exc!r}",
                    file=sys.stderr,
                )
    finally:
        router.flush(last_sim_time_ns)


if __name__ == "__main__":  # pragma: no cover
    main()
