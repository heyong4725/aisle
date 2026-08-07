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
from dataclasses import dataclass, field
from pathlib import Path

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
        if not frames:
            return StageVote("fail", detail=f"{camera}: no judged frames")
        if self.latched:
            return StageVote(
                "fail",
                detail="wrong-object latch set (VER-9)",
                measurement={"latched_at_ns": self.first_latch_event["sim_time_ns"]},
            )
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
