"""Unit tests for tools/h4_iteration.py (SPEC 070 HAR-12; design doc
§8.3 item 5, §6 H4; ADR-h4-iteration-protocol). Pure metric logic —
no sim, no dora."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from h4_iteration import (  # noqa: E402
    analyze,
    credited_episode,
    episode_starts,
    latency_from_record,
    timeline_from_polls,
)

pytestmark = pytest.mark.unit


def test_episode_starts_chain_from_stream_start():
    """ADR-h4 §4: episode i starts when episode i-1's result lands;
    episode 0 starts at the stream's start."""
    assert episode_starts([110.0, 150.0, 190.0], 100.0) == [100.0, 110.0, 150.0]
    assert episode_starts([], 100.0) == []


def test_credited_episode_excludes_the_straddler():
    """HAR-12 bounded by ADR-h4 §3: an episode already RUNNING when the
    change lands is never credited (the shakeout showed a straddling
    episode can fail from the swap itself) — the first episode whose
    start is at/after the change is the measured one."""
    timeline = [110.0, 150.0, 190.0]  # ep1 runs 110..150
    # change lands at 120, mid-episode-1: ep1 is a straddler, ep2 counts
    assert credited_episode(timeline, 100.0, 120.0) == (2, 190.0)
    # change lands exactly at ep1's start: ep1 counts
    assert credited_episode(timeline, 100.0, 110.0) == (1, 150.0)
    # change after every completed start: nothing creditable yet
    assert credited_episode(timeline, 100.0, 195.0) is None
    assert credited_episode([], 100.0, 90.0) is None


def test_latency_is_idea_to_credited_result():
    """The HAR-12 metric: idea-open ts -> the credited episode's result
    ts (NOT the change ts — the agent's decision starts the clock)."""
    rec = {
        "timeline": [110.0, 150.0, 190.0],
        "stream_t0": 100.0,
        "t_idea": 115.0,
        "change_ok_ts": 120.0,
    }
    assert latency_from_record(rec) == pytest.approx(75.0)  # 190 - 115
    assert latency_from_record({**rec, "change_ok_ts": 195.0}) is None


def test_timeline_from_polls_first_observation_wins():
    """Result ts of episode i = the first poll at which the results
    stream showed more than i lines (0.5 s poll resolution, ADR-h4)."""
    poll = [(1.0, 0), (2.0, 1), (3.0, 1), (4.0, 3), (5.0, 3)]
    assert timeline_from_polls(poll) == [2.0, 4.0, 4.0]
    assert timeline_from_polls([]) == []


def test_analyze_recomputes_from_raw_records_only():
    """The published table derives from the record (medians, min/max,
    the relaunch/hotswap ratio); an uncreditable rep is reported as
    failed, never silently dropped into the average."""

    def rec(path, t_idea, result):
        return {
            "path": path,
            "timeline": [result],
            "stream_t0": t_idea + 1.0,  # episode started after the change
            "t_idea": t_idea,
            "change_ok_ts": t_idea + 0.5,
        }

    records = [
        rec("relaunch", 0.0, 100.0),
        rec("relaunch", 0.0, 120.0),
        rec("hotswap", 0.0, 40.0),
        rec("hotswap", 0.0, 60.0),
        # a hotswap rep whose change landed after every start: failed
        {
            "path": "hotswap",
            "timeline": [10.0],
            "stream_t0": 0.0,
            "t_idea": 5.0,
            "change_ok_ts": 50.0,
        },
    ]
    table = analyze(records)
    assert table["relaunch"]["median_s"] == pytest.approx(110.0)
    assert table["hotswap"]["median_s"] == pytest.approx(50.0)
    assert table["hotswap"]["failed"] == 1 and table["hotswap"]["n"] == 2
    assert table["median_ratio_relaunch_over_hotswap"] == pytest.approx(2.2)


def test_stale_pid_selection_leaves_unrelated_processes_alone():
    """PR #79 review P1: orphan reaping must be scoped to THIS
    dataflow's own node pids whose live command line references THIS
    checkout — an unrelated AISLE experiment (other pid, other
    checkout) must never be selected."""
    from h4_iteration import stale_node_pids

    entries = [
        {"node": "grasp-planner-topdown", "pid": "111"},
        {"node": "dora-genesis", "pid": "222"},
        {"node": "bad", "pid": "not-a-pid"},
    ]
    ps = [
        "111 /this/checkout/.venv/bin/python3 /this/checkout/src/aisle/nodes/grasp_topdown.py",
        "222 /this/checkout/.venv/bin/python3 /this/checkout/src/aisle/nodes/dora_genesis.py",
        "333 /other/checkout/.venv/bin/python3 /other/checkout/src/aisle/nodes/dora_genesis.py",
        "444 /this/checkout/.venv/bin/python3 /this/checkout/src/aisle/nodes/oracle_pose.py",
    ]
    # 333: unrelated checkout — excluded even though it is an AISLE node.
    # 444: this checkout but NOT one of this dataflow's snapshot pids.
    assert stale_node_pids(entries, ps, "/this/checkout") == [111, 222]
    # a snapshot pid whose process is gone (not in ps) is not selected
    assert stale_node_pids([{"pid": "999"}], ps, "/this/checkout") == []


def test_runner_has_no_global_pkill():
    """The reaping path must be pid-scoped — a global pattern kill can
    terminate concurrent campaigns (PR #79 review P1)."""
    src = (REPO_ROOT / "tools" / "h4_iteration.py").read_text()
    assert "pkill" not in src
    assert "stale_node_pids(" in src


def test_order_and_phase_are_seeded_not_fixed():
    """PR #79 review P1 (phase lock): the runner must randomize the
    path order and the idea-arrival phase from a recorded seed — a
    fixed R,H order with instant idea logging pins every hot-swap rep
    to the worst arrival phase."""
    src = (REPO_ROOT / "tools" / "h4_iteration.py").read_text()
    assert "rng.shuffle(order)" in src
    assert "rng.uniform(0.0, 25.0)" in src
    assert "phase_delay_s" in src
