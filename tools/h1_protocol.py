#!/usr/bin/env python3
"""H1 composition experiment runner (design doc §8.2.4; hypothesis H1 §6).

Protocol: N fresh agent sessions (claude | codex), each in a detached git
worktree PINNED to one commit resolved at protocol start, placed OUTSIDE
the repository (no adjacency to the base checkout or sibling attempts).
The session composes graphs/agent_h1.yaml for T1 and iterates
`harness validate`; the runner snapshots the FIRST parseable graph during
the session, then scores first AND final graphs in a SEPARATE CLEAN
worktree at the pinned commit (only the graph file is copied in — agent
mutations to its own workspace cannot touch scoring).

Per attempt: zero-shot validity and LAUNCH of the first graph (H1's
headline: "valid, launching dataflow zero-shot ≥80%"), the agent's
validate-fix cycles (structured event telemetry for both arms), final
graph validity/launch/pass@1, workspace violations (files edited beyond
the graph), and session budget telemetry. Launch := the scored rollout
produced ≥1 episode result (gate refusals and pre-episode crashes are
NOT launches; both are recorded distinctly).

Treatment pinning: commit OID, agent CLI version, model id, prompt
sha256, and full argv are recorded in every result set. Both arms get
the SAME wall-clock session budget and bypassed-permission mode; the
turn-cap asymmetry (claude --max-turns has no codex equivalent) is
recorded as a limitation in ADR-h1-protocol.

Failure attribution: agent failures (no graph, invalid, refusal,
timeout) are COMPLETE records — the measurement. Infrastructure failures
(worktree/uv/CLI-crash) abort the attempt into runner_errors and the
protocol exits nonzero (CON-8). `--start` RESUMES: existing records are
merged by attempt index and the summary recomputed over all.

Usage:
  uv run python tools/h1_protocol.py --agent claude --attempts 1   # smoke
  uv run python tools/h1_protocol.py --agent claude --attempts 20
  uv run python tools/h1_protocol.py --agent codex  --attempts 20
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "runs" / "h1"

GRAPH_REL = "graphs/agent_h1.yaml"
SESSION_TIMEOUT_S = 1200.0  # the shared wall budget, both arms
CLAUDE_MAX_TURNS = 50  # secondary safety, claude only (recorded limitation)
ROLLOUT_EPISODES = 8  # pass@1 over 8 seeds per attempt (protocol choice)
DEFAULT_MODELS = {"claude": "claude-fable-5", "codex": "gpt-5.2-codex"}

TASK_PROMPT = f"""You are the RESEARCH agent for the AISLE experiment.
Read harness/CLAUDE.research.md — it is your contract — and follow it.

