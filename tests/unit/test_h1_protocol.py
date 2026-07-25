"""Unit tests for the H1 protocol runner's pure cores (design doc §8.2.4,
ADR-h1-protocol; PR #27 review): event parsing for BOTH agent arms,
first-graph gating, launch classification, workspace audit, headline
summarization, and resume-merge semantics — everything decidable without
spawning sessions. Cites CON-5 (pinned, reproducible treatment) and
CON-8 (protocol exit semantics are encoded in summarize/merge)."""

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from h1_protocol import (  # noqa: E402
    GRAPH_REL,
    audit_workspace,
    classify_launch,
    is_parseable_graph,
    merge_results,
    parse_claude_events,
    parse_codex_events,
    summarize,
)


def _claude_event(blocks):
    return json.dumps({"type": "assistant", "message": {"content": blocks}})


def test_claude_parser_pairs_validate_calls_to_results():
    """Bash tool_use blocks containing `harness validate` are paired to
    their tool_result by id; ok is read from the result JSON."""
    lines = [
        _claude_event(
            [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "uv run harness validate graphs/agent_h1.yaml"},
                }
            ]
        ),
        _claude_event(
            [{"type": "tool_result", "tool_use_id": "t1", "content": '{"ok": false, "errors": []}'}]
        ),
        _claude_event(
            [
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "Bash",
                    "input": {"command": "uv run harness validate graphs/agent_h1.yaml"},
                }
            ]
        ),
        _claude_event(
            [{"type": "tool_result", "tool_use_id": "t2", "content": '{"ok": true, "errors": []}'}]
        ),
        # a non-validate Bash call must not count
        _claude_event(
            [{"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "ls graphs/"}}]
        ),
        _claude_event([{"type": "tool_result", "tool_use_id": "t3", "content": "expert_t0.yaml"}]),
        "not json at all",
    ]
    t = parse_claude_events(lines)
    assert t["validate_calls"] == 2
    assert t["validate_results"] == [False, True]


def test_claude_parser_handles_structured_result_content():
    lines = [
        _claude_event(
            [
                {
                    "type": "tool_use",
                    "id": "a",
                    "name": "Bash",
                    "input": {"command": "uv run harness validate g.yaml"},
                }
            ]
        ),
        _claude_event(
            [
                {
                    "type": "tool_result",
                    "tool_use_id": "a",
                    "content": [{"type": "text", "text": '{"ok": true}'}],
                }
            ]
        ),
    ]
    assert parse_claude_events(lines)["validate_results"] == [True]


def test_codex_parser_reads_command_execution_items():
    """codex --json telemetry: item.completed command_execution items are
    real command telemetry (PR #27: text-occurrence counting is not)."""
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "x"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "uv run harness validate graphs/agent_h1.yaml",
                    "aggregated_output": '{"ok": false, "errors": [{"code": "SCHEMA_MISMATCH"}]}',
                    "exit_code": 1,
                },
            }
        ),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "uv run harness validate graphs/agent_h1.yaml",
                    "aggregated_output": '{"ok": true, "errors": []}',
                    "exit_code": 0,
                },
            }
        ),
        # a prose mention of `harness validate` in an agent message must
        # NOT count as a call
        json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "next I will run harness validate"},
            }
        ),
    ]
    t = parse_codex_events(lines)
    assert t["validate_calls"] == 2
    assert t["validate_results"] == [False, True]


def test_codex_parser_falls_back_to_exit_code():
    lines = [
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "command": "uv run harness validate g.yaml",
                    "exit_code": 0,
                },
            }
        )
    ]
    assert parse_codex_events(lines)["validate_results"] == [True]


def test_first_graph_gate_accepts_nodes_yaml_only():
    assert is_parseable_graph("nodes:\n  - id: a\n    path: x\n")
    assert not is_parseable_graph("")
    assert not is_parseable_graph("nodes: []\n")  # empty mapping: no snapshot yet
    assert not is_parseable_graph("{invalid: yaml: [")
    assert not is_parseable_graph("just a string")


def test_launch_classification_covers_every_outcome():
    """PR #27: gate refusals and pre-episode crashes are NOT launches."""
    assert classify_launch({"episodes": [{"status": "success"}]})["launched"] is True
    refused = classify_launch({"refused": {"gate": "env_hash"}, "episodes": []})
    assert refused["launched"] is False and refused["launch_outcome"] == "refused"
    stalled = classify_launch({"ok": False, "stalled": True, "episodes": []})
    assert stalled["launched"] is False and stalled["launch_outcome"] == "stalled"
    crashed = classify_launch({"ok": False, "episodes": []})
    assert crashed["launched"] is False and crashed["launch_outcome"] == "no_episodes"


