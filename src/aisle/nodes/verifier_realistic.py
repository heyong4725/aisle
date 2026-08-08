"""Realistic verifier node (SPEC 040 VER-5, increment 1b).

Judges episodes from PIXELS as they run and publishes `episode_result` with
`verifier:"realistic"` in the SAME TC-7 schema the oracle uses. The offline
sibling (`tools/judge_recorded_run.py`) replays recorded frames through the
same `judge_frames`; this is the live path A7 needs, where the loop is
driven by the realistic verdict and the oracle is held out for scoring.

ORACLE-FREE BY CONSTRUCTION, which is the whole point of the ablation: this
node subscribes to camera frames, `joint_state`, `bridge_info` and
`episode_goal` — never `oracle_state`, and never the oracle's own
`episode_result`. It therefore has to decide when an episode ENDS by
itself: success the moment a judged frame fuses to success (VER-13), or
failure when the episode's sim-time budget expires. Waiting for the
oracle's verdict would make the two verifiers agree by construction and
VER-6 would measure nothing.

Frames arrive faster than they are judged, so only VER-9's judged frames
are retained: `CaptureSchedule` picks the last frame at or before each
checkpoint boundary, the same selector the recorder uses, so a live verdict
and a replay of the recorded frames judge the same pixels.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from dataclasses import dataclass, field

import numpy as np

from aisle.harness.trace_recorder import CaptureSchedule, decode_frame

# the wrist judged-frame set needs the EE pose at each frame's stamp (VER-8);
# joints arrive at 100 Hz, so only the latest is kept and sampled per frame
CAMERA_STREAMS = {
    "rgb_overhead": ("overhead", "rgb"),
    "depth_overhead": ("overhead", "depth"),
    "rgb_wrist": ("wrist", "rgb"),
}


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
    latest: dict[str, tuple[int, np.ndarray]] = field(default_factory=dict)
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
        self.latest[stream] = (int(sim_time_ns), frame)
        return promoted

    def _promote(self) -> bool:
        """Move the retained payloads into the judged set. The overhead pair
        must come from ONE render (BRG-2) or the geometry stages would fuse
        pixels from two ticks — same rule as the recorder."""
        rgb = self.latest.get("rgb_overhead")
        depth = self.latest.get("depth_overhead")
        if rgb is None or depth is None or rgb[0] != depth[0]:
            return False
        if rgb[0] in self.frames.get("overhead", {}):
            # already judged: two checkpoints can legitimately snap to the
            # SAME frame when renders are sparser than the period, and
            # re-adding it would make the promoted/not-promoted return value
            # meaningless. The boundary still advances (the caller does it).
            return True
        self.frames.setdefault("overhead", {})[rgb[0]] = {"rgb": rgb[1], "depth": depth[1]}
        wrist = self.latest.get("rgb_wrist")
        if wrist is not None:
            self.frames.setdefault("wrist", {})[wrist[0]] = {"rgb": wrist[1]}
            if self.joints is not None:
                self.ee_poses[wrist[0]] = ee_pose_from_joints(self.joints[1])
        return True

    def promote_terminal(self) -> bool:
        """VER-9 always judges the terminal frame."""
        return self._promote()

    def expired(self, sim_time_ns: int) -> bool:
        return sim_time_ns - self.start_ns >= self.timeout_ns

    def judgeable(self) -> bool:
        return bool(self.frames.get("overhead"))


def ee_pose_from_joints(qpos: np.ndarray) -> tuple:
    """(pos, quat_xyzw) of the EE from FK (VER-8), for the wrist ROI. The
    repo's branch-stable conversion, not a local one: a naive
    `w = sqrt(1 + trace)/2` collapses 180-degree rotations to identity and a
    top-down grasp sits at that pose (PR #104 review round 5)."""
    from aisle.nodes.budget_guard import fk_flange
    from aisle.verifier.calibration import quat_xyzw_from_rotation

    pos, rot = fk_flange(np.asarray(qpos, dtype=float)[:7])
    return (pos, quat_xyzw_from_rotation(rot))


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
    episode: EpisodeBuffer | None = None
    node = Node()

    def publish(record: dict) -> None:
        node.send_output("episode_result", pa.array([json.dumps(record)]))

    def finish(ep: EpisodeBuffer, sim_time_ns: int, failure: str) -> None:
        ep.promote_terminal()
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
                (sim_time_ns - ep.start_ns) / 1e9,
            )
        )

    # judging the LAST episode takes seconds, and the runner tears the
    # dataflow down as soon as the client exits -- without this the final
    # episode loses the race and every run silently drops one record from
    # its VER-6 comparison (observed: 2 records for 3 episodes)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    last_sim_time_ns = 0
    try:
        for event in node:
            if event["type"] != "INPUT":
                continue
            topic, metadata = event["id"], (event.get("metadata") or {})
            sim_time_ns = int(metadata.get("sim_time_ns", 0)) or last_sim_time_ns
            last_sim_time_ns = max(last_sim_time_ns, sim_time_ns)
            if topic == "bridge_info":
                published = json.loads(event["value"][0].as_py())["calibration"]
            elif topic == "episode_goal":
                # the arrival of the NEXT goal is this node's episode-end
                # signal. It cannot wait for the oracle's episode_result (A7
                # holds the oracle out), and its own sim-time budget never
                # fires on an episode the robot finishes early -- which is
                # every successful one, so the first live run judged NOTHING.
                if episode is not None:
                    finish(episode, sim_time_ns, "never_delivered")
                    episode = None
                # goal_id rides the METADATA, not the payload (TC-7's goal
                # pattern, set by the rollout client as ep-NNNN). Reading it
                # from the payload silently produced ids like
                # "ep-21370000000", and VER-6 correlates realistic records to
                # oracle episodes BY goal_id -- so a wrong one makes the
                # comparison quietly EMPTY rather than obviously wrong.
                goal_id = metadata.get("goal_id")
                if not goal_id:
                    print("episode_goal without goal_id: cannot correlate (TC-7)", file=sys.stderr)
                    continue
                goal = json.loads(event["value"][0].as_py())
                schedule = CaptureSchedule(period_ns)
                schedule.start(sim_time_ns)
                episode = EpisodeBuffer(
                    goal_id=goal_id,
                    target_med=goal["target_med"],
                    start_ns=sim_time_ns,
                    timeout_ns=timeout_ns,
                    schedule=schedule,
                )
            elif topic == "joint_state" and episode is not None:
                episode.observe_joints(
                    sim_time_ns, np.asarray(event["value"].to_numpy(zero_copy_only=False))
                )
            elif topic in CAMERA_STREAMS and episode is not None:
                frame = decode_frame(metadata, event["value"])
                if frame is not None:
                    episode.observe_frame(topic, sim_time_ns, frame)
                if episode.expired(sim_time_ns):
                    finish(episode, sim_time_ns, "timeout")
                    episode = None
    finally:
        if episode is not None:
            finish(episode, last_sim_time_ns, "never_delivered")


if __name__ == "__main__":  # pragma: no cover
    main()