Task T1: compose a dora dataflow graph that picks the NAMED medicine
(the episode goal's target_med field) from the shelf — 5 medicines at
seed-randomized poses — and delivers it to the tray. Compose it from the
capability registry (search it; every node id must be a registry
manifest id) following the wiring rules the validator enforces.

Write the graph to {GRAPH_REL} (node paths relative to the graphs/
directory, like the expert graphs). Validate it with
`uv run harness validate {GRAPH_REL}` and fix errors until it reports
ok. Do NOT run rollouts. Do NOT edit any file other than {GRAPH_REL}.
Stop as soon as validate reports ok true."""

PROMPT_SHA = hashlib.sha256(TASK_PROMPT.encode()).hexdigest()


# ---------------------------------------------------------------- pure cores


def parse_claude_events(lines: list[str]) -> dict:
    """Validate-call telemetry from claude stream-json events: Bash
    tool_use blocks paired to their tool_result by id."""
    results: list[bool] = []
    pending: dict[str, bool] = {}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = event.get("message") or {}
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                cmd = str((block.get("input") or {}).get("command", ""))
                if "harness validate" in cmd:
                    pending[str(block.get("id", ""))] = True
            elif block.get("type") == "tool_result" and pending.pop(
                str(block.get("tool_use_id", "")), False
            ):
                content = block.get("content")
                text = content if isinstance(content, str) else json.dumps(content)
                results.append('"ok": true' in text or '\\"ok\\": true' in text)
    return {"validate_calls": len(results), "validate_results": results}


def parse_codex_events(lines: list[str]) -> dict:
    """Validate-call telemetry from codex --json events: item.completed
    command_execution items carry the command and its output/exit code."""
    results: list[bool] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") or {}
        if item.get("type") != "command_execution":
            continue
        if "harness validate" not in str(item.get("command", "")):
            continue
        out = str(item.get("aggregated_output", "")) + str(item.get("output", ""))
        ok = '"ok": true' in out
        if not out and item.get("exit_code") is not None:
            ok = item.get("exit_code") == 0
        results.append(ok)
    return {"validate_calls": len(results), "validate_results": results}


def is_parseable_graph(text: str) -> bool:
    """The first-graph snapshot gate: a non-empty YAML mapping with nodes.
    (Content gate only — validity is decided by the scorer's validate.)"""
    import yaml

    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    return isinstance(doc, dict) and bool(doc.get("nodes"))


def classify_launch(report: dict) -> dict:
    """H1 'launching': the scored rollout produced >=1 episode result.
    Refusals (gate) and pre-episode crashes/stalls are distinct
    non-launch outcomes."""
    episodes = report.get("episodes") or []
    if report.get("refused"):
        outcome = "refused"
    elif len(episodes) >= 1:
        outcome = "launched"
    elif report.get("stalled"):
        outcome = "stalled"
    else:
        outcome = "no_episodes"
    return {"launched": outcome == "launched", "launch_outcome": outcome}


def audit_workspace(porcelain: str, allowed: str = GRAPH_REL) -> list[str]:
    """Files the session changed besides the allowed graph (the
    no-cheating audit; scoring is immune regardless, via the clean
    worktree)."""
    violations = []
    for line in porcelain.splitlines():
        path = line[3:].strip().strip('"')
        if path and path != allowed:
            violations.append(path)
    return violations


def summarize(records: list[dict]) -> dict:
    n = len(records)
    if not n:
        return {"attempts": 0}
    zs_valid = sum(1 for r in records if r.get("first_graph", {}).get("valid"))
    zs_launch = sum(
        1
        for r in records
        if r.get("first_graph", {}).get("valid") and r.get("first_graph", {}).get("launched")
    )
    within3 = sum(
        1
        for r in records
        if r.get("final_graph", {}).get("valid")
        and r.get("validate_calls", 99) <= 3
        and (r.get("final_graph", {}).get("pass1") or 0) > 0
    )
    return {
        "attempts": n,
        "zero_shot_valid": zs_valid,
        "zero_shot_valid_and_launching": zs_launch,
        "zero_shot_rate": round(zs_launch / n, 3),
        "h1_zero_shot_target_80pct": zs_launch / n >= 0.8,
        "final_valid": sum(1 for r in records if r.get("final_graph", {}).get("valid")),
        "final_launched": sum(1 for r in records if r.get("final_graph", {}).get("launched")),
        "working_within_3_cycles": within3,
        "mean_validate_calls": round(sum(r.get("validate_calls", 0) for r in records) / n, 2),
        "mean_final_pass1": round(
            sum(r.get("final_graph", {}).get("pass1") or 0 for r in records) / n, 3
        ),
        "attempts_with_workspace_violations": sum(
            1 for r in records if r.get("workspace_violations")
        ),
        "sessions_timed_out": sum(1 for r in records if r.get("session_timed_out")),
    }


def merge_results(existing: dict | None, new_records: list[dict]) -> list[dict]:
    """Resume semantics: records keyed by attempt index; new replaces old
    at the same index; summary is recomputed over the union."""
    by_idx: dict[int, dict] = {}
    for r in (existing or {}).get("records", []):
        by_idx[int(r["attempt"])] = r
    for r in new_records:
        by_idx[int(r["attempt"])] = r
    return [by_idx[i] for i in sorted(by_idx)]


# ------------------------------------------------------------------- runner


class InfraError(RuntimeError):
    """Runner-infrastructure failure: aborts the attempt, fails the
    protocol — never recorded as an agent outcome."""


def _run(cmd, cwd, timeout=None, check_infra: str | None = None):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check_infra and proc.returncode != 0:
        raise InfraError(f"{check_infra}: rc={proc.returncode}: {(proc.stderr or '')[-300:]}")
    return proc


def make_worktree(oid: str, dest: Path) -> Path:
    _run(
        ["git", "worktree", "add", "--detach", str(dest), oid],
        cwd=REPO_ROOT,
        timeout=120,
        check_infra="worktree add",
    )
    _run(["uv", "sync", "--extra", "sim"], cwd=dest, timeout=900, check_infra="uv sync")
    return dest


def remove_worktree(wt: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(wt)], cwd=REPO_ROOT, capture_output=True
    )


def agent_cmd(agent: str, model: str) -> list[str]:
    if agent == "claude":
        return [
            "claude",
            "-p",
            TASK_PROMPT,
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            str(CLAUDE_MAX_TURNS),
            "--allowedTools",
            "Bash,Read,Write,Edit,Glob,Grep",
            "--dangerously-skip-permissions",
        ]
    return [
        "codex",
        "exec",
        "--json",
        "--model",
        model,
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--dangerously-bypass-approvals-and-sandbox",
        TASK_PROMPT,
    ]


def run_session(agent: str, model: str, wt: Path, attempt_dir: Path) -> dict:
    """Popen + poll: enforces the shared wall budget, snapshots the FIRST
    parseable graph the session writes, captures the event log."""
    graph_path = wt / GRAPH_REL
    first_snapshot: str | None = None
    t0 = time.monotonic()
    with open(attempt_dir / "session.jsonl", "w") as log:
        proc = subprocess.Popen(
            agent_cmd(agent, model), cwd=wt, stdout=log, stderr=subprocess.DEVNULL, text=True
        )
        timed_out = False
        while proc.poll() is None:
            if first_snapshot is None and graph_path.exists():
                text = graph_path.read_text(errors="replace")
                if is_parseable_graph(text):
                    first_snapshot = text
                    (attempt_dir / "first_graph.yaml").write_text(text)
            if time.monotonic() - t0 > SESSION_TIMEOUT_S:
                proc.kill()
                timed_out = True
                break
            time.sleep(1.0)
        proc.wait(timeout=30)
    # the first write may have landed between the last poll and exit
    if first_snapshot is None and graph_path.exists():
        text = graph_path.read_text(errors="replace")
        if is_parseable_graph(text):
            first_snapshot = text
            (attempt_dir / "first_graph.yaml").write_text(text)
    lines = (attempt_dir / "session.jsonl").read_text(errors="replace").splitlines()
    if proc.returncode not in (0, None) and not lines and not timed_out:
        raise InfraError(f"{agent} session crashed with rc={proc.returncode} and no output")
    telemetry = (parse_claude_events if agent == "claude" else parse_codex_events)(lines)
    porcelain = _run(["git", "status", "--porcelain"], cwd=wt, timeout=60).stdout
    return {
        **telemetry,
        "session_wall_s": round(time.monotonic() - t0, 1),
        "session_timed_out": timed_out,
        "session_rc": proc.returncode,
        "first_graph_captured": first_snapshot is not None,
        "final_graph_exists": graph_path.exists(),
        "workspace_violations": audit_workspace(porcelain),
    }


def score_graph(graph_text: str, score_wt: Path, run_id: str, attempt_dir: Path, tag: str) -> dict:
    """Score ONE graph in the CLEAN pinned worktree: validate, then a
    T1 rollout. Only the graph text enters the worktree."""
    (score_wt / GRAPH_REL).write_text(graph_text)
    (attempt_dir / f"{tag}_graph.yaml").write_text(graph_text)
    val = _run(
        ["uv", "run", "harness", "validate", GRAPH_REL, "--embodiment", "franka"],
        cwd=score_wt,
        timeout=600,
    )
    try:
        valid = bool(json.loads(val.stdout.splitlines()[-1])["ok"])
    except (json.JSONDecodeError, IndexError, KeyError):
        valid = False
    if not valid:
        (attempt_dir / f"{tag}_validate.json").write_text(val.stdout)
        return {"valid": False, "launched": False, "launch_outcome": "invalid", "pass1": 0.0}
    roll = _run(
        [
            "uv",
            "run",
            "harness",
            "rollout",
            "--graph",
            GRAPH_REL,
            "--tier",
            "T1",
            "--episodes",
            str(ROLLOUT_EPISODES),
            "--seeds",
            f"0..{ROLLOUT_EPISODES - 1}",
            "--reset",
            "teleport",
            "--no-idea-gate",
            "--run-id",
            run_id,
        ],
        cwd=score_wt,
        timeout=3600,
    )
    try:
        report = json.loads(roll.stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        report = {"ok": False, "error": (roll.stderr or roll.stdout)[-400:]}
    (attempt_dir / f"{tag}_rollout.json").write_text(json.dumps(report, indent=1))
    return {
        "valid": True,
        **classify_launch(report),
        "pass1": report.get("pass1", 0.0),
        "failures": report.get("failures"),
        "refused": report.get("refused"),
    }


def run_attempt(agent: str, model: str, oid: str, index: int, out: Path, scratch: Path) -> dict:
    attempt_dir = out / agent / f"attempt_{index:02d}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    session_wt = scratch / f"{agent}_{index:02d}_session"
    score_wt = scratch / f"{agent}_{index:02d}_score"
    try:
        make_worktree(oid, session_wt)
        session = run_session(agent, model, session_wt, attempt_dir)
        first_text = (
            (attempt_dir / "first_graph.yaml").read_text()
            if session["first_graph_captured"]
            else None
        )
        final_text = (
            (session_wt / GRAPH_REL).read_text(errors="replace")
            if session["final_graph_exists"]
            else None
        )
    finally:
        remove_worktree(session_wt)

    record: dict = {"agent": agent, "attempt": index, **session}
    try:
        make_worktree(oid, score_wt)
        none_score = {"valid": False, "launched": False, "launch_outcome": "no_graph", "pass1": 0.0}
        record["first_graph"] = (
            score_graph(first_text, score_wt, f"h1-{agent}-{index:02d}-first", attempt_dir, "first")
            if first_text
            else dict(none_score)
        )
        if final_text is None:
            record["final_graph"] = dict(none_score)
        elif final_text == first_text:
            record["final_graph"] = dict(record["first_graph"])
        else:
            record["final_graph"] = score_graph(
                final_text, score_wt, f"h1-{agent}-{index:02d}-final", attempt_dir, "final"
            )
    finally:
        remove_worktree(score_wt)
    record["valid_first_try"] = record["first_graph"]["valid"]
    (attempt_dir / "record.json").write_text(json.dumps(record, indent=1))
    return record


def treatment(agent: str, model: str, oid: str) -> dict:
    version = subprocess.run(
        [agent, "--version"], capture_output=True, text=True, timeout=30
    ).stdout.strip()
    return {
        "commit": oid,
        "agent": agent,
        "agent_cli_version": version,
        "model": model,
        "prompt_sha256": PROMPT_SHA,
        "session_wall_budget_s": SESSION_TIMEOUT_S,
        "claude_max_turns": CLAUDE_MAX_TURNS if agent == "claude" else None,
        "argv": agent_cmd(agent, model)[:-1] if agent == "codex" else agent_cmd(agent, model)[3:],
        "episodes_per_attempt": ROLLOUT_EPISODES,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="claude", choices=["claude", "codex"])
    parser.add_argument("--model", default=None, help="explicit model id (pinned in results)")
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--start", type=int, default=0, help="resume from attempt index (merges)")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    model = args.model or DEFAULT_MODELS[args.agent]
    oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"h1_results_{args.agent}.json"
    existing = json.loads(path.read_text()) if path.exists() else None

    records, infra_errors = [], []
    with tempfile.TemporaryDirectory(prefix="aisle-h1-") as scratch_s:
        scratch = Path(scratch_s)
        for i in range(args.start, args.start + args.attempts):
            print(f"[h1] {args.agent} attempt {i} ...", file=sys.stderr)
            try:
                record = run_attempt(args.agent, model, oid, i, args.out, scratch)
            except InfraError as bad:
                infra_errors.append({"attempt": i, "error": str(bad)})
                print(f"[h1] attempt {i} INFRA error: {bad}", file=sys.stderr)
                continue
            records.append(record)
            brief = {
                "zero_shot": record["first_graph"]["valid"] and record["first_graph"]["launched"],
                "cycles": record.get("validate_calls"),
                "final_pass1": record["final_graph"].get("pass1"),
            }
            print(f"[h1] attempt {i}: {json.dumps(brief)}", file=sys.stderr)

    merged = merge_results(existing, records)
    results = {
        "experiment": "H1 (design doc 8.2.4)",
        "task": "T1",
        "treatment": treatment(args.agent, model, oid),
        "records": merged,
        "summary": summarize(merged),
        "runner_errors": ((existing or {}).get("runner_errors") or []) + infra_errors,
        "ok": not infra_errors,
    }
    path.write_text(json.dumps(results, indent=1))
    print(json.dumps({"ok": results["ok"], "results": str(path), "summary": results["summary"]}))
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
