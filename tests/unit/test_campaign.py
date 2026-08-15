"""Unit tests for tools/campaign.py (ADR-h2-campaign-protocol; design doc
§8.3 item 6, hypothesis H2). Pure runner logic — no sim, no agent CLIs."""

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from campaign import (  # noqa: E402
    attach_historical_baseline_compat,
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


def test_historical_worktree_gets_recorded_baseline_compat(tmp_path):
    """HAR-2 / CON-5 (PR #166 review): `--commit` may predate native
    AISLE_ENV_BASELINE/OID support.  The CURRENT runner must put its
    compatibility hook on that session's Python path; setting only the
    variable leaves an old in-worktree `uv run harness` unchanged."""
    wt = tmp_path / "worktree"
    cli = wt / "src" / "aisle" / "harness" / "cli.py"
    rollout = wt / "src" / "aisle" / "harness" / "rollout.py"
    cli.parent.mkdir(parents=True)
    cli.write_text('roll.add_argument("--env-baseline", default="origin/main")\n')
    rollout.write_text("def resolve_trusted_baseline(root): pass\ndef run_gates(root): pass\n")
    session = tmp_path / "session_00"
    session.mkdir()
    env = {"PYTHONPATH": "/ambient/operator/path"}
    pin = "a" * 40

    record = attach_historical_baseline_compat(wt, session, pin, env)

    compat_dir = session / "baseline_compat"
    assert env["PYTHONPATH"] == str(compat_dir)  # ambient path is not a treatment input
    assert (compat_dir / "sitecustomize.py").is_file()
    assert record == {
        "mode": "injected",
        "pin": pin,
        "pythonpath": str(compat_dir),
        "sha256": record["sha256"],
    }
    assert len(record["sha256"]) == 64
    assert attach_historical_baseline_compat(wt, session, pin, env) == record


def test_native_worktree_needs_no_baseline_compat(tmp_path):
    """HAR-2: pins at/after PR #166 use their native immutable selector;
    the runner must not unnecessarily inject a second implementation."""
    wt = tmp_path / "worktree"
    cli = wt / "src" / "aisle" / "harness" / "cli.py"
    rollout = wt / "src" / "aisle" / "harness" / "rollout.py"
    cli.parent.mkdir(parents=True)
    cli.write_text('default=os.environ.get("AISLE_ENV_BASELINE", "origin/main")\n')
    rollout.write_text(
        "_COMMIT_OID = object()\ndef resolve_trusted_baseline(root, baseline): pass\n"
    )
    env = {}

    assert attach_historical_baseline_compat(wt, tmp_path / "session", "b" * 40, env) == {
        "mode": "native",
        "pin": "b" * 40,
    }
    assert "PYTHONPATH" not in env


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
    assert len(t["baseline_compat_sha256"]) == 64


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


def _write_provenanced_run(wt, run_id, mtime, episodes, **manifest_extra):
    import os

    run_dir = wt / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"run_id": run_id, **manifest_extra}))
    ep = run_dir / "episodes.jsonl"
    ep.write_text("".join(json.dumps(e) + "\n" for e in episodes))
    os.utime(ep, (mtime, mtime))
    os.utime(run_dir / "manifest.json", (mtime, mtime))


