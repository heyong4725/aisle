"""VER-9/VER-13/VER-14 (SPEC 040): the wrong-object latch, the explicit
Boolean fusion, and the sidecar record shape.

Pure judge logic — the model-bearing stages are injected as votes, so
these run without torch or sim (CON-12).
"""

import json

import pytest

from aisle.verifier.realistic import (
    EpisodeJudge,
    StageVote,
    fuse,
    sidecar_record,
)

pytestmark = pytest.mark.unit


def frame(sim_time_ns, target=True, non_target=False, scores=None):
    return {
        "sim_time_ns": sim_time_ns,
        "per_class_scores": scores or {"omeprazole": 0.9},
        "target_in_tray": target,
        "non_target_in_tray": non_target,
    }


def test_fusion_is_and_over_every_stage():
    """VER-13: success iff all five stages pass."""
    passing = {
        k: StageVote("pass")
        for k in (
            "calibration",
            "identity_overhead",
            "identity_wrist",
            "containment",
            "upright",
            "home",
        )
    }
    assert fuse(passing) is True
    for stage in passing:
        one_bad = dict(passing)
        one_bad[stage] = StageVote("fail")
        assert fuse(one_bad) is False, f"{stage} did not gate the verdict"


def test_stage_error_fails_closed_never_skips():
    """VER-13: a stage that cannot produce a verdict FAILS the episode —
    never skip-and-fuse over the remaining stages."""
    votes = {
        k: StageVote("pass")
        for k in (
            "calibration",
            "identity_overhead",
            "identity_wrist",
            "containment",
            "upright",
            "home",
        )
    }
    votes["upright"] = StageVote("error", detail="segmenter raised")
    assert fuse(votes) is False


def test_missing_stage_fails_closed():
    """A stage absent from the mapping cannot be silently treated as
    passing (a refactor that drops a stage must not loosen the verdict)."""
    votes = {"calibration": StageVote("pass"), "identity_overhead": StageVote("pass")}
    assert fuse(votes) is False


def test_latch_rejects_simultaneous_target_and_wrong_item():
    """VER-9: a tray holding the target AND a non-target is a FAIL, by
    construction — mirroring VER-3's wrong_object asymmetry."""
    judge = EpisodeJudge(goal_id="ep-0007")
    judge.observe("overhead", frame(1_000_000_000, target=True, non_target=True))
    assert judge.identity_vote("overhead").status == "fail"
    assert judge.latched is True


def test_latch_survives_a_transient_wrong_item():
    """VER-9 (the round-2 review's case): wrong item enters at a
    checkpoint, leaves, target is then delivered — the terminal frame
    looks clean but the episode must still FAIL."""
    judge = EpisodeJudge(goal_id="ep-0011")
    judge.observe("overhead", frame(1_000_000_000, target=False, non_target=True))  # wrong enters
    judge.observe("overhead", frame(6_000_000_000, target=False, non_target=False))  # leaves
    judge.observe("overhead", frame(11_000_000_000, target=True, non_target=False))  # target lands
    assert judge.identity_vote("overhead").status == "fail"
    assert judge.latched is True
    assert judge.first_latch_event["sim_time_ns"] == 1_000_000_000


def test_latch_is_cross_camera_and_episode_scoped():
    """Either camera can set the latch; a fresh episode starts clean."""
    judge = EpisodeJudge(goal_id="ep-0012")
    judge.observe("wrist", frame(2_000_000_000, target=True, non_target=True))
    assert judge.identity_vote("overhead").status == "fail"  # latch is episode-wide
    assert judge.first_latch_event["camera"] == "wrist"
    fresh = EpisodeJudge(goal_id="ep-0013")
    fresh.observe("overhead", frame(1_000_000_000, target=True))
    assert fresh.identity_vote("overhead").status == "pass"


def test_identity_requires_the_target_on_that_camera():
    """A camera that never saw the target in the tray votes fail even
    with a clear latch (per-camera AND fusion, VER-5/VER-9)."""
    judge = EpisodeJudge(goal_id="ep-0014")
    judge.observe("overhead", frame(1_000_000_000, target=True))
    assert judge.identity_vote("overhead").status == "pass"
    assert judge.identity_vote("wrist").status == "fail"  # no frames at all


def test_sidecar_record_carries_the_full_timeline_and_latch():
    """VER-14: goal_id-keyed, latch object, identity frame arrays with
    sim stamps — the shape fidelity.py consumes."""
    judge = EpisodeJudge(goal_id="ep-0020")
    judge.observe("overhead", frame(1_000_000_000, target=False, non_target=True))
    judge.observe("overhead", frame(6_000_000_000, target=True))
    judge.observe("wrist", frame(6_000_000_000, target=True))
    votes = {
        "calibration": StageVote("pass", measurement={"max_dev_m": 0.001}),
        "identity_overhead": judge.identity_vote("overhead"),
        "identity_wrist": judge.identity_vote("wrist"),
        "containment": StageVote("pass", measurement={"margin_m": 0.012}),
        "upright": StageVote("pass", measurement={"tilt_deg": 3.4}),
        "home": StageVote("fail", measurement={"max_joint_residual_rad": 0.2}),
    }
    record = sidecar_record(judge, votes)

    assert record["goal_id"] == "ep-0020"
    assert record["verifier"] == "realistic"
    assert record["latch"]["latched"] is True
    assert record["latch"]["first_event"]["sim_time_ns"] == 1_000_000_000
    overhead = record["stages"]["identity_overhead"]
    assert overhead["vote"] == "fail"
    assert [f["sim_time_ns"] for f in overhead["frames"]] == [1_000_000_000, 6_000_000_000]
    assert overhead["frames"][0]["non_target_in_tray"] is True
    assert record["stages"]["home"]["measurement"]["max_joint_residual_rad"] == 0.2
    assert set(record["stages"]) == {
        "calibration",
        "identity_overhead",
        "identity_wrist",
        "containment",
        "upright",
        "home",
    }
    json.dumps(record)  # serializable as one JSONL line


