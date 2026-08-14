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
