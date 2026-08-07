"""VER-5 end to end on the golden frames (SPEC 040).

PR #103's review found the judge was reachable only from tests with
injected votes — scaffolding, not a verifier. This test runs the REAL
composition: stage 0 against the published calibration, the pinned
models through the CPU-disciplined adapters, back-projection,
containment/upright/home, the VER-13 fusion, and the VER-14 sidecar.

Marker `sim`: needs the pinned weights and the committed fixture.
"""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "verifier" / "golden_frames.npz"

pytestmark = [
    pytest.mark.sim,
    pytest.mark.skipif(
        importlib.util.find_spec("transformers") is None,
        reason="transformers not installed (uv sync --extra sim)",
    ),
    pytest.mark.skipif(not FIXTURE.exists(), reason="golden frames missing"),
]


def _inputs():
    from aisle.scenes.pharmacy import load_physics, resolve_layout
    from aisle.verifier.oracle import load_thresholds

    data = np.load(FIXTURE, allow_pickle=False)
    calibration = json.loads(str(data["calibration"]))
    nominal = dict(calibration)
    physics = load_physics()
    nominal["_overhead_lookat"] = physics["cameras"]["overhead_lookat"]
    layout = resolve_layout(physics, "franka")
    tray = layout["tray"]
    half = np.asarray(tray["size"], dtype=float) / 2
    centre = np.asarray(tray["pos"], dtype=float)
    tray_min = (centre - half).tolist()
    tray_max = (centre + half).tolist()
    return data, calibration, nominal, load_thresholds(), tray_min, tray_max, physics


def test_judge_frames_runs_the_whole_pipeline_and_writes_the_sidecar(tmp_path):
    """The composed path produces a Boolean verdict and a complete
    VER-14 record from RAW frames — no injected votes anywhere."""
    from aisle.verifier.realistic import judge_frames

    data, calibration, nominal, thresholds, tray_min, tray_max, physics = _inputs()
    home = np.asarray(physics["embodiment"]["franka"]["home_qpos"], dtype=float)
    frames = {
        "overhead": {
            0: {
                "rgb": data["delivered_rgb_overhead"],
                "depth": data["delivered_depth_overhead"],
            }
        }
    }
    success, record = judge_frames(
        goal_id="golden-0001",
        target_med=str(data["target_med"]),
        med_names=[str(m) for m in data["med_names"]],
        frames=frames,
        calibration=calibration,
        nominal_calibration=nominal,
        jitter_bound_m=physics["domain_randomization"]["camera_jitter_m"],
        tray_min=tray_min,
        tray_max=tray_max,
        joint_state=home,
        home_qpos=home,
        thresholds=thresholds,
        run_dir=tmp_path,
    )

    assert isinstance(success, bool)
    assert set(record["stages"]) == {
        "calibration",
        "identity_overhead",
        "identity_wrist",
        "containment",
        "upright",
        "home",
    }
    assert record["stages"]["calibration"]["vote"] == "pass"  # stage 0 accepted
    assert record["stages"]["home"]["vote"] == "pass"  # at home by construction
    # the geometry stages produced real measurements from real pixels
    assert "margin_m" in record["stages"]["containment"].get("measurement", {})
    assert "tilt_deg" in record["stages"]["upright"].get("measurement", {})
    # the identity timeline is populated from actual detections
    assert record["stages"]["identity_overhead"]["frames"], "no judged identity frames"

    written = (tmp_path / "verifier_stages.jsonl").read_text().splitlines()
    assert json.loads(written[0])["goal_id"] == "golden-0001"


def test_calibration_refusal_short_circuits_before_any_model_runs(tmp_path):
    """VER-13 fail-closed: a corrupt calibration must fail the episode
    without spending model time."""
    from aisle.verifier.realistic import judge_frames

    data, calibration, nominal, thresholds, tray_min, tray_max, physics = _inputs()
    corrupt = json.loads(json.dumps(calibration))
    corrupt["overhead"]["depth_scale_m"] = 0.001
    home = np.asarray(physics["embodiment"]["franka"]["home_qpos"], dtype=float)

    success, record = judge_frames(
        goal_id="golden-refuse",
        target_med=str(data["target_med"]),
        med_names=[str(m) for m in data["med_names"]],
        frames={
            "overhead": {
                0: {
                    "rgb": data["delivered_rgb_overhead"],
                    "depth": data["delivered_depth_overhead"],
                }
            }
        },
        calibration=corrupt,
        nominal_calibration=nominal,
        jitter_bound_m=physics["domain_randomization"]["camera_jitter_m"],
        tray_min=tray_min,
        tray_max=tray_max,
        joint_state=home,
        home_qpos=home,
        thresholds=thresholds,
        run_dir=tmp_path,
    )
    assert success is False
    assert record["stages"]["calibration"]["vote"] == "error"
    assert "depth_scale_m" in record["stages"]["calibration"]["detail"]