def test_workspace_audit_flags_everything_but_the_graph():
    porcelain = f" M {GRAPH_REL}\n M src/aisle/verifier/oracle.py\n?? notes.md\n"
    assert audit_workspace(porcelain) == ["src/aisle/verifier/oracle.py", "notes.md"]
    assert audit_workspace(f"?? {GRAPH_REL}\n") == []


def _record(zs_valid, zs_launch, cycles, final_valid, pass1, attempt=0, violations=None):
    return {
        "attempt": attempt,
        "validate_calls": cycles,
        "first_graph": {"valid": zs_valid, "launched": zs_launch, "pass1": pass1},
        "final_graph": {"valid": final_valid, "launched": final_valid, "pass1": pass1},
        "workspace_violations": violations or [],
    }


def test_summary_headline_requires_first_graph_LAUNCH():
    """PR #27: H1's 80% target is 'valid, LAUNCHING dataflow' — a first
    graph that validates but never launches must not count."""
    records = [
        _record(True, True, 1, True, 0.5, attempt=0),  # zero-shot ✓
        _record(True, False, 1, True, 0.5, attempt=1),  # valid but no launch ✗
        _record(False, False, 4, True, 0.2, attempt=2),  # fixed later ✗
    ]
    s = summarize(records)
    assert s["zero_shot_valid"] == 2
    assert s["zero_shot_valid_and_launching"] == 1
    assert s["zero_shot_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert s["h1_zero_shot_target_80pct"] is False
    assert s["working_within_3_cycles"] == 2  # attempts 0 and 1 (final works)


def test_resume_merges_by_attempt_and_recomputes():
    """PR #27 P2: --start must MERGE with prior records, not replace."""
    old = {"records": [_record(True, True, 1, True, 1.0, attempt=0)]}
    new = [
        _record(True, True, 1, True, 1.0, attempt=1),
        _record(False, False, 2, False, 0.0, attempt=0),  # re-run replaces
    ]
    merged = merge_results(old, new)
    assert [r["attempt"] for r in merged] == [0, 1]
    assert merged[0]["first_graph"]["valid"] is False  # replaced by re-run
    assert summarize(merged)["attempts"] == 2


def test_summary_counts_violations_and_timeouts():
    records = [
        {**_record(True, True, 1, True, 1.0, attempt=0), "session_timed_out": True},
        _record(True, True, 1, True, 1.0, attempt=1, violations=["src/x.py"]),
    ]
    s = summarize(records)
    assert s["sessions_timed_out"] == 1
    assert s["attempts_with_workspace_violations"] == 1


def test_resume_refuses_treatment_mismatch():
    """PR #27 r3: a resumed segment must be the SAME experiment — any
    drift in commit/model/CLI/prompt/budgets refuses the merge."""
    from h1_protocol import check_resume_treatment

    base = {
        "commit": "abc",
        "agent": "claude",
        "agent_cli_version": "2.1.214",
        "model": "claude-fable-5",
        "prompt_sha256": "p1",
        "session_wall_budget_s": 1200.0,
        "claude_max_turns": 50,
        "episodes_per_attempt": 8,
    }
    assert check_resume_treatment(None, base) is None
    assert check_resume_treatment({"treatment": dict(base)}, base) is None
    drifted = {**base, "commit": "def"}
    msg = check_resume_treatment({"treatment": base}, drifted)
    assert msg is not None and "commit" in msg


def test_runner_errors_merge_resolves_reran_attempts():
    """PR #27 r3: prior errors whose attempt now has a complete record
    are resolved; retained errors keep the aggregate NOT-ok."""
    from h1_protocol import merge_runner_errors

    existing = {"runner_errors": [{"attempt": 3, "error": "x"}, {"attempt": 5, "error": "y"}]}
    records = [{"attempt": 3}]  # attempt 3 re-ran successfully
    merged = merge_runner_errors(existing, records, [{"attempt": 7, "error": "z"}])
    assert [e["attempt"] for e in merged] == [5, 7]
    assert merge_runner_errors(existing, [{"attempt": 3}, {"attempt": 5}], []) == []


def test_episode_accounting_counts_each_scored_rollout_once():
    """PR #27 r3: explicit protocol episode accounting — one rollout per
    valid first graph, one more only when a DIFFERENT final was scored."""
    from h1_protocol import episodes_scored

    records = [
        {  # first valid, final identical -> one rollout
            "first_graph": {"valid": True},
            "final_graph": {"valid": True},
            "final_same_as_first": True,
        },
        {  # first invalid, fixed final -> one rollout (final only)
            "first_graph": {"valid": False},
            "final_graph": {"valid": True},
            "final_same_as_first": False,
        },
        {  # both scored -> two rollouts
            "first_graph": {"valid": True},
            "final_graph": {"valid": True},
            "final_same_as_first": False,
        },
        {"first_graph": {"valid": False}, "final_graph": {"valid": False}},  # none
    ]
    assert episodes_scored(records, 8) == 8 + 8 + 16


def test_first_graph_shim_snapshots_on_first_validate(tmp_path, monkeypatch):
    """PR #27 r3: the zero-shot artifact is the graph CONSUMED BY THE
    FIRST validate call — the shim snapshots before delegating, once."""
    from h1_protocol import SHIM

    graph = tmp_path / "agent_h1.yaml"
    snap = tmp_path / "first_graph.yaml"
    shim_src = SHIM.format(python=sys.executable, graph=str(graph), snap=str(snap))
    # neuter the delegation for the test: everything before the import
    shim_body = shim_src.split("from aisle.harness.cli import main")[0]

    def invoke(argv):
        monkeypatch.setattr(sys, "argv", argv)
        exec(compile(shim_body, "<shim>", "exec"), {})

    graph.write_text("nodes: [v1]\n")
    invoke(["harness", "validate", "g"])
    assert snap.read_text() == "nodes: [v1]\n"
    graph.write_text("nodes: [v2]\n")  # later fix must NOT overwrite the snapshot
    invoke(["harness", "validate", "g"])
    assert snap.read_text() == "nodes: [v1]\n"
    # non-validate invocations never snapshot
    snap.unlink()
    invoke(["harness", "traces", "query"])
    assert not snap.exists()


def test_sandbox_profile_uses_valid_sbpl_string_literals(tmp_path):
    """CON-8: sandbox setup MUST produce a runnable CLI wrapper, including
    valid double-quoted string arguments in the macOS SBPL profile."""
    from h1_protocol import sandbox_wrap

    wt = tmp_path / "session"
    scratch = tmp_path / "scratch"
    attempt = tmp_path / "attempt"
    out = tmp_path / "runs" / "h1"
    cmd = sandbox_wrap(["claude", "--version"], wt, scratch, attempt, out)
    profile_path = Path(cmd[2])
    try:
        profile = profile_path.read_text()
    finally:
        profile_path.unlink()

    assert "(subpath '" not in profile
    assert "(literal '" not in profile
    assert f'(subpath "{wt}")' in profile
    assert f'(subpath "{scratch}")' in profile
    assert f'(literal "{Path.home() / ".claude.json"}")' in profile
    deny_results = f'(deny file-read* (subpath "{out}"))'
    allow_attempt = f'(allow file-read* (subpath "{attempt}"))'
    assert profile.index(deny_results) < profile.index(allow_attempt)


def test_session_cli_failure_preserves_stderr(tmp_path, monkeypatch):
    """CON-8: infrastructure failures MUST keep child stderr out of JSON
    stdout while preserving it as an artifact and in the runner error."""
    import h1_protocol

    wt = tmp_path / "session"
    attempt = tmp_path / "attempt"
    scratch = tmp_path / "scratch"
    out = tmp_path / "runs" / "h1"
    (wt / ".venv" / "bin").mkdir(parents=True)
    (wt / ".venv" / "bin" / "harness").write_text("#!/bin/sh\n")
    attempt.mkdir(parents=True)
    marker = "sandbox profile failed to compile"
    monkeypatch.setattr(
        h1_protocol,
        "agent_cmd",
        lambda _agent, _model: [
            sys.executable,
            "-c",
            f"import sys; print({marker!r}, file=sys.stderr); raise SystemExit(65)",
        ],
    )

    with pytest.raises(h1_protocol.InfraError, match=marker):
        h1_protocol.run_session("claude", "test-model", wt, attempt, scratch, out, False)

    assert (attempt / "session.stderr").read_text().strip() == marker


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS-only")
def test_sandbox_profile_allows_the_nested_cli_scratchpad(tmp_path):
    """The r5 smoke regression: the nested Claude CLI mkdirs its Bash
    scratchpad under /private/tmp/claude-<uid>/ — the profile must allow
    it or every session shell command EPERMs and the agent flies blind."""
    import subprocess

    from h1_protocol import sandbox_wrap

    probe = "mkdir -p /private/tmp/claude-$(id -u)/h1-test-probe && echo OK && rmdir /private/tmp/claude-$(id -u)/h1-test-probe"
    cmd = sandbox_wrap(
        ["/bin/sh", "-c", probe],
        tmp_path / "wt",
        tmp_path / "sc",
        tmp_path / "at",
        tmp_path / "out",
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    Path(cmd[2]).unlink(missing_ok=True)
    assert proc.returncode == 0 and "OK" in proc.stdout, proc.stderr[:200]
