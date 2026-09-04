"""Unit tests for the A4 agent-comparison orchestrator
(ADR-a4-protocol) — no dora, no sim, no agent CLIs (CON-12)."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

pytestmark = pytest.mark.unit


def test_plan_matches_the_adr():
    """ADR-a4: claude before codex (direction-of-bias), identical
    desk-T1 budgets, both arms mapped to their default models."""
    from a4_protocol import AGENTS, BUDGET
    from h1_protocol import DEFAULT_MODELS

    assert AGENTS == ("claude", "codex")
    assert BUDGET == {"tokens": 400_000, "episodes": 40, "wall_h": 2.5}
    assert set(AGENTS) <= set(DEFAULT_MODELS)


def test_refusals_are_json_on_stdout():
    """CON-8: missing dora identity and unknown agents refuse."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "a4_protocol.py"), "--commit", head],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode != 0 and "expect-dora-sha256" in proc.stdout
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "a4_protocol.py"),
            "--commit",
            head,
            "--agents",
            "claude,kimi",
            "--expect-dora-sha256",
            "0" * 64,
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode != 0 and "bad --agents" in proc.stdout


def test_arm_lane_passes_the_full_ceilings_contract(tmp_path, monkeypatch):
    """The #218 lesson, pinned here too: run_session requires the full
    ceilings contract including prior_wall_s."""
    import a4_protocol as a4

    seen = {}

    def fake_run_session(agent, cmd, wt, out, ceilings, env=None, environment_record=None):
        seen.update(ceilings)
        return {"stopped": "agent_done", "rc": 0, "tokens": 1, "wall_s": 1.0}

    monkeypatch.setattr(a4, "make_worktree", lambda oid, wt: wt.mkdir(parents=True))
    monkeypatch.setattr(
        a4,
        "isolated_session_env",
        lambda out, env_baseline_oid: ({}, {"ambient_baseline": {}}),
    )
    monkeypatch.setattr(a4, "seed_session_credentials", lambda agent, env: ({}, None))
    monkeypatch.setattr(a4, "run_session", fake_run_session)
    monkeypatch.setattr(a4, "campaign_metrics", lambda wt, t0, pin=None: {"rollouts": []})
    monkeypatch.setattr(a4, "score_holdout", lambda wt, seeds, tag, tier: {"ok": True})
    monkeypatch.setattr(a4, "scrub_session_credentials", lambda home: [])
    monkeypatch.setattr(a4, "sweep_worktree", lambda wt: [])
    record = a4.run_arm("claude", "oid", tmp_path, 1.0)
    assert "infra_error" not in record, record
    assert set(seen) >= {"prior_tokens", "prior_wall_s", "token_ceiling", "wall_ceiling_s"}
