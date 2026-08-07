"""VER-9 identity across ALL FIVE med classes, delivered and absent.

PR #104 review, finding 1: the operating point was calibrated from one
class (purple omeprazole) on one golden frame, and a threshold frozen
from a single class is not safe for a GLOBAL wrong-object latch. These
fixtures come from `identity-calib-I2` — one live episode per class, all
five scored `success` by the oracle — cropped to the tray ROI by
`tools/make_identity_fixtures.py`.

Two properties are asserted per class, and the second is the one that
protects VER-3's safety asymmetry:
  1. delivered  -> the TARGET is detected in the tray;
  2. delivered and absent -> NO non-target class is detected, so the
     wrong-object latch never fires on a correct delivery.

Marker `sim`: needs the pinned weights and the committed fixture.
"""

from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.sim

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "verifier" / "identity_classes.npz"
MEDS = ("amoxicillin", "ibuprofen", "cetirizine", "omeprazole", "metformin")


@pytest.fixture(scope="module")
def ctx():
    from aisle.scenes.pharmacy import load_meds, load_physics
    from aisle.verifier.models import load_pinned
    from aisle.verifier.oracle import build_judge_cfg, load_thresholds
    from aisle.verifier.stages import med_box_area_limit

    data = np.load(FIXTURE, allow_pickle=False)
    meds, physics = load_meds(), load_physics()
    cfg = build_judge_cfg(
        physics,
        meds,
        "franka",
        timeout_s=60.0,
        initial_positions=[(0.0, 0.0, 0.0)] * len(meds),
        robot_home_error_rad=0.0,
    )
    thresholds = load_thresholds()["realistic"]
    roi = tuple(float(v) for v in data["roi"])
    return {
        "data": data,
        "meds": meds,
        "roi": roi,
        "offset": tuple(float(v) for v in data["offset"]),
        "min_score": thresholds["identity_min_score"],
        "pair": load_pinned("identity"),
        "limit": med_box_area_limit(
            cfg.tray_min,
            cfg.tray_max,
            {name: spec["size"] for name, spec in meds.items()},
            roi,
            thresholds["identity_max_box_area_slack"],
        ),
    }


def _scores(ctx, key):
    from aisle.verifier.models import detect_meds
    from aisle.verifier.stages import detections_in_roi, shift_detections

    detections = shift_detections(
        detect_meds(ctx["data"][key], list(ctx["meds"]), ctx["pair"]), ctx["offset"]
    )
    return detections_in_roi(detections, ctx["roi"], ctx["min_score"], ctx["limit"])


@pytest.mark.parametrize("med", MEDS)
def test_delivered_target_is_detected_in_the_tray(ctx, med):
    """VER-9: every class must produce a target vote when it IS the
    delivery — the vocabulary cannot work for purple boxes only."""
    scores = _scores(ctx, f"{med}_present")
    assert med in scores, f"{med} delivered but not detected: {scores}"


@pytest.mark.parametrize("med", MEDS)
def test_delivered_target_sets_no_wrong_object_latch(ctx, med):
    """VER-9's latch is GLOBAL and episode-scoped, so a non-target
    surviving on a correct delivery fails the episode permanently. The
    tray-sized artifacts that used to do this (a 'cetirizine' box
    covering 0.774 of the ROI at 0.0474, against a 0.05 threshold) are
    rejected by the size gate."""
    scores = _scores(ctx, f"{med}_present")
    assert set(scores) <= {med}, f"non-target detected on a correct {med} delivery: {scores}"


@pytest.mark.parametrize("med", MEDS)
def test_empty_tray_detects_nothing(ctx, med):
    """The ABSENT half: before the arm has delivered anything, no class
    may be reported in the tray — otherwise the latch fires before the
    episode starts."""
    assert _scores(ctx, f"{med}_absent") == {}
