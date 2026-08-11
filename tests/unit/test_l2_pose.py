"""L2 perception (TC-9's top rung): identity from rendered RGB plus ordinary
same-stamp sensor depth; no simulator pose or segmentation ground truth.

The identity-safety rules are measured under the IMPLEMENTED rival rule
(PR #139 round-2 re-measurement; the first cut's any-overlap rule was a
different population than the offline calibration and these tests encoded
it — defect-class instance #9). Under rival-contains-the-target-centre:
wrong picks all carry NEGATIVE margins (max -0.027), right picks all
positive (min +0.016) — MARGIN_FLOOR 0.01 sits inside the gap. The
production detector emits ~3600 boxes/frame at threshold 0 with EVERY
label populated, so the fakes here include background boxes under every
label (scores <= the measured background max 0.054): the no-detection
regime does not exist in production, and the neighbour score floor is
what makes a None slot reachable at all."""

import numpy as np
import pytest

from aisle.nodes.l2_pose import MARGIN_FLOOR, NEIGHBOUR_SCORE_FLOOR, L2Session
from aisle.nodes.segmented_pose import PoseRefused

pytestmark = pytest.mark.unit

BOX_H = 0.10
TOP_Z = 0.60


def _flat_backproject(depth, pixels):
    pixels = np.asarray(pixels)
    z = depth[pixels[:, 1], pixels[:, 0]]
    return np.stack([pixels[:, 0] / 1000.0, pixels[:, 1] / 1000.0, z], axis=1)


def _scene(box=(10, 20, 60, 65)):
    rgb = np.zeros((100, 100, 3), dtype=np.uint8)
    depth = np.full((100, 100), 0.40, dtype=np.float32)
    x0, y0, x1, y1 = box
    rgb[y0:y1, x0:x1] = 200
    depth[y0:y1, x0:x1] = TOP_Z
    return rgb, depth


def _background(meds):
    """The production regime's floor noise: low-score argmax boxes under
    EVERY label, scattered off the meds (measured max 0.054)."""
    return [
        {"label": name, "score": 0.02 + i * 0.001, "box": [80 + i, 80, 95 + i, 95]}
        for i, name in enumerate(meds)
    ]


def _session(detections, meds=None):
    meds = meds or {"ibuprofen": {"size": [0.05, 0.045, BOX_H]}}
    payload = {"dets": list(detections) + _background(meds)}
    calls = []

    def detector(rgb):
        calls.append(1)
        return payload["dets"]

    s = L2Session(
        meds=meds,
        detector=detector,
        backprojector=lambda calibration: _flat_backproject,
        retry_gap_ns=0,
    )
    s.on_bridge_info({"calibration": {}})
    s.on_target_request({"target_med": "ibuprofen"})
    s.calls = calls
    s.payload = payload  # mutable: recovery tests re-feed the SAME session
    return s


def _feed(s, sim_time_ns=100, box=(10, 20, 60, 65)):
    rgb, depth = _scene(box)
    return s.on_depth(sim_time_ns, depth) or s.on_rgb(sim_time_ns, rgb)


def test_clean_detection_yields_the_box_centre():
    """The bbox-masked depth goes through the SAME top-surface geometry as
    L1, with the detection evidence riding for the audit — against the
    full production-regime background noise."""
    s = _session([{"label": "ibuprofen", "score": 0.5, "box": [10, 20, 60, 65]}])
    out = _feed(s)
    assert out is not None
    assert out["pos"][2] == pytest.approx(TOP_Z - BOX_H / 2)
    assert out["detection"]["score"] == 0.5
    assert out["target_med"] == "ibuprofen"


