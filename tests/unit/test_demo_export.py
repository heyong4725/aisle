"""Unit tests for demo_export's pure alignment (arc 3; CON-12 — no
traces, no lerobot)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import demo_export as de  # noqa: E402

pytestmark = pytest.mark.unit


def test_actions_pair_with_latest_frame_at_or_before():
    """The policy must learn from the observation it WOULD have had:
    each action maps to the newest frame not after it."""
    frames = [0, 100, 200, 300]
    actions = [50, 100, 150, 250, 999]
    assert de.align_to_frames(actions, frames) == [0, 1, 1, 2, 3]


def test_actions_before_any_frame_are_flagged_for_dropping():
    """An action with no preceding frame gets -1 — never paired with a
    FUTURE observation (causality, the same rule the read barrier and
    staleness floor enforce elsewhere)."""
    assert de.align_to_frames([5, 20], [10, 30]) == [-1, 0]
