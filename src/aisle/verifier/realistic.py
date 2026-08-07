"""Realistic verifier — the five-stage judge (SPEC 040 VER-5, VER-8..14).

Stage 0 calibration (VER-8) -> per-camera identity (VER-9) -> overhead
containment (VER-10) -> upright (VER-11) -> home (VER-12), fused
explicitly (VER-13) and recorded per episode in the VER-14 sidecar.

The judge core here is PURE: stages arrive as votes, so the fusion, the
episode-scoped wrong-object latch, and the sidecar shape are testable
without torch or sim (CON-12). Model-bearing stage implementations live
in `stages.py` and are injected.

Failure-class attribution stays the ORACLE's (VER-3); this verifier
publishes a Boolean success bit plus the stage record.
"""

from __future__ import annotations

import json
from collections.abc import Callable  # noqa: TC003 — used in a runtime annotation
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# VER-13: the exact stage set the fusion requires. A missing stage is a
# FAIL, not an implicit pass — dropping a stage must never loosen a
# verdict.
STAGES = (
    "calibration",
    "identity_overhead",
    "identity_wrist",
    "containment",
    "upright",
    "home",
)
CAMERAS = ("overhead", "wrist")
SIDECAR_NAME = "verifier_stages.jsonl"


@dataclass(frozen=True)
class StageVote:
    """One stage's verdict. `status` is pass | fail | error — error is a
    stage that could not judge (missing frame, VER-8 refusal, model
    exception) and fails the episode closed (VER-13)."""

    status: str
    measurement: dict | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("pass", "fail", "error"):
            raise ValueError(f"invalid stage status {self.status!r}")


@dataclass
class EpisodeJudge:
    """Accumulates judged frames for ONE episode (goal_id-keyed) and
    holds the VER-9 wrong-object latch.

    The latch is the time-spanning half of VER-3's safety asymmetry: the
    moment any judged frame from EITHER camera sees a non-target med in
    the tray, identity stays failed for the rest of the episode — so a
    transient (wrong item enters, is removed, target delivered) cannot
    fuse to success on a clean terminal frame.
    """

    goal_id: str
    frames: dict[str, list[dict]] = field(default_factory=lambda: {c: [] for c in CAMERAS})
    latched: bool = False
    first_latch_event: dict | None = None

    def observe(self, camera: str, frame: dict) -> None:
        """Record one judged frame (checkpoint or terminal) for a camera.
        `frame`: {sim_time_ns, per_class_scores, target_in_tray,
        non_target_in_tray} — see VER-14."""
        if camera not in CAMERAS:
            raise ValueError(f"unknown camera {camera!r}")
        self.frames[camera].append(frame)
        if frame.get("non_target_in_tray") and not self.latched:
            self.latched = True
            offenders = {
                k: v
                for k, v in (frame.get("per_class_scores") or {}).items()
                if k != frame.get("target_med")
            }
            self.first_latch_event = {
                "sim_time_ns": frame["sim_time_ns"],
                "camera": camera,
                "med_class": max(offenders, key=offenders.get) if offenders else None,
            }

    def identity_vote(self, camera: str) -> StageVote:
        """VER-9: pass iff this camera saw the TARGET in the tray on some
        judged frame AND the episode latch is clear."""
        frames = self.frames[camera]
        # ORDER MATTERS: a set latch is a KNOWN wrong-object finding and
        # dominates missing evidence on this camera — VER-3's safety
        # asymmetry outranks unable-to-judge. Only with the latch clear
        # is an empty camera an error (VER-13, PR #103 review round 3).
        if self.latched:
            return StageVote(
                "fail",
                detail="wrong-object latch set (VER-9)",
                measurement={"latched_at_ns": self.first_latch_event["sim_time_ns"]},
            )
        if not frames:
            # VER-13: missing camera evidence is UNABLE TO JUDGE, not a
            # negative finding — the Boolean verdict is false either way,
            # but the sidecar and D5 stage attribution must say which
            # (PR #103 review round 3)
            return StageVote("error", detail=f"{camera}: no judged frames")
        if not any(f.get("target_in_tray") for f in frames):
            return StageVote("fail", detail=f"{camera}: target never detected in tray")
        return StageVote("pass")


