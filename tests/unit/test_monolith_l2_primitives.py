"""MON-4/BND-2: the paired L2 primitive crosses the data-only worker boundary."""

import numpy as np
import pytest

from aisle.monolith.confinement import ConfinementViolation, default_integrity
from aisle.monolith.primitives import Primitives
from aisle.monolith.proxy import PrimitiveClient
from aisle.monolith.requests import PrimitiveRequestError, PrimitiveRequests
from aisle.nodes import l2_pose
from aisle.verifier import models, stages

pytestmark = pytest.mark.unit


@pytest.fixture
def detector(monkeypatch):
    calls = []
    model_pair = object()

    def load(role):
        calls.append(("load", role))
        return model_pair

    def detect(rgb, names, *, model_pair):
        calls.append(("detect", rgb.copy(), tuple(names), model_pair))
        return [{"label": "ibuprofen", "score": 0.5, "box": [10, 20, 60, 65]}]

    def project(depth, calibration, pixels):
        pixels = np.asarray(pixels)
        return np.stack(
            [pixels[:, 0] / 1000.0, pixels[:, 1] / 1000.0, depth[pixels[:, 1], pixels[:, 0]]],
            axis=1,
        )

    monkeypatch.setattr(models, "load_pinned", load)
    monkeypatch.setattr(models, "detect_meds", detect)
    monkeypatch.setattr(stages, "backproject_overhead", project)
    return calls, model_pair


def _frames():
    rgb = np.full((100, 100, 3), 200, dtype=np.uint8)
    depth = np.full((100, 100), 0.6, dtype=np.float32)
    return rgb, depth


def test_l2_factory_reuses_pinned_model_with_independent_pose_state(detector):
    """MON-4/BND-2: use the existing RGB estimator and thresholds, without L1 masks."""
    calls, model_pair = detector
    primitives = Primitives._load("franka")
    assert calls == []  # An L1 client does not load an unused perception model.
    first = primitives.l2_pose_session()
    second = primitives.l2_pose_session()
    assert type(first) is type(second) is l2_pose.L2Session
    assert first is not second
    assert first.margin_floor == l2_pose.MARGIN_FLOOR
    assert first.retry_gap_ns == l2_pose.MIN_RETRY_GAP_NS
    assert not hasattr(first, "on_seg")
    assert calls == [("load", "identity")]
    assert first.on_target_request({"target_med": "ibuprofen"})
    assert second.target is None
    rgb, _ = _frames()
    first.detector(rgb)
    assert calls[-1][0] == "detect"
    np.testing.assert_array_equal(calls[-1][1], rgb)
    assert calls[-1][3] is model_pair


def test_l2_proxy_matches_direct_estimate_and_refuses_segmentation(detector):
    """MON-4/MON-8/BND-2: real request/proxy calls preserve RGB results and deny masks."""
    primitives = Primitives._load("franka")
    service = PrimitiveRequests(primitives)
    remote = PrimitiveClient(service.dispatch).root.l2_pose_session()
    direct = primitives.l2_pose_session()
    rgb, depth = _frames()
    results = []
    for session in (direct, remote):
        session.on_bridge_info({"calibration": {}})
        assert session.on_target_request({"target_med": "ibuprofen"})
        assert session.on_depth(100, depth) is None
        results.append(session.on_rgb(100, rgb))
        session.on_reset_done()
        assert session.target is None
    assert results[0] is not None and results[0] == results[1]
    with pytest.raises(AttributeError, match="undeclared"):
        _ = remote.on_seg
    with pytest.raises(PrimitiveRequestError, match="undeclared primitive method"):
        service.dispatch(
            {"op": "call", "handle": remote._handle, "name": "on_seg", "args": [], "kwargs": {}}
        )
    with pytest.raises(PrimitiveRequestError, match="undeclared primitive attribute"):
        service.dispatch(
            {"op": "get", "handle": remote._handle, "name": "detector", "args": [], "kwargs": {}}
        )


def test_integrity_detects_l2_estimator_replacement(monkeypatch):
    """MON-7: the added trusted L2 primitive must participate in callable integrity."""
    integrity = default_integrity()
    integrity.snapshot()
    monkeypatch.setattr(l2_pose.L2Session, "_estimate", lambda *args: None)
    with pytest.raises(ConfinementViolation, match="replaced"):
        integrity.verify()