def test_l2_rgb_identity_uses_same_stamp_sensor_depth_only_for_metric_pose():
    """TC-9 / issue #143: L2 class identity is derived from rendered RGB,
    while ordinary same-stamp camera depth MAY supply metric geometry. The
    detector receives only the RGB image; changing the paired depth changes
    reconstructed Z without becoming an identity input."""
    seen_rgb = []

    def detector(rgb):
        seen_rgb.append(rgb.copy())
        return [{"label": "ibuprofen", "score": 0.5, "box": [10, 20, 60, 65]}]

    session = L2Session(
        meds={"ibuprofen": {"size": [0.05, 0.045, BOX_H]}},
        detector=detector,
        backprojector=lambda calibration: _flat_backproject,
        retry_gap_ns=0,
    )
    session.on_bridge_info({"calibration": {}})
    session.on_target_request({"target_med": "ibuprofen"})

    rgb, depth = _scene()
    metric_top_z = 0.72
    depth[20:65, 10:60] = metric_top_z
    assert session.on_depth(123, depth) is None
    out = session.on_rgb(123, rgb)

    assert len(seen_rgb) == 1 and np.array_equal(seen_rgb[0], rgb)
    assert out["pos"][2] == pytest.approx(metric_top_z - BOX_H / 2)

    session.on_target_request({"target_med": "ibuprofen"})
    shallower_depth = depth.copy()
    shallower_top_z = 0.61
    shallower_depth[20:65, 10:60] = shallower_top_z
    assert session.on_depth(124, shallower_depth) is None
    shallower_out = session.on_rgb(124, rgb)

    assert len(seen_rgb) == 2 and np.array_equal(seen_rgb[1], rgb)
    assert shallower_out["pos"][2] == pytest.approx(shallower_top_z - BOX_H / 2)
    assert shallower_out["pos"][2] != pytest.approx(out["pos"][2])


def test_a_rival_at_the_picks_centre_with_higher_score_is_refused():
    """The measured wrong-pick signature under the implemented rule: the
    rival label's box contains the target pick's centre and outscores it —
    margin negative, refuse (wrong_object is 10x worse than a timeout).
    The SAME session then recovers when a later frame is unambiguous (the
    first cut's recovery test built a fresh session and proved nothing —
    round-2 review)."""
    s = _session(
        [
            {"label": "ibuprofen", "score": 0.30, "box": [10, 20, 60, 65]},
            {"label": "amoxicillin", "score": 0.33, "box": [8, 18, 62, 67]},
        ]
    )
    with pytest.raises(PoseRefused, match="margin"):
        _feed(s, sim_time_ns=100)
    assert s.pending  # refusal must not consume the request
    s.payload["dets"] = [
        {"label": "ibuprofen", "score": 0.5, "box": [10, 20, 60, 65]}
    ] + _background(s.meds)
    assert _feed(s, sim_time_ns=200) is not None


def test_a_grazing_neighbour_box_is_scene_layout_not_a_rival():
    """The live over-refusal the round-2 review caught: a correctly-labeled
    NEIGHBOUR whose box grazes the target's by a few pixels (but does not
    contain its centre) outscoring the target is layout, not identity
    ambiguity — under the first cut's any-overlap rule this refused; under
    the measured rule it must publish."""
    s = _session(
        [
            {"label": "ibuprofen", "score": 0.30, "box": [10, 20, 60, 65]},
            # overlaps x in [55,60] — nowhere near the target centre (35, 42)
            {"label": "amoxicillin", "score": 0.45, "box": [55, 20, 99, 65]},
        ]
    )
    assert _feed(s) is not None


def test_background_noise_alone_cannot_ground_a_target():
    """With only production floor noise under the target label (<= the
    measured background max), the pick's margin equals its raw score minus
    any centre rival — the tiny box also fails the min-pixel floor. Either
    way: refusal, not a phantom pose."""
    s = _session([])  # nothing but background under every label
    with pytest.raises(PoseRefused):
        _feed(s)
    assert s.pending


def test_malformed_detection_box_refuses_not_crashes():
    """A NaN box coordinate must become PoseRefused (the node's refusal
    boundary), never a ValueError that kills the pose source mid-run
    (round-2 review)."""
    s = _session([{"label": "ibuprofen", "score": 0.5, "box": [10, float("nan"), 60, 65]}])
    with pytest.raises(PoseRefused, match="non-finite|malformed"):
        _feed(s)
    # an INVERTED box is finite (passes the pick-time check) and must be
    # refused by _bbox_mask's own guard — the two guards are separately
    # load-bearing and separately mutation-verified
    s2 = _session([{"label": "ibuprofen", "score": 0.5, "box": [60, 20, 10, 65]}])
    with pytest.raises(PoseRefused, match="malformed"):
        _feed(s2)