def test_campaign_metrics_records_provenance_and_pins_first_success(tmp_path):
    """PR #76 review (the L/S3-r2 defect): every rollout entry carries
    its manifest's git_sha / env_baseline / env_baseline_oid /
    env_attested so the committed scenario record is auditable, and with
    `pin` set the first-success metric is derived ONLY from
    trusted-baseline rollouts at the treatment pin — a local skill-eval
    success (earlier mtime) must never supply the headline metric."""
    t0 = 1_000_000.0
    success = [{"episode": 0, "status": "success", "failure": None}]
    _write_provenanced_run(
        tmp_path,
        "skill-eval-local",
        t0 + 100,
        success,
        git_sha="PIN",
        env_baseline="local",
        env_baseline_oid=None,
        env_attested=None,
    )
    _write_provenanced_run(
        tmp_path,
        "trusted-at-pin",
        t0 + 500,
        success,
        git_sha="PIN",
        env_baseline="PIN",
        env_baseline_oid="PIN",
        env_attested=True,
    )
    metrics = campaign_metrics(tmp_path, session_t0=t0, pin="PIN")
    assert metrics["first_success_wall_s"] == pytest.approx(500.0)  # NOT the local 100
    by_id = {r["run_id"]: r for r in metrics["rollouts"]}
    assert by_id["skill-eval-local"]["env_baseline"] == "local"
    assert by_id["trusted-at-pin"]["env_baseline_oid"] == "PIN"
    assert by_id["trusted-at-pin"]["env_attested"] is True
    # without a pin (legacy callers) the earliest success still wins
    assert campaign_metrics(tmp_path, session_t0=t0)["first_success_wall_s"] == pytest.approx(100.0)
    # a drifted trusted run (baseline moved past the pin) is inadmissible
    drifted = tmp_path / "drifted"
    _write_provenanced_run(
        drifted,
        "trusted-drifted",
        t0 + 50,
        success,
        git_sha="MERGED",
        env_baseline="origin/main",
        env_baseline_oid="POSTPIN",
        env_attested=True,
    )
    assert campaign_metrics(drifted, session_t0=t0, pin="PIN")["first_success_wall_s"] is None

    # Issue #91: even when main still resolves to the pin, a new
    # campaign metric is admissible only when the selector itself is
    # pinned; this makes an accidental moving default machine-visible.
    moving = tmp_path / "moving"
    _write_provenanced_run(
        moving,
        "trusted-but-moving",
        t0 + 25,
        success,
        git_sha="PIN",
        env_baseline="origin/main",
        env_baseline_oid="PIN",
        env_attested=True,
    )
    assert campaign_metrics(moving, session_t0=t0, pin="PIN")["first_success_wall_s"] is None


def test_score_holdout_no_deliverable_is_structured(tmp_path):
    """PR #76 review: the no-deliverable outcome is a structured field,
    not prose — the analyzer keys on it instead of substring-matching
    the error message."""
    from campaign import score_holdout

    out = score_holdout(tmp_path, "100..107", "W-S1", "S1")
    assert out["ok"] is False
    assert out["outcome"] == "no_deliverable"


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


def test_parsers_skip_non_dict_json_lines():
    """PR #43 review: a line that is valid JSON but not an object (null,
    123) must be skipped like non-JSON — an AttributeError here killed the
    tee thread silently, freezing the counter and disabling the ceiling."""
    lines = [
        "null",
        "123",
        json.dumps(
            {"type": "assistant", "message": {"usage": {"input_tokens": 5, "output_tokens": 1}}}
        ),
    ]
    assert parse_usage_claude(lines) == 6
    assert parse_usage_codex(["null", "[]"]) == 0


def _run(tmp_path, monkeypatch, fake_agent, ceilings=None, join_s=None):
    import campaign as c

    monkeypatch.setattr(c, "POLL_S", 0.1)
    if join_s is not None:
        monkeypatch.setattr(c, "TEE_JOIN_S", join_s)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return c.run_session(
        "claude",
        [sys.executable, "-u", "-c", fake_agent],
        tmp_path,
        out,
        ceilings
        or {
            "prior_tokens": 0,
            "prior_wall_s": 0.0,
            "token_ceiling": 1000,
            "wall_ceiling_s": 3600.0,
        },
    )


def test_run_session_true_tamper_scenario(tmp_path, monkeypatch):
    """The ACTUAL issue #42 attack, end to end: the session truncates the
    runner's own session.jsonl mid-run; the pipe-accumulated count is
    unaffected (PR #43 review: the earlier unit test truncated a scratch
    file the counter never read)."""
    fake_agent = (
        "import json,time\n"
        "print(json.dumps({'type':'assistant','message':{'usage':"
        "{'input_tokens':40,'output_tokens':2}}}), flush=True)\n"
        "time.sleep(0.3)\n"
        "open('out/session.jsonl','w').close()\n"  # the tamper
        "print(json.dumps({'type':'assistant','message':{'usage':"
        "{'input_tokens':7,'output_tokens':1}}}), flush=True)\n"
    )
    session = _run(tmp_path, monkeypatch, fake_agent)
    assert session["stopped"] == "agent_done"
    assert session["tokens"] == 50  # both events counted despite the wipe


def test_run_session_infra_error_on_nonzero_rc(tmp_path, monkeypatch):
    """PR #43 review: a session that dies rc!=0 without a budget stop is
    an infrastructure failure, never an agent outcome."""
    from campaign import InfraError

    with pytest.raises(InfraError):
        _run(tmp_path, monkeypatch, "import sys; sys.exit(3)")


