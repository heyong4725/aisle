"""Unit tests for the MRU decomposition (ENPIRE follow-up 1) — pure
parts, synthetic filesystem (CON-12)."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import mru_report as mr  # noqa: E402

pytestmark = pytest.mark.unit


def _touch(p: Path, mtime: float) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    os.utime(p, (mtime, mtime))


def test_lane_report_decomposes_session_wall(tmp_path):
    """sim_utilization = rollout wall / session wall; rollout spans come
    from run-dir file mtimes CLIPPED to the session window (a run that
    started before the session cannot donate pre-session time)."""
    t0 = 1_000_000.0
    wt = tmp_path / "wt"
    _touch(wt / "runs" / "r1" / "a.json", t0 + 10)
    _touch(wt / "runs" / "r1" / "b.json", t0 + 110)  # 100 s rollout
    _touch(wt / "runs" / "r2" / "a.json", t0 - 50)  # pre-session start
    _touch(wt / "runs" / "r2" / "b.json", t0 + 50)  # clipped -> 50 s
    record = {
        "agent_index": 0,
        "session_start_epoch": t0,
        "session": {"wall_s": 300.0, "tokens": 1000},
    }
    lane = mr.lane_report(record, wt)
    assert lane["rollout_wall_s"] == 150.0
    assert lane["sim_utilization"] == 0.5
    assert lane["think_wait_s"] == 150.0
    assert lane["rollouts_in_session"] == 2


def test_lane_report_refuses_without_session_evidence(tmp_path):
    assert mr.lane_report({"session": {}}, tmp_path) is None