def test_retry_gap_throttles_detection_attempts():
    """Detection costs ~0.7 s/frame against a 15 Hz pair stream: attempts
    are throttled to the retry gap in SIM time; skipped frames are not
    attempts and the request stays pending."""
    s = _session([])
    s.retry_gap_ns = int(1e9)
    with pytest.raises(PoseRefused):
        _feed(s, sim_time_ns=1_000_000_000)
    assert _feed(s, sim_time_ns=1_066_000_000) is None
    assert len(s.calls) == 1
    with pytest.raises(PoseRefused):
        _feed(s, sim_time_ns=2_100_000_000)
    assert len(s.calls) == 2


def test_reset_clears_the_retry_clock_with_the_buffers():
    s = _session([{"label": "ibuprofen", "score": 0.5, "box": [10, 20, 60, 65]}])
    s.retry_gap_ns = int(1e9)
    assert _feed(s, sim_time_ns=1_000_000_000) is not None
    s.on_reset_done()
    s.on_target_request({"target_med": "ibuprofen"})
    assert _feed(s, sim_time_ns=1_200_000_000) is not None


def test_a_complete_pre_reset_pair_never_attempts_after_the_boundary():
    """Round-2 review: with a 0.7 s detector the queues back up ~30 deep,
    so COMPLETE pre-reset pairs can drain after reset_done — clearing the
    buffers only stops a single straggler half. The reset watermark makes
    the epoch explicit: both halves of an old pair arriving after the
    boundary must not attempt, while genuinely new frames must."""
    s = _session([{"label": "ibuprofen", "score": 0.5, "box": [10, 20, 60, 65]}])
    s.on_reset_done()  # boundary lands while pair (t=100) is still queued
    s.on_target_request({"target_med": "ibuprofen"})
    s.newest_stamp_ns = 100  # the epoch boundary saw stamps up to 100
    s.reset_watermark_ns = 100
    rgb, depth = _scene()
    # BOTH drain orders: the watermark must hold whichever half completes
    # the stale pair (each on_* method carries its own condition — a
    # mutation of one alone must fail this test)
    assert s.on_depth(100, depth) is None
    assert s.on_rgb(100, rgb) is None  # rgb completes the pair: no attempt
    s.latest_depth = s.latest_obs = None
    assert s.on_rgb(100, rgb) is None
    assert s.on_depth(100, depth) is None  # depth completes the pair: no attempt
    assert len(s.calls) == 0
    assert _feed(s, sim_time_ns=200) is not None  # fresh frames attempt at once


def test_neighbours_score_floor_makes_none_slots_real():
    """At threshold 0 every label always has candidate boxes, so an
    unfloored neighbour slot would be filled by a background argmax box —
    a guessed obstacle with the audit reading confident. Below the floor
    (just above the measured background max) the slot is None + counted;
    a CONFIDENT detection fills its slot even without a margin gate."""
    meds = {
        "amoxicillin": {"size": [0.10, 0.035, 0.055]},
        "ibuprofen": {"size": [0.05, 0.045, BOX_H]},
        "cetirizine": {"size": [0.11, 0.04, 0.06]},
    }
    s = _session(
        [
            {"label": "ibuprofen", "score": 0.5, "box": [10, 20, 60, 65]},
            {"label": "cetirizine", "score": 0.4, "box": [70, 20, 95, 65]},
            # amoxicillin present ONLY as background noise (in _background)
        ],
        meds=meds,
    )
    out = _feed(s)
    assert len(out["neighbours"]) == 3
    assert out["neighbours"][0] is None  # amoxicillin: background only
    assert out["neighbours"][1] is not None and out["neighbours"][2] is not None
    assert out["neighbours_refused"] == 1


def test_floors_match_the_remeasured_separation():
    """MARGIN_FLOOR inside the measured gap (wrong picks max -0.027, right
    picks min +0.016); NEIGHBOUR_SCORE_FLOOR just above the measured
    background max (0.054) and below cetirizine/amoxicillin confident
    scores. Move either only with a new measurement."""
    assert -0.027 < MARGIN_FLOOR < 0.016
    assert 0.054 < NEIGHBOUR_SCORE_FLOOR < 0.15