def fuse(votes: dict[str, StageVote]) -> bool:
    """VER-13: realistic success iff EVERY stage passes. Missing stages
    and `error` statuses both fail closed."""
    return all(votes.get(stage, StageVote("error")).status == "pass" for stage in STAGES)


def sidecar_record(judge: EpisodeJudge, votes: dict[str, StageVote]) -> dict:
    """VER-14: the per-episode stage record. The identity stages carry
    the full judged-frame timeline; the others carry their measurement.
    `harness/fidelity.py` consumes exactly this shape."""
    stages: dict[str, dict] = {}
    for stage in STAGES:
        vote = votes.get(stage, StageVote("error", detail="stage not run"))
        entry: dict = {"vote": vote.status}
        if stage.startswith("identity_"):
            entry["frames"] = list(judge.frames[stage.removeprefix("identity_")])
        if vote.measurement is not None:
            entry["measurement"] = vote.measurement
        if vote.detail is not None:
            entry["detail"] = vote.detail
        stages[stage] = entry
    return {
        "goal_id": judge.goal_id,
        "verifier": "realistic",
        "latch": {"latched": judge.latched, "first_event": judge.first_latch_event},
        "stages": stages,
    }


def append_sidecar(run_dir: Path, record: dict) -> Path:
    """One JSON object per judged episode, appended (VER-14)."""
    path = Path(run_dir) / SIDECAR_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return path


def checkpoint_stamps(
    start_ns: int, end_ns: int, period_s: float, frames_available: list[int] | None = None
) -> list[int]:
    """VER-9's judged-frame set: checkpoints every `period_s` from goal
    receipt PLUS the terminal frame. With `frames_available`, each stamp
    snaps to the nearest available frame at or before it (renders are
    rate-limited, BRG-2 — the exact stamp rarely exists), and the
    terminal frame is always judged."""
    if end_ns < start_ns:
        raise ValueError("episode ends before it starts")
    period_ns = int(period_s * 1e9)
    wanted = list(range(start_ns, end_ns, period_ns)) + [end_ns] if period_ns > 0 else [end_ns]
    if frames_available is None:
        return sorted(set(wanted))
    available = sorted(frames_available)
    if not available:
        return []
    snapped = []
    for stamp in wanted:
        earlier = [f for f in available if f <= stamp]
        snapped.append(earlier[-1] if earlier else available[0])
    snapped.append(available[-1])  # the terminal frame is always judged
    return sorted(set(snapped))


def judge_episode(
    goal_id: str,
    target_med: str,
    frames: dict[str, dict[int, dict]],
    detect: Callable[[str, dict, int], list[dict]],
    stage_votes: dict[str, StageVote],
    period_s: float,
    min_score: float,
    roi: dict[str, tuple],
    run_dir: Path | None = None,
) -> tuple[bool, dict]:
    """One episode's realistic verdict (VER-5): run the identity stage
    over the judged-frame set per camera, fuse with the non-identity
    stage votes the caller computed, and write the VER-14 sidecar.

    `frames[camera][sim_time_ns]` holds the camera's RGB payloads;
    `detect(camera, payload, sim_time_ns) -> [{label, score, box}]` is
    the injected model call (models.load_pinned on the real path, a
    recorded fixture in tests). Returns (success, sidecar_record).
    """
    from aisle.verifier.stages import identity_frame

    judge = EpisodeJudge(goal_id=goal_id)
    for camera in CAMERAS:
        available = sorted(frames.get(camera, {}))
        if not available:
            continue
        for stamp in checkpoint_stamps(available[0], available[-1], period_s, available):
            detections = detect(camera, frames[camera][stamp], stamp)
            judge.observe(
                camera,
                identity_frame(detections, target_med, roi[camera], min_score, stamp),
            )
    votes = dict(stage_votes)
    votes["identity_overhead"] = judge.identity_vote("overhead")
    votes["identity_wrist"] = judge.identity_vote("wrist")
    record = sidecar_record(judge, votes)
    record["success"] = fuse(votes)
    if run_dir is not None:
        append_sidecar(run_dir, record)
    return record["success"], record