def test_run_session_wall_budget_stop(tmp_path, monkeypatch):
    """PR #43 review: the wall ceiling's kill path, end to end."""
    session = _run(
        tmp_path,
        monkeypatch,
        "import time; time.sleep(60)",
        ceilings={
            "prior_tokens": 0,
            "prior_wall_s": 0.0,
            "token_ceiling": 10**9,
            "wall_ceiling_s": 0.3,
        },
    )
    assert session["stopped"] == "wall_budget"


def test_run_session_empty_and_partial_output(tmp_path, monkeypatch):
    """PR #43 review: a silent session records 0 tokens and agent_done; a
    final unterminated line counts 0 without crashing the tee."""
    session = _run(tmp_path, monkeypatch, "pass")
    assert session["stopped"] == "agent_done" and session["tokens"] == 0
    session = _run(
        tmp_path,
        monkeypatch,
        'import sys; sys.stdout.write(\'{"type":"assist\')',  # partial, no newline
    )
    assert session["stopped"] == "agent_done" and session["tokens"] == 0


def test_run_session_non_utf8_does_not_freeze_counter(tmp_path, monkeypatch):
    """PR #43 review (fail-open regression): strict decoding of non-UTF8
    session bytes killed the tee thread silently, freezing the counter and
    disabling the token ceiling. With errors=replace the stream survives
    and later usage still counts."""
    fake_agent = (
        "import json,sys\n"
        "sys.stdout.buffer.write(b'\\xff\\xfe garbage \\xff\\n')\n"
        "sys.stdout.buffer.flush()\n"
        "print(json.dumps({'type':'assistant','message':{'usage':"
        "{'input_tokens':9,'output_tokens':1}}}), flush=True)\n"
    )
    session = _run(tmp_path, monkeypatch, fake_agent)
    assert session["stopped"] == "agent_done"
    assert session["tokens"] == 10


def test_run_session_prior_spend_counts_toward_ceiling(tmp_path, monkeypatch):
    """ADR-h2 point 6: resumed sessions accumulate — prior spend plus this
    session's live count trips the ceiling even though the session-local
    total is far below it."""
    fake_agent = (
        "import json,time\n"
        "print(json.dumps({'type':'assistant','message':{'usage':"
        "{'input_tokens':90,'output_tokens':10}}}), flush=True)\n"
        "time.sleep(60)\n"
    )
    session = _run(
        tmp_path,
        monkeypatch,
        fake_agent,
        ceilings={
            "prior_tokens": 950,
            "prior_wall_s": 0.0,
            "token_ceiling": 1000,
            "wall_ceiling_s": 3600.0,
        },
    )
    assert session["stopped"] == "token_budget"
    assert session["tokens"] == 100


def test_run_session_escaped_grandchild_join_timeout(tmp_path, monkeypatch, capsys):
    """PR #43 review: a grandchild that setsids out of the process group
    holds the stdout pipe open past killpg — the drain join times out, a
    warning is emitted, and run_session still returns the pre-kill total
    instead of hanging."""
    fake_agent = (
        "import json,os,subprocess,sys\n"
        "print(json.dumps({'type':'assistant','message':{'usage':"
        "{'input_tokens':11,'output_tokens':1}}}), flush=True)\n"
        "subprocess.Popen(['sleep','3'], start_new_session=True,"
        " stdout=sys.stdout.fileno())\n"
    )
    session = _run(tmp_path, monkeypatch, fake_agent, join_s=0.3)
    assert session["stopped"] == "agent_done"
    assert session["tokens"] == 12
    assert "drain incomplete" in capsys.readouterr().err


def test_resolve_campaign_commit(tmp_path):
    """Clean-rerun support: an explicit --commit pins the worktree at a
    historical rev (an uncontaminated pre-analysis commit — the codex H2
    lesson: committed findings of the same experiment are an experimental
    input); default resolves HEAD; unknown revs refuse."""
    from campaign import resolve_commit

    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "f").write_text("1")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    env = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*env, "commit", "-qm", "one"], cwd=repo, check=True)
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    (repo / "f").write_text("2")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run([*env, "commit", "-qm", "two"], cwd=repo, check=True)
    assert resolve_commit(repo, None) != first  # default = HEAD
    assert resolve_commit(repo, first[:8]) == first  # short rev resolves full
    with pytest.raises(SystemExit):
        resolve_commit(repo, "not-a-rev")


