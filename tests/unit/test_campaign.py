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
        json.dumps({"type": "assistant"}),  # no message/usage: skipped
        json.dumps({"type": "assistant", "message": {"usage": None}}),
        json.dumps(
            {
                "type": "assistant",
                "message": {"usage": {"input_tokens": "junk", "output_tokens": -5}},
            }
        ),  # malformed and negative components count 0, never crash/reduce
        "not json",
    ]
    assert parse_usage_claude(lines) == (2 + 16670 + 7) + (230 + 11)  # cache reads excluded


def test_codex_usage_counts_new_tokens_only_per_completed_turn():
    """Codex new-tokens-only: input INCLUDES the cached slice, so new
    input = input - cached, plus output; turn.completed events only
    (item.started duplicates never carry usage, PR #33); the anomalous
    cached > input case clamps to output-only instead of going NEGATIVE
    and offsetting real spend (PR #41 review); malformed lines and
    usage-less events are skipped."""
    lines = [
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 500, "cached_input_tokens": 300, "output_tokens": 20},
            }
        ),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message"}}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 800, "output_tokens": 5}}),
        # cached > input: contributes only its output, never a negative
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 100, "cached_input_tokens": 250, "output_tokens": 10},
            }
        ),
        json.dumps({"type": "turn.completed"}),  # no usage at all
        "not json",
    ]
    assert parse_usage_codex(lines) == ((500 - 300) + 20) + (800 + 5) + 10


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
    assert t["agent_cli_version"]  # probed or "not-installed", never empty
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


def test_campaign_treatment_survives_absent_cli():
    """PR #41 review: the version probe must not require the agent CLI
    (CI has none) — an absent binary records "not-installed" and a real
    campaign fails later at spawn with a proper InfraError."""
    t = campaign_treatment("definitely-not-a-cli", "m", "b" * 40, "0..9", "20..27")
    assert t["agent_cli_version"] == "not-installed"


def test_usage_counter_is_incremental_and_tamper_immune(tmp_path):
    """Issue #42: the runner is the SOLE authority on token spend — it
    accumulates from the live stream in memory; the on-disk log is a tee.
    Feeding lines incrementally equals batch parsing, and destroying the
    log file afterwards does not change the count."""
    from campaign import UsageCounter

    lines = [
        json.dumps(
            {"type": "assistant", "message": {"usage": {"input_tokens": 10, "output_tokens": 2}}}
        ),
        "not json",
        json.dumps(
            {"type": "assistant", "message": {"usage": {"input_tokens": 30, "output_tokens": 4}}}
        ),
    ]
    counter = UsageCounter("claude")
    for line in lines:
        counter.feed(line)
    assert counter.total == parse_usage_claude(lines) == 46
    log = tmp_path / "session.jsonl"
    log.write_text("\n".join(lines))
    log.write_text("")  # session truncates its own log: count unaffected
    assert counter.total == 46


def test_run_session_counts_from_pipe_and_kills_at_ceiling(tmp_path, monkeypatch):
    """Issue #42 end-to-end with a FAKE agent process: the runner counts
    usage from the live pipe (log file is a tee, still written), and the
    token ceiling kills the session (stopped=token_budget) even though
    the fake agent would otherwise sleep forever."""
    import campaign as c

    monkeypatch.setattr(c, "POLL_S", 0.1)
    fake_agent = (
        "import json,sys,time\n"
        "print(json.dumps({'type':'assistant','message':{'usage':"
        "{'input_tokens':5000,'output_tokens':100}}}), flush=True)\n"
        "time.sleep(60)\n"
    )
    out = tmp_path / "out"
    out.mkdir()
    session = c.run_session(
        "claude",
        [sys.executable, "-u", "-c", fake_agent],
        tmp_path,
        out,
        {
            "prior_tokens": 0,
            "prior_wall_s": 0.0,
            "token_ceiling": 1000,
            "wall_ceiling_s": 3600.0,
        },
    )
    assert session["stopped"] == "token_budget"
    assert session["tokens"] == 5100
    # the tee still captured the stream for post-hoc analysis
    assert "input_tokens" in (out / "session.jsonl").read_text()


def test_run_session_agent_done_on_clean_exit(tmp_path, monkeypatch):
    """A session that finishes under budget records agent_done with the
    pipe-accumulated total."""
    import campaign as c

    monkeypatch.setattr(c, "POLL_S", 0.1)
    fake_agent = (
        "import json\n"
        "print(json.dumps({'type':'assistant','message':{'usage':"
        "{'input_tokens':7,'output_tokens':3}}}), flush=True)\n"
    )
    out = tmp_path / "out"
    out.mkdir()
    session = c.run_session(
        "claude",
        [sys.executable, "-u", "-c", fake_agent],
        tmp_path,
        out,
        {
            "prior_tokens": 0,
            "prior_wall_s": 0.0,
            "token_ceiling": 1000,
            "wall_ceiling_s": 3600.0,
        },
    )
    assert session["stopped"] == "agent_done"
    assert session["tokens"] == 10
