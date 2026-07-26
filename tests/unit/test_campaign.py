"""Unit tests for tools/campaign.py (ADR-h2-campaign-protocol; design doc
§8.3 item 6, hypothesis H2). Pure runner logic — no sim, no agent CLIs."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from campaign import (  # noqa: E402
    audit_frozen,
    budget_stop,
    campaign_metrics,
    campaign_prompt,
    campaign_treatment,
    parse_usage_claude,
    parse_usage_codex,
    validate_seed_ranges,
)

pytestmark = pytest.mark.unit


def test_claude_usage_counts_new_tokens_only():
    """ADR-h2 point 3 (HAR-5, dry-run decision): spend = NEW tokens only
    (input + cache_creation + output, cache re-reads excluded). Counting
    only `input_tokens` read 856 for a 91-message session; counting
    cache reads read 5.49M for 18 minutes — either extreme breaks the
    5M budget's design-era meaning."""
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 2,
                        "cache_creation_input_tokens": 16670,
                        "cache_read_input_tokens": 15105,
                        "output_tokens": 7,
                    }
                },
            }
        ),
        json.dumps({"type": "user", "message": {"content": "tool result"}}),
        json.dumps(
            {"type": "assistant", "message": {"usage": {"input_tokens": 230, "output_tokens": 11}}}
        ),
        "not json",
    ]
    assert parse_usage_claude(lines) == (2 + 16670 + 7) + (230 + 11)  # cache reads excluded


def test_codex_usage_accumulates_per_completed_turn():
    """Codex telemetry: turn.completed events carry the turn's usage;
    item.started duplicates must not double-count (the PR #33 parser
    lesson applies here too)."""
    turn = {
        "type": "turn.completed",
        "usage": {"input_tokens": 500, "cached_input_tokens": 300, "output_tokens": 20},
    }
    lines = [
        json.dumps({"type": "turn.started"}),
        json.dumps(turn),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 800, "output_tokens": 5}}),
    ]
    # codex's input_tokens INCLUDES the cached slice: new input = input
    # minus cached, mirroring the claude new-tokens-only rule
    assert parse_usage_codex(lines) == (500 - 300) + 20 + 800 + 5


def test_budget_stop_reasons():
    """ADR-h2 point 3: the runner kills at the token ceiling or the
    campaign wall ceiling, whichever first; below both it keeps going."""
    assert budget_stop(tokens=10, token_ceiling=100, wall_s=5.0, wall_ceiling_s=100.0) is None
    assert budget_stop(tokens=100, token_ceiling=100, wall_s=5.0, wall_ceiling_s=100.0) == (
        "token_budget"
    )
    assert budget_stop(tokens=10, token_ceiling=100, wall_s=100.0, wall_ceiling_s=100.0) == (
        "wall_budget"
    )


def test_seed_ranges_must_be_disjoint():
    """ADR-h2 point 4: held-out scoring seeds may never overlap the dev
    range the agent rolled — overlap is a refusal, not a warning."""
    assert validate_seed_ranges("0..49", "100..107") is None
    error = validate_seed_ranges("0..49", "40..55")
    assert error is not None and "disjoint" in error


def test_campaign_prompt_names_contract_budgets_and_deliverable():
    """ADR-h2 point 1: the session prompt names the tier goal, the
    deliverable graph path, the budgets, and points at the research
    contract rather than restating it."""
    prompt = campaign_prompt(tier="T1", token_ceiling=5_000_000, wall_h=40.0, dev_seeds="0..49")
    assert "harness/CLAUDE.research.md" in prompt
    assert "graphs/agent_campaign.yaml" in prompt
    assert "T1" in prompt and "5,000,000" in prompt and "0..49" in prompt
    assert "Do NOT run rollouts" not in prompt  # H1's restriction must not leak


def test_campaign_treatment_pins_seed_ranges_and_unsandboxed_spawn():
    """ADR-h2 points 5+8: treatment records the seed ranges and the v1
    no-sandbox decision; there is no claude turn cap (campaigns are
    long-form)."""
    t = campaign_treatment("claude", "claude-fable-5", "a" * 40, "0..49", "100..107")
    assert t["dev_seeds"] == "0..49" and t["holdout_seeds"] == "100..107"
    assert t["session_spawn"]["confinement"] == "none (ADR-h2 point 5)"
    assert t.get("claude_max_turns") is None
    assert t["runner_sha256"]


def _write_run(wt: Path, run_id: str, mtime: float, episodes: list[dict]) -> None:
    run_dir = wt / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"run_id": run_id, "tier": "T1"}))
    ep = run_dir / "episodes.jsonl"
    ep.write_text("".join(json.dumps(e) + "\n" for e in episodes))
    import os

    os.utime(ep, (mtime, mtime))
    os.utime(run_dir / "manifest.json", (mtime, mtime))


def test_campaign_metrics_trajectory_and_first_success(tmp_path):
    """ADR-h2 point 7: pass1 trajectory in chronological run order,
    time-to-first-verified-success from the first success's artifact
    mtime, wrong_object totalled across every episode (H5: must be 0 and
    must never be silently dropped)."""
    t0 = 1_000_000.0
    _write_run(
        tmp_path,
        "r1",
        t0 + 60,
        [
            {"episode": 0, "status": "fail", "failure": "never_grasped"},
            {"episode": 1, "status": "fail", "failure": "timeout"},
        ],
    )
    _write_run(
        tmp_path,
        "r2",
        t0 + 120,
        [
            {"episode": 0, "status": "success", "failure": None},
            {"episode": 1, "status": "fail", "failure": "wrong_object"},
        ],
    )
    metrics = campaign_metrics(tmp_path, session_t0=t0)
    assert [r["run_id"] for r in metrics["rollouts"]] == ["r1", "r2"]
    assert metrics["rollouts"][0]["pass1"] == 0.0
    assert metrics["rollouts"][1]["pass1"] == 0.5
    assert metrics["first_success_wall_s"] == pytest.approx(120.0)
    assert metrics["wrong_object_total"] == 1
    assert metrics["episodes_total"] == 4


def test_campaign_metrics_no_success(tmp_path):
    _write_run(tmp_path, "r1", 1_000_100.0, [{"episode": 0, "status": "fail", "failure": "x"}])
    metrics = campaign_metrics(tmp_path, session_t0=1_000_000.0)
    assert metrics["first_success_wall_s"] is None


def test_audit_frozen_detects_drift(tmp_path):
    """ADR-h2 point 5: the post-session audit diffs the frozen paths in
    the worktree against the pinned OID — any drift is reported."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    frozen = repo / "src" / "aisle" / "verifier"
    frozen.mkdir(parents=True)
    (frozen / "oracle.py").write_text("original\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )
    oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    assert audit_frozen(repo, oid) == []
    (frozen / "oracle.py").write_text("tampered\n")
    drift = audit_frozen(repo, oid)
    assert drift and "src/aisle/verifier/oracle.py" in drift[0]