def test_sweep_worktree_kills_only_worktree_processes(tmp_path):
    """PR #44 follow-up: campaign rollouts leak dora nodes (the known
    leak, twice from campaign worktrees); the post-session sweep kills
    processes running scripts under the WORKTREE only — never bystanders."""
    import time as _time

    from campaign import sweep_worktree

    wt = tmp_path / "wt"
    wt.mkdir()
    inside = wt / "node_x.py"
    inside.write_text("import time\ntime.sleep(60)\n")
    outside = tmp_path / "bystander.py"
    outside.write_text("import time\ntime.sleep(60)\n")
    p_in = subprocess.Popen([sys.executable, str(inside)])
    p_out = subprocess.Popen([sys.executable, str(outside)])
    try:
        _time.sleep(0.5)
        killed = sweep_worktree(wt)
        _time.sleep(0.5)
        assert p_in.pid in killed
        assert p_in.poll() is not None  # worktree process reaped
        assert p_out.poll() is None  # bystander untouched
    finally:
        for p in (p_in, p_out):
            if p.poll() is None:
                p.kill()


def test_isolated_session_env_points_home_at_scratch(tmp_path):
    """Issue #96: campaign sessions must not inherit the operator's
    config/home — the S3-r3 agent read ~/.claude memory (annotated
    transcript event [21]). The isolation env rebinds HOME and
    CLAUDE_CONFIG_DIR to a per-session scratch home and returns a
    record of both, without mutating the parent environment."""
    import os

    import campaign as c

    before_home = os.environ.get("HOME")
    pin = "a" * 40
    env, rec = c.isolated_session_env(tmp_path / "out", env_baseline_oid=pin)
    assert env["HOME"] == str(tmp_path / "out" / "agent_home")
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "out" / "agent_home" / ".claude")
    assert Path(env["CLAUDE_CONFIG_DIR"]).is_dir()  # created, empty
    assert not any(Path(env["CLAUDE_CONFIG_DIR"]).iterdir())
    assert env["AISLE_ENV_BASELINE"] == pin
    assert os.environ.get("HOME") == before_home  # parent untouched
    assert rec == {
        "home": env["HOME"],
        "claude_config_dir": env["CLAUDE_CONFIG_DIR"],
        "codex_home": env["CODEX_HOME"],
        "xdg_rebound": True,
        "env_baseline_oid": pin,
    }


def test_run_session_spawns_with_the_isolated_env(tmp_path):
    """The session subprocess must SEE the isolation (issue #96) — a
    child that prints its HOME/CLAUDE_CONFIG_DIR proves the env reached
    the spawn, not just the record."""
    import campaign as c

    out = tmp_path / "out"
    out.mkdir()
    env, _ = c.isolated_session_env(out)
    probe = "import os; print(os.environ['HOME']); print(os.environ['CLAUDE_CONFIG_DIR'])"
    c.run_session(
        "claude",
        [sys.executable, "-u", "-c", probe],
        tmp_path,
        out,
        {
            "prior_tokens": 0,
            "prior_wall_s": 0.0,
            "token_ceiling": 10_000,
            "wall_ceiling_s": 30.0,
        },
        env=env,
    )
    log = (out / "session.jsonl").read_text().splitlines()
    assert log[0] == str(out / "agent_home")
    assert log[1] == str(out / "agent_home" / ".claude")


def test_resume_refuses_prior_session_policies(tmp_path):
    """HAR-2 / CON-5: the session policy is resume identity. Both an
    unisolated record and PR #166's env-only v1 can contain moving-baseline
    rollouts, so neither may mix with compatibility-enforced v2 sessions."""
    import campaign as c

    current = c.campaign_treatment("claude", "m", "abc", "0..4", "100..103")
    assert current["session_isolation_policy"] == "isolated-home-baseline-compat-v2"
    for policy in (None, "isolated-home-v1"):
        prior = {k: current[k] for k in c.TREATMENT_IDENTITY}
        if policy is None:
            del prior["session_isolation_policy"]
        else:
            prior["session_isolation_policy"] = policy
        (tmp_path / "campaign.json").write_text(json.dumps({"treatment": prior, "sessions": []}))
        with pytest.raises(SystemExit) as exc:
            c.load_existing(tmp_path, current)
        refusal = json.loads(str(exc.value))
        assert refusal["ok"] is False and "session_isolation_policy" in refusal["error"]


