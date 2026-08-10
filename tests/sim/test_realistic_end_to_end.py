"""VER-5 end to end on the golden frames (SPEC 040).

PR #103's reviews found (a) the judge was reachable only with injected
votes and (b) these assertions were too weak to catch a mis-grounded
segmentation. This file now asserts the ACTUAL geometry votes for a
known delivery, and pins the fail-closed paths: model errors, and a
detector too weak to ground the mask.

Marker `sim`: needs the pinned weights and the committed fixture.
"""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "verifier" / "golden_frames.npz"
STAGES = ("calibration", "identity_overhead", "identity_wrist", "containment", "upright", "home")

pytestmark = [
    pytest.mark.sim,
    pytest.mark.skipif(
        importlib.util.find_spec("transformers") is None,
        reason="transformers not installed (uv sync --extra sim)",
    ),
    pytest.mark.skipif(not FIXTURE.exists(), reason="golden frames missing"),
]


def _inputs():
    from aisle.scenes.pharmacy import load_meds, load_physics
    from aisle.verifier.oracle import build_judge_cfg, load_thresholds

    data = np.load(FIXTURE, allow_pickle=False)
    calibration = json.loads(str(data["calibration"]))
    physics = load_physics()
    nominal = json.loads(json.dumps(calibration))
    nominal["_overhead_lookat"] = physics["cameras"]["overhead_lookat"]
    # the tray bounds come from the ORACLE's own cfg builder, so the
    # realistic verifier measures against exactly VER-2's geometry —
    # notably the open-topped floor at pos_z + size_z/2, not the slab
    # bottom (deriving it independently here got that wrong)
    meds = load_meds()
    cfg = build_judge_cfg(
        physics,
        meds,
        "franka",
        timeout_s=60.0,
        initial_positions=[(0.0, 0.0, 0.0)] * len(meds),
        robot_home_error_rad=0.0,
    )
    return {
        "data": data,
        "calibration": calibration,
        "nominal": nominal,
        "thresholds": load_thresholds(),
        "tray_min": cfg.tray_min,
        "tray_max": cfg.tray_max,
        "physics": physics,
        "med_sizes": {name: meds[name]["size"] for name in meds},
        "home": np.asarray(physics["embodiment"]["franka"]["home_qpos"], dtype=float),
    }


def _target_pixel(ctx):
    """Project the delivered target's KNOWN position to pixels — test-only
    grounding, so the geometry stages are exercised independently of
    detector quality (which the fidelity number measures separately)."""
    from aisle.scenes.pharmacy import MED_NAMES
    from aisle.verifier.stages import project_to_pixels

    data = ctx["data"]
    idx = MED_NAMES.index(str(data["target_med"]))
    state = data["delivered_oracle_state"]
    pos = np.asarray(state[idx * 7 : idx * 7 + 3], dtype=float)
    size = ctx["med_sizes"][str(data["target_med"])]
    top = pos + np.array([0.0, 0.0, float(size[2]) / 2])  # the visible lid
    return tuple(project_to_pixels(top.reshape(1, 3), ctx["calibration"])[0])


def _judge(ctx, run_dir, **overrides):
    from aisle.verifier.realistic import judge_frames

    data = ctx["data"]
    kwargs = {
        "goal_id": "golden-0001",
        "target_med": str(data["target_med"]),
        "med_names": [str(m) for m in data["med_names"]],
        "med_sizes": ctx["med_sizes"],
        "frames": {
            "overhead": {
                0: {
                    "rgb": data["delivered_rgb_overhead"],
                    "depth": data["delivered_depth_overhead"],
                }
            }
        },
        "calibration": ctx["calibration"],
        "nominal_calibration": ctx["nominal"],
        "jitter_bound_m": ctx["physics"]["domain_randomization"]["camera_jitter_m"],
        "tray_min": ctx["tray_min"],
        "tray_max": ctx["tray_max"],
        "joint_state": ctx["home"],
        "home_qpos": ctx["home"],
        "thresholds": ctx["thresholds"],
        "run_dir": run_dir,
    }
    kwargs.update(overrides)
    return judge_frames(**kwargs)


def test_grounded_delivery_produces_the_expected_geometry_votes(tmp_path):
    """The delivered target IS in the tray, upright, robot at home — with
    the segmenter grounded on it, containment and upright must PASS with
    a near-zero resting gap and near-zero tilt (PR #103 review round 2:
    the old centre prompt segmented the wrong region and both failed)."""
    ctx = _inputs()
    # NO grounding override: the REAL detector must locate the target
    # (colour-word queries + the calibrated threshold, 2026-08-07)
    _, record = _judge(ctx, tmp_path)

    # the identity stage now votes on REAL detections: colour-word
    # queries at the calibrated threshold (the old name-based query at
    # 0.30 rejected every detection this scene can produce)
    assert record["stages"]["identity_overhead"]["vote"] == "pass", record["stages"][
        "identity_overhead"
    ]
    containment = record["stages"]["containment"]
    upright = record["stages"]["upright"]
    assert containment["vote"] == "pass", containment
    assert abs(containment["measurement"]["rest_gap_m"]) < 0.02
    assert upright["vote"] == "pass", upright
    assert upright["measurement"]["tilt_deg"] < 10.0
    assert record["stages"]["home"]["vote"] == "pass"
    # VER-14: stage 0 records its measured deviations, not a bare pass
    assert "overhead_pos_max_dev_m" in record["stages"]["calibration"]["measurement"]


def test_ungrounded_detection_fails_closed_rather_than_measuring_noise(tmp_path):
    """When no target detection clears the grounding threshold, the
    geometry stages record `error` — the pipeline refuses to measure an
    arbitrary mask. On these untextured renders that is the real
    behaviour today (ADR section 7 legibility)."""
    ctx = _inputs()
    ctx["thresholds"] = json.loads(json.dumps(ctx["thresholds"]))
    ctx["thresholds"]["realistic"]["grounding_min_score"] = 0.99  # nothing can clear it
    success, record = _judge(ctx, tmp_path)

    assert success is False
    for stage in ("containment", "upright"):
        assert record["stages"][stage]["vote"] == "error"
        assert "no grounded target detection" in record["stages"][stage]["detail"]


def test_model_errors_become_stage_errors_not_tracebacks(tmp_path, monkeypatch):
    """VER-13 (PR #103 review round 2): a raised model error must fail
    the episode with the stage recorded as `error` and the sidecar
    written — never escape the judge."""
    import aisle.verifier.models as models

    ctx = _inputs()
    monkeypatch.setattr(
        models,
        "load_pinned",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("weights unavailable")),
    )
    success, record = _judge(ctx, tmp_path)

    assert success is False
    assert set(record["stages"]) == set(STAGES)
    assert record["stages"]["identity_overhead"]["vote"] == "error"
    assert "weights unavailable" in record["stages"]["identity_overhead"]["detail"]
    assert (tmp_path / "verifier_stages.jsonl").exists(), "sidecar not written on model failure"


def test_calibration_refusal_short_circuits_before_any_model_runs(tmp_path):
    """VER-13 fail-closed: a corrupt calibration fails the episode
    without spending model time, and records its deviations."""
    ctx = _inputs()
    corrupt = json.loads(json.dumps(ctx["calibration"]))
    corrupt["overhead"]["depth_scale_m"] = 0.001
    success, record = _judge(ctx, tmp_path, calibration=corrupt)

    assert success is False
    assert record["stages"]["calibration"]["vote"] == "error"
    assert "depth_scale_m" in record["stages"]["calibration"]["detail"]
