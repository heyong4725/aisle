"""SPEC 480 sensor arm (issue #352, SEM-3/SEM-6/SEM-14): the rendered-
perception identity adapter binds detections at the tool centre point's
pixel into SEM-3 assertions, throttles the detector, and refuses when
nothing sits there — with a fake detector, no model download."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from aisle.nodes.semantic_gateway import (
    SENSOR_MIN_INTERVAL_S,
    Gateway,
    SensorIdentity,
)

pytestmark = pytest.mark.unit

MEDS = ["amoxicillin", "ibuprofen", "cetirizine", "omeprazole", "metformin"]
TRAY = {"pos": [0.5, -0.4, 0.05], "size": [0.3, 0.2, 0.05]}
KEY = hashlib.sha256(b"sensor").digest()
FRAME = np.zeros((480, 640, 3), dtype=np.uint8)


class FakeDetector:
    def __init__(self, detections):
        self.detections = detections
        self.calls = 0

    def __call__(self, rgb):
        self.calls += 1
        return list(self.detections)


def _sensor(detections, projector=lambda tcp: (320.0, 240.0)):
    s = SensorIdentity(MEDS, detector=FakeDetector(detections), projector=projector)
    s.on_calibration({"overhead": {}})
    return s


def _gateway(sensor):
    g = Gateway("sensor_shield", KEY, MEDS, TRAY, grasp_cmd=1.0, sensor=sensor)
    g.on_goal({"target_med": "ibuprofen"}, "ep-0", now_s=1.0, vocabulary=MEDS)
    return g


def test_detection_at_the_tool_pixel_becomes_a_normalized_assertion():
    """SEM-3: the detections whose box centres sit at the TCP pixel form the
    class distribution; the frame stamp is the capture time."""
    s = _sensor(
        [
            {"label": "ibuprofen", "score": 0.6, "box": [300, 220, 340, 260]},
            {"label": "metformin", "score": 0.2, "box": [310, 230, 330, 250]},
            {"label": "amoxicillin", "score": 0.9, "box": [10, 10, 50, 50]},  # far away
        ]
    )
    s.on_rgb(FRAME, 1.9)
    assertion, track = s.assertion(np.array([0.4, 0.1, 0.3]), 0.1, 2.0)
    assert track == "ibuprofen" and assertion["refused"] is False
    assert assertion["classes"] == pytest.approx({"ibuprofen": 0.75, "metformin": 0.25})
    assert assertion["capture_s"] == 1.9 and assertion["in_envelope"] is True
    assert assertion["evidence_kind"] == "rendered_perception"


def test_no_detection_at_the_tool_refuses():
    """SEM-6: nothing at the TCP pixel is a refusal, not a guess."""
    s = _sensor([{"label": "ibuprofen", "score": 0.9, "box": [10, 10, 50, 50]}])
    s.on_rgb(FRAME, 1.9)
    assertion, track = s.assertion(np.array([0.4, 0.1, 0.3]), 0.1, 2.0)
    assert track is None and assertion["refused"] is True and assertion["classes"] == {}


def test_detector_is_throttled_and_cached_per_frame():
    """The detector runs once per frame and not more often than the frozen
    interval while a stage is active; proposals reuse the cache."""
    det = FakeDetector([{"label": "ibuprofen", "score": 0.9, "box": [300, 220, 340, 260]}])
    s = SensorIdentity(MEDS, detector=det, projector=lambda tcp: (320.0, 240.0))
    s.on_calibration({})
    s.on_rgb(FRAME, 1.0)
    for i in range(10):
        s.assertion(np.zeros(3), 0.1, 1.0 + i * 0.01)
    assert det.calls == 1
    s.on_rgb(FRAME, 1.1)  # newer frame but inside the interval
    s.assertion(np.zeros(3), 0.1, 1.1)
    assert det.calls == 1
    s.on_rgb(FRAME, 1.0 + SENSOR_MIN_INTERVAL_S)
    s.assertion(np.zeros(3), 0.1, 1.0 + SENSOR_MIN_INTERVAL_S)
    assert det.calls == 2


def test_sensor_gateway_permits_a_confident_correct_detection_and_refuses_a_wrong_one():
    """SEM-4 / SEM-7: a confident detection of the assigned med permits the
    closure; a confident detection of another med refuses it; a weak
    detection is below the frozen threshold."""
    tcp = np.array([0.4, 0.1, 0.3])
    right = _sensor([{"label": "ibuprofen", "score": 0.9, "box": [300, 220, 340, 260]}])
    right.on_rgb(FRAME, 1.9)
    g = _gateway(right)
    assert g.propose("gripper_cmd", np.array([1.0]), tcp, 2.0)["forward"] is True

    wrong = _sensor([{"label": "metformin", "score": 0.9, "box": [300, 220, 340, 260]}])
    wrong.on_rgb(FRAME, 1.9)
    g = _gateway(wrong)
    d = g.propose("gripper_cmd", np.array([1.0]), tcp, 2.0)
    assert d["forward"] is False and d["reason"] == "wrong_target"

    weak = _sensor(
        [
            {"label": "ibuprofen", "score": 0.5, "box": [300, 220, 340, 260]},
            {"label": "metformin", "score": 0.4, "box": [305, 225, 335, 255]},
        ]
    )
    weak.on_rgb(FRAME, 1.9)
    g = _gateway(weak)
    d = g.propose("gripper_cmd", np.array([1.0]), tcp, 2.0)
    assert d["forward"] is False and d["reason"] == "below_threshold"


def test_stale_frame_refuses():
    """SEM-6: a detection on a frame older than max_age_s is not evidence."""
    s = _sensor([{"label": "ibuprofen", "score": 0.9, "box": [300, 220, 340, 260]}])
    s.on_rgb(FRAME, 1.0)
    g = _gateway(s)
    d = g.propose("gripper_cmd", np.array([1.0]), np.array([0.4, 0.1, 0.3]), 2.0)
    assert d["forward"] is False and d["reason"] == "missing_or_stale_identity"
