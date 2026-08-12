"""Unit coverage for the recorder fixtures' capture-window state machine
(issue #160, from the PR #159 review: the RECORDER_AWAIT semantics that fixed
issue #94 were previously testable only through full graph runs). Cites the
issue #94 contract: a capture window MUST NOT close before its awaited
protocol rows, the Nth row re-anchors the tail, and a sim-denominated
horizon (CON-5 layer (c) windows) holds the window when rtf collapses."""

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MOD_PATH = Path(__file__).parent.parent / "fixtures" / "nodes" / "recorder_window.py"
_spec = importlib.util.spec_from_file_location("recorder_window", _MOD_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["recorder_window"] = _mod
_spec.loader.exec_module(_mod)
CaptureWindow = _mod.CaptureWindow
parse_await_spec = _mod.parse_await_spec


def test_window_opens_at_first_event_not_at_construction():
    """Issue #94: the genesis/taichi build must not eat the capture — the
    duration is anchored at the FIRST observed event."""
    w = CaptureWindow(10.0)
    assert w.observe(100.0) is False  # first contact opens
    assert w.observe(109.0) is False  # inside the window
    assert w.observe(110.5) is True  # past first-contact + duration


def test_unbounded_window_never_closes():
    """base_recorder's historical default (no RECORDER_DURATION_S): record
    until teardown."""
    w = CaptureWindow(None)
    assert w.observe(0.0) is False
    assert w.observe(1e9) is False


def test_await_holds_window_past_wall_deadline_until_nth_row():
    """Issue #94: a wall-only window guesses when load-stretched events
    land; with an await, the deadline CANNOT expire before the Nth row."""
    w = CaptureWindow(5.0, await_topic="reset_done", await_count=2, await_tail_s=1.0)
    w.observe(0.0)
    w.on_recorded("reset_done", 1_000, 1.0)
    assert w.observe(50.0) is False  # deadline long past; 1 of 2 rows
    w.on_recorded("other_topic", 2_000, 50.0)
    assert w.observe(51.0) is False  # other topics do not satisfy the await
    w.on_recorded("reset_done", 3_000, 51.0)
    assert w.observe(51.5) is False  # inside the re-anchored tail
    assert w.observe(52.5) is True  # tail elapsed after the Nth row


def test_nth_awaited_row_reanchors_tail_never_shortens():
    """The Nth row guarantees now + tail; an EARLY Nth row must not pull
    the deadline before the original window end."""
    w = CaptureWindow(10.0, await_topic="t", await_count=1, await_tail_s=1.0)
    w.observe(0.0)
    w.on_recorded("t", 1, 2.0)  # early: 2 + 1 < 10
    assert w.observe(9.0) is False  # original window still governs
    assert w.observe(10.5) is True


def test_sim_horizon_holds_window_until_sim_advances():
    """PR #159 review: a wall tail under-covers SIM time when rtf
    collapses under load — the sim horizon holds the window until the
    recorded stamps advance await_sim_ns past the Nth row's stamp."""
    w = CaptureWindow(1.0, await_topic="t", await_count=1, await_tail_s=0.5, await_sim_ns=1_000)
    w.observe(0.0)
    w.on_recorded("t", 5_000, 0.5)
    assert w.observe(60.0) is False  # wall long past; sim stuck at 5_000
    w.on_recorded("pose", 5_900, 60.0)
    assert w.observe(61.0) is False  # sim advanced, but short of 6_000
    w.on_recorded("pose", 6_000, 61.0)
    assert w.observe(62.0) is True


def test_unstamped_awaited_row_falls_back_to_wall_tail():
    """An unstamped awaited row cannot anchor a sim horizon; the wall
    tail then governs alone (issue #94 fix, kept honest)."""
    w = CaptureWindow(1.0, await_topic="t", await_count=1, await_tail_s=2.0, await_sim_ns=1_000)
    w.observe(0.0)
    w.on_recorded("t", None, 5.0)  # no usable stamp
    assert w.sim_target is None
    assert w.observe(6.5) is False  # inside the wall tail
    assert w.observe(7.5) is True


@pytest.mark.parametrize("spec", ["x", "topic:", ":3", "topic:0", "topic:abc", "topic:-1"])
def test_malformed_await_spec_raises_loudly(spec):
    """A silent recorder death burns the settle helper's whole outer
    deadline with an opaque empty capture — malformed specs fail LOUDLY
    at parse (PR #159 review)."""
    with pytest.raises(ValueError, match="RECORDER_AWAIT"):
        parse_await_spec(spec)


def test_empty_spec_disables_await():
    assert parse_await_spec("") == ("", 0)
    topic, count = parse_await_spec("violation:1")
    assert (topic, count) == ("violation", 1)