def _campaign_repo(tmp_path):
    """A pinned repo plus a worktree standing in for a campaign session's,
    with `runs/` gitignored exactly as CON-6 has it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "graphs").mkdir()
    (repo / "graphs" / "agent_campaign.yaml").write_text("nodes: []\n")
    (repo / ".gitignore").write_text("runs/*\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "pin"],
        cwd=repo,
        check=True,
    )
    oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    wt = tmp_path / "out" / "worktree"
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(wt), oid], cwd=repo, check=True)
    return repo, wt, oid


def _agent_authors_a_skill(wt):
    """What a campaign session actually leaves behind: UNCOMMITTED working
    -tree changes (audit_frozen diffs the working tree, so the protocol
    never asked the agent to commit), plus fat gitignored run artifacts."""
    skill = wt / "skills" / "t2-scan-pose"
    skill.mkdir(parents=True)
    (skill / "node.py").write_text("# the agent's skill\n")
    (skill / "skill.yaml").write_text("id: t2-scan-pose\norigin: agent-authored\n")
    (wt / "graphs" / "agent_campaign.yaml").write_text("nodes: [best-system]\n")
    traces = wt / "runs" / "r_000"
    traces.mkdir(parents=True)
    (traces / "trace.arrow").write_text("x" * 4096)


def test_archive_captures_the_agents_uncommitted_deliverable(tmp_path):
    """CON-6 (#245): a campaign session's work lives as UNCOMMITTED worktree
    state. Archiving HEAD would capture the pin and nothing the agent did —
    the archive must snapshot the working tree."""
    import campaign as c

    repo, wt, oid = _campaign_repo(tmp_path)
    _agent_authors_a_skill(wt)
    rec = c.archive_deliverable(wt, oid, "h3-desk-L-T2", now="2026-08-15T00:00:00Z")
    assert rec["ok"] and rec["ref"] == "refs/campaign/h3-desk-L-T2"
    listed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", rec["commit"]],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout
    assert "skills/t2-scan-pose/node.py" in listed
    assert (
        "nodes: [best-system]"
        in subprocess.run(
            ["git", "show", f"{rec['commit']}:graphs/agent_campaign.yaml"],
            cwd=repo,
            capture_output=True,
            text=True,
        ).stdout
    )


def test_the_archive_outlives_the_worktree(tmp_path):
    """CON-6 (#245): THE bug. Worktrees live under gitignored runs/, so
    cleaning runs/ destroyed three agent-authored skills with no branch, no
    tag, and no copy — unreviewable, which §9.4 review presumes against. A
    ref makes the objects reachable, so they survive the directory."""
    import shutil

    import campaign as c

    repo, wt, oid = _campaign_repo(tmp_path)
    _agent_authors_a_skill(wt)
    rec = c.archive_deliverable(wt, oid, "doomed", now="2026-08-15T00:00:00Z")

    subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=repo, check=True)
    shutil.rmtree(tmp_path / "out", ignore_errors=True)
    assert not wt.exists()

    survived = subprocess.run(
        ["git", "show", f"{rec['ref']}:skills/t2-scan-pose/node.py"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert survived.returncode == 0 and "the agent's skill" in survived.stdout


def test_the_archive_excludes_gitignored_run_artifacts(tmp_path):
    """CON-6 (#245): a campaign worktree's runs/ holds traces and videos.
    Archiving them would put gigabytes per session into the object store —
    the archive follows .gitignore, so it keeps code and drops evidence
    that already has its own home."""
    import campaign as c

    repo, wt, oid = _campaign_repo(tmp_path)
    _agent_authors_a_skill(wt)
    rec = c.archive_deliverable(wt, oid, "lean", now="2026-08-15T00:00:00Z")
    listed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", rec["commit"]],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout
    assert "skills/t2-scan-pose/node.py" in listed
    assert "runs/" not in listed


def test_archiving_does_not_disturb_the_session_worktree(tmp_path):
    """CON-6 (#245): retention is an OBSERVER. It must not stage, commit,
    or check anything out in the worktree — a campaign's frozen-set audit
    and holdout scoring both read that state after the archive runs."""
    import campaign as c

    repo, wt, oid = _campaign_repo(tmp_path)
    _agent_authors_a_skill(wt)

    def worktree_state():
        return subprocess.run(
            ["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True
        ).stdout

    before_status, before_head = worktree_state(), (wt / ".git").read_text()
    c.archive_deliverable(wt, oid, "observer", now="2026-08-15T00:00:00Z")
    assert worktree_state() == before_status
    assert (wt / ".git").read_text() == before_head
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True
        ).stdout.strip()
        == oid
    )


def test_archive_refuses_cleanly_rather_than_killing_the_campaign(tmp_path):
    """CON-8 (#245): retention runs at the END of a metered session. A
    failure there must never discard the campaign record it exists to
    accompany — it reports {ok: false, error} and the campaign continues."""
    import campaign as c

    _repo, wt, oid = _campaign_repo(tmp_path)
    rec = c.archive_deliverable(wt / "nonexistent", oid, "broken", now="2026-08-15T00:00:00Z")
    assert rec["ok"] is False and rec["error"]


def test_the_campaign_cli_makes_no_dead_retention_promise(tmp_path):
    """#245: `--keep-worktree` was parsed and never read (0 uses) — a flag
    that reads as a retention guarantee while guaranteeing nothing. Either
    it works or it is gone; it must not come back as decoration."""
    import campaign as c

    source = Path(inspect.getfile(c)).read_text()
    assert "--keep-worktree" not in source, "dead retention flag is back"


def test_isolated_home_is_fresh_on_reuse(tmp_path):
    """PR #98 review P2: an aborted attempt's agent_home must not leak
    into the next launch — an occupied home rotates aside (audit
    preserved) and the new session starts from an EMPTY scratch."""
    import campaign as c

    out = tmp_path / "session_00"
    env1, _ = c.isolated_session_env(out)
    stale = Path(env1["CLAUDE_CONFIG_DIR"]) / "memory.md"
    stale.write_text("aborted-attempt state")
    env2, rec2 = c.isolated_session_env(out)
    assert env2["HOME"] == env1["HOME"]  # same canonical path...
    assert not (Path(env2["CLAUDE_CONFIG_DIR"]) / "memory.md").exists()  # ...fresh content
    assert rec2["rotated_prior_home"] == str(out / "agent_home-superseded1")
    assert (out / "agent_home-superseded1" / ".claude" / "memory.md").read_text() == (
        "aborted-attempt state"
    )


def test_h2_launcher_probes_auth_before_any_side_effect(tmp_path, monkeypatch, capsys):
    """PR #98 review P1: the generic campaign launcher must refuse on a
    failed isolated-env auth probe BEFORE creating the worktree or the
    session directory — never start a metered session."""
    import sys as _sys

    import campaign as c

    monkeypatch.setattr(c, "probe_agent_auth", lambda *a, **k: "auth probe exited 1: no creds")
    monkeypatch.setattr(
        c, "seed_session_credentials", lambda *a, **k: ({"credential_seed": "t"}, None)
    )
    monkeypatch.setattr(
        _sys,
        "argv",
        ["campaign.py", "--agent", "claude", "--out", str(tmp_path / "h2")],
    )
    rc = c.main()
    assert rc == 1
    refusal = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert refusal["ok"] is False and "auth probe" in refusal["error"]
    out = tmp_path / "h2"
    assert not (out / "worktree").exists()
    assert not any(p.name.startswith("session_") for p in out.iterdir())


def test_isolation_pins_agent_home_overrides(tmp_path, monkeypatch):
    """PR #98 review round 2: an operator-exported CODEX_HOME (or XDG
    base dir) bypasses the HOME rebind — codex resolves its home from
    CODEX_HOME first. Every such override must be pinned into the
    scratch home, never inherited."""
    import campaign as c

    monkeypatch.setenv("CODEX_HOME", "/Users/operator/.codex")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/Users/operator/.config")
    env, rec = c.isolated_session_env(tmp_path / "out")
    scratch = tmp_path / "out" / "agent_home"
    assert env["CODEX_HOME"] == str(scratch / ".codex")
    assert Path(env["CODEX_HOME"]).is_dir() and not any(Path(env["CODEX_HOME"]).iterdir())
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        assert env[var].startswith(str(scratch)), var
    assert rec["codex_home"] == str(scratch / ".codex")


def test_credential_seed_copies_only_the_campaign_login(tmp_path, monkeypatch):
    """Issue #96 follow-up (live-launch finding): a custom
    CLAUDE_CONFIG_DIR/CODEX_HOME reads credentials from the config dir,
    not the keychain — so isolated sessions authenticate via a
    DEDICATED campaign login whose credential file (and nothing else)
    is copied into each scratch home, 0600."""
    import campaign as c

    fake_home = tmp_path / "operator"
    login = fake_home / ".codex-campaign"
    login.mkdir(parents=True)
    (login / "auth.json").write_text('{"token": "campaign-secret"}')
    (login / "history.jsonl").write_text("operator history — must NOT be copied")
    monkeypatch.setattr(c.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setitem(c.CAMPAIGN_LOGIN, "codex", (login, "auth.json"))

    env, _ = c.isolated_session_env(tmp_path / "out")
    rec, err = c.seed_session_credentials("codex", env)
    assert err is None
    dest = Path(env["CODEX_HOME"]) / "auth.json"
    assert dest.read_text() == '{"token": "campaign-secret"}'
    assert oct(dest.stat().st_mode & 0o777) == "0o600"
    assert not (Path(env["CODEX_HOME"]) / "history.jsonl").exists()  # allow-list only
    assert rec == {"credential_seed": "codex-campaign login (auth.json)"}


def test_missing_campaign_login_is_an_actionable_refusal(tmp_path, monkeypatch):
    """No campaign login → CON-8-grade error naming the one-time setup
    command; never a silent fallback to the operator's own login."""
    import campaign as c

    monkeypatch.setitem(
        c.CAMPAIGN_LOGIN, "claude", (tmp_path / "nope" / ".claude-campaign", ".credentials.json")
    )
    env, _ = c.isolated_session_env(tmp_path / "out")
    rec, err = c.seed_session_credentials("claude", env)
    assert rec is None
    assert "no campaign login for 'claude'" in err and "claude" in err and "/login" in err


def test_credential_copy_failure_is_a_refusal_not_a_traceback(tmp_path, monkeypatch):
    """PR #100 review P1: an unreadable/uncopyable credential file must
    surface through the (record, error) CON-8 path, never raise past
    the launcher."""
    import campaign as c

    login = tmp_path / ".codex-campaign"
    login.mkdir()
    src = login / "auth.json"
    src.write_text("{}")
    src.chmod(0o000)  # unreadable
    monkeypatch.setitem(c.CAMPAIGN_LOGIN, "codex", (login, "auth.json"))
    env, _ = c.isolated_session_env(tmp_path / "out")
    rec, err = c.seed_session_credentials("codex", env)
    src.chmod(0o600)
    assert rec is None and "credential seed failed" in err


def test_seeded_credentials_are_scrubbed_and_rotation_scrubs_too(tmp_path, monkeypatch):
    """PR #100 review P1: live tokens must not persist in runs/
    artifacts — scrub removes them from the active home, and a rotated
    (aborted-attempt) home is scrubbed at rotation while its other
    artifacts survive for audit."""
    import campaign as c

    login = tmp_path / ".codex-campaign"
    login.mkdir()
    (login / "auth.json").write_text('{"token": "live"}')
    monkeypatch.setitem(c.CAMPAIGN_LOGIN, "codex", (login, "auth.json"))
    out = tmp_path / "session_00"
    env, _ = c.isolated_session_env(out)
    _, err = c.seed_session_credentials("codex", env)
    assert err is None
    (Path(env["CODEX_HOME"]) / "history.jsonl").write_text("audit artifact")

    # a same-named file OUTSIDE the canonical credential path is an
    # agent workspace artifact and must SURVIVE (PR #100 round 2: no
    # recursive name matching)
    decoy_dir = Path(env["HOME"]) / "workspace"
    decoy_dir.mkdir()
    (decoy_dir / "auth.json").write_text("agent audit artifact, not a token")

    scrubbed = c.scrub_session_credentials(Path(env["HOME"]))
    assert scrubbed == [str(Path(env["CODEX_HOME"]) / "auth.json")]
    assert not (Path(env["CODEX_HOME"]) / "auth.json").exists()
    assert (Path(env["CODEX_HOME"]) / "history.jsonl").exists()
    assert (decoy_dir / "auth.json").exists()  # workspace decoy survives

    # an ABORTED attempt (seeded, never scrubbed) must lose its token at
    # rotation time, keeping the rest of the home for audit
    env2, _ = c.isolated_session_env(out)
    _, err = c.seed_session_credentials("codex", env2)
    assert err is None
    env3, rec3 = c.isolated_session_env(out)  # rotates the seeded home aside
    rotated = Path(rec3["rotated_prior_home"])
    assert not list(rotated.rglob("auth.json"))  # token gone
    assert (rotated / ".codex").exists()  # audit shape preserved


def test_credential_seed_is_private_from_creation_and_clean_on_failure(tmp_path, monkeypatch):
    """PR #100 round 2 P1: the token file is 0600 FROM CREATION (no
    write-then-chmod window), and a failed copy leaves NO file behind."""
    import campaign as c

    login = tmp_path / ".codex-campaign"
    login.mkdir()
    (login / "auth.json").write_text('{"token": "live"}')
    monkeypatch.setitem(c.CAMPAIGN_LOGIN, "codex", (login, "auth.json"))
    env, _ = c.isolated_session_env(tmp_path / "out")
    rec, err = c.seed_session_credentials("codex", env)
    assert err is None
    dest = Path(env["CODEX_HOME"]) / "auth.json"
    assert oct(dest.stat().st_mode & 0o777) == "0o600"

    # failure path: an unwritable destination leaves nothing behind
    env2, _ = c.isolated_session_env(tmp_path / "out")
    dest_dir = Path(env2["CODEX_HOME"])
    dest_dir.chmod(0o500)  # cannot create files
    rec2, err2 = c.seed_session_credentials("codex", env2)
    dest_dir.chmod(0o700)
    assert rec2 is None and "credential seed failed" in err2
    assert not (dest_dir / "auth.json").exists()


def test_credential_seed_survives_short_writes(tmp_path, monkeypatch):
    """PR #100 round 3: os.write may write fewer bytes than requested; a
    short write must be continued to completion — one-byte-per-call
    writes still yield the full token — and zero progress is a refusal
    with no file left behind, never a truncated 'success'."""
    import os as _os

    import campaign as c

    login = tmp_path / ".codex-campaign"
    login.mkdir()
    payload = '{"token": "0123456789abcdef"}'
    (login / "auth.json").write_text(payload)
    monkeypatch.setitem(c.CAMPAIGN_LOGIN, "codex", (login, "auth.json"))

    real_write = _os.write
    monkeypatch.setattr(c.os, "write", lambda fd, data: real_write(fd, bytes(data)[:1]))
    env, _ = c.isolated_session_env(tmp_path / "out")
    rec, err = c.seed_session_credentials("codex", env)
    assert err is None
    assert (Path(env["CODEX_HOME"]) / "auth.json").read_text() == payload

    monkeypatch.setattr(c.os, "write", lambda fd, data: 0)
    env2, _ = c.isolated_session_env(tmp_path / "out2")
    rec2, err2 = c.seed_session_credentials("codex", env2)
    assert rec2 is None and "credential seed failed" in err2
    assert not (Path(env2["CODEX_HOME"]) / "auth.json").exists()


def test_the_frozen_audit_covers_every_env_hashed_path():
    """ADR-h2 point 5 / issue #228: the campaign's tamper audit must diff
    the SAME paths env_hash freezes.

    It re-listed `graphs/expert_*.yaml` by hand beside the shared constants,
    so it already missed `graphs/turn_plans/expert_*.json` when #197 froze
    that — an agent could have edited ADR-30 scheduler topology with the
    audit blind to it. Derived from `FROZEN_GLOBS` now; this pins that it
    stays derived."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import campaign
    from env_hash import FROZEN_DIRS, FROZEN_FILES, FROZEN_GLOBS

    source = inspect.getsource(campaign.audit_frozen)
    assert "FROZEN_GLOBS" in source, "the audit re-lists frozen paths instead of deriving them"
    # the `paths` assignment only — a comment naming a pattern is fine, a
    # string literal in the list is the drift this test exists to catch
    assignment = next(line for line in source.splitlines() if line.strip().startswith("paths ="))
    for pattern in (*FROZEN_DIRS, *FROZEN_FILES, *FROZEN_GLOBS):
        assert pattern not in assignment, (
            f"{pattern!r} is hardcoded in audit_frozen; it will drift from env_hash"
        )


def test_ceiling_kill_survives_eperm_from_killpg(tmp_path, monkeypatch):
    """A4 codex live failure: the session finished its work, hung, the
    wall ceiling fired -- and macOS raised EPERM from killpg (unsignalable
    group member), which escaped the ProcessLookupError-only guard and
    turned a budget stop into a bare infra crash. The ceiling kill must
    fall back to the direct child and still return a session record."""
    import campaign as c

    def killpg_eperm(pgid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(c.os, "killpg", killpg_eperm)
    monkeypatch.setattr(c, "POLL_S", 0.2)
    record = c.run_session(
        "claude",
        [sys.executable, "-c", "import time; time.sleep(60)"],
        tmp_path,
        tmp_path,
        {
            "prior_tokens": 0,
            "prior_wall_s": 0.0,
            "token_ceiling": 10**9,
            "wall_ceiling_s": 0.5,
        },
    )
    assert record["stopped"] == "wall_budget"
