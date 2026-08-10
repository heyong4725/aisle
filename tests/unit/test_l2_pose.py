"""L2 perception (TC-9's top rung): pose from DETECTION on rendered rgb —
no ground truth of any kind reaches the graph. The identity-safety rules are
measured, not guessed (idea I7): the pinned detector found 19/19 shelf
instances but wrong-id'd 16%, every wrong-id at score margin <= 0.034 vs a
right-id median of 0.134 — so the margin floor is what keeps wrong_object
at zero, and a refusal (1x failure) is always preferred to a low-margin
guess (10x)."""

import numpy as np
import pytest

from aisle.nodes.l2_pose import MARGIN_FLOOR, L2Session
from aisle.nodes.segmented_pose import PoseRefused

pytestmark = pytest.mark.unit

BOX_H = 0.10
TOP_Z = 0.60


def _flat_backproject(depth, pixels):
    pixels = np.asarray(pixels)
    z = depth[pixels[:, 1], pixels[:, 0]]
    return np.stack([pixels[:, 0] / 1000.0, pixels[:, 1] / 1000.0, z], axis=1)


def _scene(box=(10, 20, 60, 65)):
    """rgb + depth with one raised box at `box` (x0, y0, x1, y1)."""
    rgb = np.zeros((100, 100, 3), dtype=np.uint8)
    depth = np.full((100, 100), 0.40, dtype=np.float32)
    x0, y0, x1, y1 = box
    rgb[y0:y1, x0:x1] = 200
    depth[y0:y1, x0:x1] = TOP_Z
    return rgb, depth


def _session(detections, meds=None):
    calls = []

    def detector(rgb):
        calls.append(1)
        return detections

    s = L2Session(
        meds=meds or {"ibuprofen": {"size": [0.05, 0.045, BOX_H]}},
        detector=detector,
        backprojector=lambda calibration: _flat_backproject,
        retry_gap_ns=0,
    )
    s.on_bridge_info({"calibration": {}})
    s.on_target_request({"target_med": "ibuprofen"})
    s.calls = calls
    return s


def _feed(s, sim_time_ns=100, box=(10, 20, 60, 65)):
    rgb, depth = _scene(box)
    return s.on_depth(sim_time_ns, depth) or s.on_rgb(sim_time_ns, rgb)


def test_clean_detection_yields_the_box_centre():
    """The bbox-masked depth goes through the SAME top-surface geometry as
    L1: centre = top surface minus half the med height, and the detection
    evidence (score, margin, box) travels with the pose for the audit
    trail."""
    s = _session([{"label": "ibuprofen", "score": 0.5, "box": [10, 20, 60, 65]}])
    out = _feed(s)
    assert out is not None
    assert out["pos"][2] == pytest.approx(TOP_Z - BOX_H / 2)
    assert out["detection"]["score"] == 0.5
    assert out["detection"]["margin"] == pytest.approx(0.5)
    assert out["target_med"] == "ibuprofen"


def test_the_measured_wrong_id_pattern_is_refused_by_the_margin_floor():
    """The wrong-id signature from the shelf measurement: an overlapping
    rival label within a few centiscores. Margin 0.01 is below the floor —
    refuse rather than risk delivering the rival's box (wrong_object is
    10x worse than a timeout)."""
    s = _session(
        [
            {"label": "ibuprofen", "score": 0.31, "box": [10, 20, 60, 65]},
            {"label": "amoxicillin", "score": 0.30, "box": [12, 22, 62, 67]},
        ]
    )
    with pytest.raises(PoseRefused, match="margin"):
        _feed(s)
    # the request stays pending: a later unambiguous frame publishes
    s.detections = None
    s2 = _session([{"label": "ibuprofen", "score": 0.5, "box": [10, 20, 60, 65]}])
    assert _feed(s2) is not None


def test_a_non_overlapping_rival_does_not_shrink_the_margin():
    """Margin is about WHICH box the label belongs to: a rival detection on
    a different box (no overlap) is not identity ambiguity."""
    s = _session(
        [
            {"label": "ibuprofen", "score": 0.31, "box": [10, 20, 60, 65]},
            {"label": "amoxicillin", "score": 0.30, "box": [70, 20, 95, 65]},
        ]
    )
    assert _feed(s) is not None


def test_missing_target_detection_is_refused():
    s = _session([{"label": "amoxicillin", "score": 0.9, "box": [10, 20, 60, 65]}])
    with pytest.raises(PoseRefused, match="no .* detection"):
        _feed(s)


def test_retry_gap_throttles_detection_attempts():
    """Detection costs ~0.7 s/frame on CPU against a 15 Hz pair stream: a
    persistent refusal must not lag the node unboundedly, so attempts are
    throttled to the retry gap in SIM time. Skipped frames are not
    attempts — the request stays pending."""
    s = _session([])  # never detects: every attempt refuses
    s.retry_gap_ns = int(1e9)
    with pytest.raises(PoseRefused):
        _feed(s, sim_time_ns=1_000_000_000)
    assert _feed(s, sim_time_ns=1_066_000_000) is None  # inside the gap: no attempt
    assert len(s.calls) == 1
    with pytest.raises(PoseRefused):
        _feed(s, sim_time_ns=2_100_000_000)  # past the gap: attempts again
    assert len(s.calls) == 2


def test_reset_clears_the_retry_clock_with_the_buffers():
    """The base lifecycle clears target and frame buffers at reset; L2 adds
    the retry clock — sim time is monotonic across teleports, so a stale
    clock would silently throttle the NEXT episode's first attempts."""
    s = _session([{"label": "ibuprofen", "score": 0.5, "box": [10, 20, 60, 65]}])
    s.retry_gap_ns = int(1e9)
    assert _feed(s, sim_time_ns=1_000_000_000) is not None
    s.on_reset_done()
    s.on_target_request({"target_med": "ibuprofen"})
    assert _feed(s, sim_time_ns=1_200_000_000) is not None  # fresh episode attempts at once


def test_neighbours_fill_positional_slots_with_none_for_undetected():
    """The `neighbours` payload contract (grasp planner zips MED_NAMES
    strict=True): one slot per med in meds order, None where no detection
    supports a position — never silently dropped, never guessed."""
    meds = {
        "amoxicillin": {"size": [0.10, 0.035, 0.055]},
        "ibuprofen": {"size": [0.05, 0.045, BOX_H]},
        "cetirizine": {"size": [0.11, 0.04, 0.06]},
    }
    s = _session(
        [
            {"label": "ibuprofen", "score": 0.5, "box": [10, 20, 60, 65]},
            {"label": "cetirizine", "score": 0.4, "box": [70, 20, 95, 65]},
        ],
        meds=meds,
    )
    out = _feed(s)
    assert len(out["neighbours"]) == 3
    assert out["neighbours"][0] is None  # amoxicillin undetected
    assert out["neighbours"][1] is not None and out["neighbours"][2] is not None
    assert out["neighbours_refused"] == 1


def test_margin_floor_matches_the_measured_separation():
    """The floor sits between the measured populations: above every wrong-id
    margin (max 0.034) and below the right-id median (0.134). Not a tunable
    style choice — move it only with a new measurement."""
    assert 0.034 < MARGIN_FLOOR < 0.134