def test_clean_episode_sidecar_has_a_null_first_event():
    judge = EpisodeJudge(goal_id="ep-0021")
    judge.observe("overhead", frame(1_000_000_000, target=True))
    judge.observe("wrist", frame(1_000_000_000, target=True))
    votes = {
        "calibration": StageVote("pass"),
        "identity_overhead": judge.identity_vote("overhead"),
        "identity_wrist": judge.identity_vote("wrist"),
        "containment": StageVote("pass"),
        "upright": StageVote("pass"),
        "home": StageVote("pass"),
    }
    record = sidecar_record(judge, votes)
    assert record["latch"] == {"latched": False, "first_event": None}
    assert fuse(votes) is True


def test_checkpoint_stamps_cover_the_cadence_and_the_terminal_frame():
    """VER-9: checkpoints every period_s from goal receipt, PLUS the
    terminal frame — the latch can only see what it samples."""
    from aisle.verifier.realistic import checkpoint_stamps

    stamps = checkpoint_stamps(0, 12_000_000_000, 5.0)
    assert stamps == [0, 5_000_000_000, 10_000_000_000, 12_000_000_000]

    # renders are rate-limited (BRG-2): stamps snap to the nearest
    # available frame at or before them, and the last frame always judges
    available = [0, 4_000_000_000, 9_000_000_000, 11_500_000_000]
    snapped = checkpoint_stamps(0, 12_000_000_000, 5.0, available)
    assert snapped == [0, 4_000_000_000, 9_000_000_000, 11_500_000_000]
    assert all(s in available for s in snapped)

    assert checkpoint_stamps(0, 1_000_000_000, 5.0, []) == []


def test_judge_episode_runs_the_cadence_and_writes_the_sidecar(tmp_path):
    """VER-5/VER-9/VER-14 end to end with an injected detector: the
    transient wrong item is sampled at a checkpoint, so the episode
    fails even though the terminal frame is clean — and the sidecar
    records every judged frame."""
    from aisle.verifier.realistic import judge_episode

    frames = {
        "overhead": {i * 5_000_000_000: {"frame": i} for i in range(4)},
        "wrist": {i * 5_000_000_000: {"frame": i} for i in range(4)},
    }
    roi = {"overhead": (0.0, 0.0, 100.0, 100.0), "wrist": (0.0, 0.0, 100.0, 100.0)}

    def detect(camera, payload, sim_time_ns):
        box = [10, 10, 20, 20]
        if payload["frame"] == 1:  # a wrong item, only at this checkpoint
            return [{"label": "ibuprofen", "score": 0.8, "box": box}]
        if payload["frame"] >= 2:
            return [{"label": "omeprazole", "score": 0.9, "box": box}]
        return []

    clean_stages = {
        "calibration": StageVote("pass"),
        "containment": StageVote("pass"),
        "upright": StageVote("pass"),
        "home": StageVote("pass"),
    }
    success, record = judge_episode(
        goal_id="ep-0042",
        target_med="omeprazole",
        frames=frames,
        detect=detect,
        stage_votes=clean_stages,
        period_s=5.0,
        min_score=0.3,
        roi=roi,
        run_dir=tmp_path,
    )
    assert success is False  # the latch caught the transient
    assert record["latch"]["latched"] is True
    assert record["latch"]["first_event"]["sim_time_ns"] == 5_000_000_000
    assert len(record["stages"]["identity_overhead"]["frames"]) == 4

    written = [
        json.loads(line) for line in (tmp_path / "verifier_stages.jsonl").read_text().splitlines()
    ]
    assert written[0]["goal_id"] == "ep-0042" and written[0]["success"] is False


def test_judge_episode_succeeds_on_a_clean_delivery(tmp_path):
    from aisle.verifier.realistic import judge_episode

    frames = {c: {0: {"f": 0}, 5_000_000_000: {"f": 1}} for c in ("overhead", "wrist")}
    roi = {"overhead": (0.0, 0.0, 100.0, 100.0), "wrist": (0.0, 0.0, 100.0, 100.0)}
    success, record = judge_episode(
        goal_id="ep-0043",
        target_med="omeprazole",
        frames=frames,
        detect=lambda c, p, t: [{"label": "omeprazole", "score": 0.95, "box": [10, 10, 20, 20]}],
        stage_votes={
            k: StageVote("pass") for k in ("calibration", "containment", "upright", "home")
        },
        period_s=5.0,
        min_score=0.3,
        roi=roi,
        run_dir=tmp_path,
    )
    assert success is True and record["latch"]["latched"] is False


def test_judge_episode_fails_closed_when_a_camera_has_no_frames(tmp_path):
    """VER-13: a camera that produced nothing cannot be skipped over."""
    from aisle.verifier.realistic import judge_episode

    success, record = judge_episode(
        goal_id="ep-0044",
        target_med="omeprazole",
        frames={"overhead": {0: {"f": 0}}, "wrist": {}},
        detect=lambda c, p, t: [{"label": "omeprazole", "score": 0.95, "box": [10, 10, 20, 20]}],
        stage_votes={
            k: StageVote("pass") for k in ("calibration", "containment", "upright", "home")
        },
        period_s=5.0,
        min_score=0.3,
        roi={"overhead": (0.0, 0.0, 100.0, 100.0), "wrist": (0.0, 0.0, 100.0, 100.0)},
    )
    assert success is False
    assert record["stages"]["identity_wrist"]["vote"] == "fail"
