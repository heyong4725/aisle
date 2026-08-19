"""Unit tests for the A5 fleet-scaling orchestrator (ADR-a5-protocol;
design doc §8.4.3, §6 A5) — no dora, no sim, no agent CLI (CON-12)."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

pytestmark = pytest.mark.unit


def test_plan_matches_the_adr():
    """ADR-a5: fleets 1/4/8 sequential; per-agent budgets are the desk
    T1 split, identical across configs so per-agent economics compare."""
    from a5_fleet import AGENT_BUDGET, FLEETS

    assert FLEETS == (1, 4, 8)
    assert AGENT_BUDGET == {"tokens": 400_000, "episodes": 40, "wall_h": 2.5}


def run_cli(*extra):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "a5_fleet.py"), *extra],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def test_refuses_without_dora_identity_and_commit():
    """CON-8 + ADR-h3 §5 inherited: the operator must assert both the
    pin and the pin-era dora CLI hash; refusals are JSON on stdout."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    proc = run_cli("--commit", head)
    assert proc.returncode != 0
    assert "expect-dora-sha256" in proc.stdout
    proc = run_cli("--commit", head, "--expect-dora-sha256", "0" * 64, "--fleets", "0,4")
    assert proc.returncode != 0
    assert "bad --fleets" in proc.stdout


def test_infra_error_is_the_agents_record_not_the_configs():
    """ADR-a5: one agent's infra failure records that agent's cell and
    does not raise out of the config."""
    from a5_fleet import run_agent

    # a bogus OID makes make_worktree fail inside the lane
    record = run_agent(0, 4, "not-a-real-oid", Path("/nonexistent/dir"), 1.0)
    assert record["agent_index"] == 0 and record["fleet"] == 4
    assert "infra_error" in record


def test_agent_lane_passes_the_full_ceilings_contract(tmp_path, monkeypatch):
    """A5 live failure (first launch): run_session requires prior_wall_s
    alongside prior_tokens -- a missing key KeyError'd EVERY lane into
    infra_error in seconds. Pin the exact ceilings contract by driving a
    lane with run_session stubbed to record its arguments."""
    import a5_fleet as a5

    seen = {}

    def fake_run_session(agent, cmd, wt, out, ceilings, env=None):
        seen.update(ceilings)
        return {"stopped": "agent_done", "rc": 0, "tokens": 1, "wall_s": 1.0}

    monkeypatch.setattr(a5, "make_worktree", lambda oid, wt: wt.mkdir(parents=True))
    monkeypatch.setattr(a5, "isolated_session_env", lambda out, env_baseline_oid: ({}, {}))
    monkeypatch.setattr(a5, "seed_session_credentials", lambda agent, env: ({}, None))
    monkeypatch.setattr(a5, "run_session", fake_run_session)
    monkeypatch.setattr(a5, "campaign_metrics", lambda wt, t0, pin=None: {"rollouts": []})
    monkeypatch.setattr(a5, "scrub_session_credentials", lambda home: [])
    monkeypatch.setattr(a5, "sweep_worktree", lambda wt: [])
    record = a5.run_agent(0, 1, "oid", tmp_path, 1.0)
    assert "infra_error" not in record, record
    assert set(seen) >= {"prior_tokens", "prior_wall_s", "token_ceiling", "wall_ceiling_s"}


def test_peer_links_are_readonly_views_of_other_lanes_ideas(tmp_path):
    """ENPIRE follow-up 4: each lane sees every OTHER lane's idea tree
    via symlink (live, append-only), never its own."""
    from a5_fleet import link_peers

    for k in range(3):
        (tmp_path / f"worktree_{k}" / "runs" / "ideas").mkdir(parents=True)
    (tmp_path / "worktree_1" / "runs" / "ideas" / "i.jsonl").write_text('{"id":"I1"}\n')
    link_peers(tmp_path, 3)
    view = tmp_path / "worktree_0" / "peers" / "agent_1" / "ideas"
    assert view.is_symlink() and (view / "i.jsonl").read_text() == '{"id":"I1"}\n'
    assert not (tmp_path / "worktree_0" / "peers" / "agent_0").exists()


def test_summary_collection_reads_the_distilled_note(tmp_path):
    from a5_fleet import collect_summary

    assert collect_summary(tmp_path) is None
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "summary.md").write_text("BC-reg helped")
    assert collect_summary(tmp_path) == "BC-reg helped"


def test_multiple_concurrent_codex_lanes_refuse():
    """The T2 attempt-1 lesson (2026-08-18): codex rotates single-use
    refresh tokens — two lanes from one auth.json burn the campaign
    login. More than one concurrent codex lane refuses (CON-8)."""
    import subprocess as sp

    head = sp.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    proc = sp.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "a5_fleet.py"),
            "--commit",
            head,
            "--fleets",
            "4",
            "--lane-agents",
            "claude,codex",
            "--expect-dora-sha256",
            "0" * 64,
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode != 0 and "codex lanes" in proc.stdout