def judge_frames(
    goal_id: str,
    target_med: str,
    med_names: list[str],
    med_sizes: dict[str, list[float]],
    frames: dict[str, dict[int, dict]],
    calibration: dict,
    nominal_calibration: dict,
    jitter_bound_m: float,
    tray_min,
    tray_max,
    joint_state,
    home_qpos,
    thresholds: dict,
    ee_poses: dict[int, tuple] | None = None,
    run_dir: Path | None = None,
    grounding_point: tuple | None = None,
) -> tuple[bool, dict]:
    """The composed five-stage verdict from RAW inputs (VER-5).

    Production entry point: stage 0 (VER-8), the pinned models via the
    CPU-disciplined adapters, the geometry stages, the VER-13 fusion and
    the VER-14 sidecar. The remaining wiring — a dora node subscribing to
    the camera topics and publishing `episode_result` with
    `verifier:"realistic"` — is increment 1b; this is what that node will
    call and what `harness/fidelity.py` replays offline.

    The segmenter is GROUNDED on the terminal overhead detection of the
    target (PR #103 review round 2: prompting at the image centre
    segmented whatever sat in the middle of the frame, so containment and
    upright judged the wrong object). No sufficiently-scored target
    detection means no grounded geometry: those stages record `error` and
    the episode fails closed rather than measuring something arbitrary.
    `grounding_point` overrides the detection-derived seed for tests that
    isolate the geometry from detector quality.

    EVERY model and geometry call runs inside an exception boundary
    (VER-13): a raised model/geometry error becomes that stage's `error`
    vote, a False verdict, and a written sidecar — never a traceback out
    of the judge.
    """
    from aisle.verifier.calibration import calibration_report
    from aisle.verifier.models import detect_meds, load_pinned, segment_mask
    from aisle.verifier.stages import (
        backproject_overhead,
        containment_vote,
        crop_to_roi,
        dominant_surface,
        home_vote,
        identity_frame,
        shift_detections,
        tray_roi_pixels,
        upright_vote,
    )

    judge = EpisodeJudge(goal_id=goal_id)
    realistic_cfg = thresholds["realistic"]
    success_cfg = thresholds["success"]
    votes: dict[str, StageVote] = {}

    def finish() -> tuple[bool, dict]:
        record = sidecar_record(judge, votes)
        record["success"] = fuse(votes)
        if run_dir is not None:
            append_sidecar(run_dir, record)
        return record["success"], record

    def independent_home() -> None:
        """VER-12 needs no pixels and no models, so it is computed even
        when everything upstream failed — per-stage attribution is what
        VER-14/D5 consume (PR #103 review round 3)."""
        try:
            votes["home"] = home_vote(
                joint_state, home_qpos, success_cfg["robot_home_tolerance_rad"]
            )
        except Exception as exc:  # noqa: BLE001 — VER-13
            votes["home"] = StageVote(
                "error", detail=f"home stage raised {type(exc).__name__}: {exc}"
            )

    # ---- stage 0 (VER-8) --------------------------------------------
    # calibration_report is itself fail-closed on absent/malformed blocks
    try:
        refusal, deviations = calibration_report(calibration, nominal_calibration, jitter_bound_m)
    except Exception as exc:  # noqa: BLE001 — VER-13, belt and braces
        refusal, deviations = f"calibration check raised {type(exc).__name__}: {exc}", {}
    if refusal is not None:
        votes["calibration"] = StageVote("error", measurement=deviations, detail=refusal)
        for stage in ("identity_overhead", "identity_wrist", "containment", "upright"):
            votes[stage] = StageVote("error", detail="calibration refused: frames not judged")
        independent_home()
        return finish()
    votes["calibration"] = StageVote("pass", measurement=deviations)

    # ---- stage 1 identity, PER CAMERA (VER-9) -----------------------
    terminal_detections: list[dict] = []
    identity_pair = None
    try:
        identity_pair = load_pinned("identity")
    except Exception as exc:  # noqa: BLE001 — VER-13
        detail = f"identity model load raised {type(exc).__name__}: {exc}"
        for camera in CAMERAS:
            votes[f"identity_{camera}"] = StageVote("error", detail=detail)

    if identity_pair is not None:
        for camera in CAMERAS:
            try:
                available = sorted(frames.get(camera, {}))
                for stamp in (
                    checkpoint_stamps(
                        available[0], available[-1], realistic_cfg["checkpoint_period_s"], available
                    )
                    if available
                    else []
                ):
                    ee = (ee_poses or {}).get(stamp)
                    if camera == "wrist" and ee is None:
                        continue  # VER-8: no EE pose, no trustworthy wrist ROI
                    roi = tray_roi_pixels(tray_min, tray_max, calibration, camera, ee)
                    window = crop_to_roi(frames[camera][stamp]["rgb"], roi)
                    if window is None:
                        continue  # the tray is out of this camera's view
                    detections = shift_detections(
                        detect_meds(window[0], med_names, identity_pair), window[1]
                    )
                    if camera == "overhead" and stamp == available[-1]:
                        terminal_detections = detections
                    judge.observe(
                        camera,
                        identity_frame(
                            detections, target_med, roi, realistic_cfg["identity_min_score"], stamp
                        ),
                    )
            except Exception as exc:  # noqa: BLE001 — VER-13, this camera only
                votes[f"identity_{camera}"] = StageVote(
                    "error", detail=f"{camera} identity raised {type(exc).__name__}: {exc}"
                )
        for camera in CAMERAS:
            votes.setdefault(f"identity_{camera}", judge.identity_vote(camera))

    # ---- stages 2-3 geometry, INDEPENDENTLY (VER-10/VER-11) ---------
    points = None
    try:
        overhead_stamps = sorted(frames.get("overhead", {}))
        seed = grounding_point
        if seed is None and terminal_detections:
            hits = [
                d
                for d in terminal_detections
                if d["label"] == target_med
                and d["score"] >= realistic_cfg.get("grounding_min_score", 0.0)
            ]
            if hits:
                best = max(hits, key=lambda d: d["score"])
                x0, y0, x1, y1 = best["box"]
                seed = ((x0 + x1) / 2, (y0 + y1) / 2)
        if not overhead_stamps:
            detail = "no overhead frames"
        elif seed is None:
            detail = "no grounded target detection: geometry stages cannot be trusted (VER-9/10)"
        else:
            detail = None
            final = frames["overhead"][overhead_stamps[-1]]
            mask = segment_mask(final["rgb"], seed)
            pixels = np.argwhere(mask)[:, ::-1]  # (row, col) -> (u, v)
            points = dominant_surface(
                backproject_overhead(final["depth"], calibration, pixels),
                realistic_cfg["surface_band_m"],
            )
        if detail is not None:
            votes["containment"] = StageVote("error", detail=detail)
            votes["upright"] = StageVote("error", detail=detail)
    except Exception as exc:  # noqa: BLE001 — VER-13: mask/back-projection
        detail = f"target reconstruction raised {type(exc).__name__}: {exc}"
        votes["containment"] = StageVote("error", detail=detail)
        votes["upright"] = StageVote("error", detail=detail)

    if points is not None:
        rank_ratio = realistic_cfg["surface_min_rank_ratio"]
        try:
            votes["containment"] = containment_vote(
                points,
                tray_min,
                tray_max,
                success_cfg["tray_margin_m"],
                success_cfg["resting_tolerance_m"],
                float(med_sizes[target_med][2]),
                rank_ratio,
            )
        except Exception as exc:  # noqa: BLE001 — VER-13, containment only
            votes["containment"] = StageVote(
                "error", detail=f"containment raised {type(exc).__name__}: {exc}"
            )
        try:
            # independent of containment: an upright failure must not
            # overwrite an already-computed containment vote
            votes["upright"] = upright_vote(points, success_cfg["upright_max_deg"], rank_ratio)
        except Exception as exc:  # noqa: BLE001 — VER-13, upright only
            votes["upright"] = StageVote(
                "error", detail=f"upright raised {type(exc).__name__}: {exc}"
            )

    independent_home()
    return finish()
