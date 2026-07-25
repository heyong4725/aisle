#!/usr/bin/env python3
"""H1 composition experiment runner (design doc §8.2.4; hypothesis H1 §6).

Protocol: N fresh agent sessions (one isolated git worktree each), each
given the research contract + the T1 composition task; per attempt record
(a) was the FIRST `harness validate` call ok, (b) how many validate calls
the agent made (its validate-fix cycles), (c) pass@1 of the final graph,
scored by an INDEPENDENT rollout run by this runner — the session
composes and validates only, so attempts stay cheap and comparable
(interpretation recorded in docs/decisions/ADR-h1-protocol.md).

H1 (§6): zero-shot valid ≥80% of attempts; working (>0% success) graph
within 3 validate-fix cycles.

Usage:
  uv run python tools/h1_protocol.py --agent claude --attempts 20
  uv run python tools/h1_protocol.py --agent claude --attempts 1   # smoke

Outputs runs/h1/<agent>/attempt_NN/ (session log, graph, rollout report)
and runs/h1/h1_results_<agent>.json; the committed table lands in
analysis/h1_table.md. JSON to stdout, logs to stderr, exit 0 iff every
attempt produced a complete record (CON-8) — an attempt whose AGENT
fails is still a complete record (that is the measurement); only runner
infrastructure errors fail the protocol.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "runs" / "h1"

GRAPH_REL = "graphs/agent_h1.yaml"
SESSION_MAX_TURNS = 50
SESSION_TIMEOUT_S = 1200
ROLLOUT_EPISODES = 8  # pass@1 over 8 seeds per attempt (protocol choice)

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


def _run(cmd, cwd, timeout=None, **kw):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, **kw)


def make_worktree(base: Path, attempt_dir: Path) -> Path:
    wt = attempt_dir / "worktree"
    proc = _run(["git", "worktree", "add", "--detach", str(wt), "HEAD"], cwd=base)
    if proc.returncode != 0:
        raise RuntimeError(f"worktree add failed: {proc.stderr.strip()}")
    return wt


def remove_worktree(base: Path, wt: Path) -> None:
    _run(["git", "worktree", "remove", "--force", str(wt)], cwd=base)


def run_claude_session(wt: Path, log_path: Path) -> dict:
    """One fresh headless session; returns validate-call telemetry parsed
    from the stream-json event log."""
    cmd = [
        "claude",
        "-p",
        TASK_PROMPT,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(SESSION_MAX_TURNS),
        "--allowedTools",
        "Bash,Read,Write,Edit,Glob,Grep",
        "--dangerously-skip-permissions",
    ]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=wt, capture_output=True, text=True, timeout=SESSION_TIMEOUT_S
        )
        timed_out = False
    except subprocess.TimeoutExpired as e:
        proc = e
        timed_out = True
    stdout = proc.stdout or ""
    log_path.write_text(stdout)
    wall_s = time.monotonic() - t0

    validate_results: list[bool] = []
    pending: dict[str, bool] = {}  # tool_use_id -> is-validate-call
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = event.get("message") or {}
        for block in msg.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "Bash":
                cmd_s = str((block.get("input") or {}).get("command", ""))
                if "harness validate" in cmd_s:
                    pending[block.get("id", "")] = True
            elif block.get("type") == "tool_result" and pending.pop(
                block.get("tool_use_id", ""), False
            ):
                content = block.get("content")
                text = json.dumps(content) if not isinstance(content, str) else content
                validate_results.append('\\"ok\\": true' in text or '"ok": true' in text)
    return {
        "validate_calls": len(validate_results),
        "valid_first_try": validate_results[0] if validate_results else None,
        "agent_saw_ok": any(validate_results),
        "session_wall_s": round(wall_s, 1),
        "session_timed_out": timed_out,
    }


def run_codex_session(wt: Path, log_path: Path) -> dict:
    """Codex arm: same task, best-effort telemetry (codex exec --json)."""
    cmd = ["codex", "exec", "--json", "--skip-git-repo-check", TASK_PROMPT]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=wt, capture_output=True, text=True, timeout=SESSION_TIMEOUT_S
        )
        timed_out = False
    except subprocess.TimeoutExpired as e:
        proc = e
        timed_out = True
    stdout = (proc.stdout or "") + "\n" + (getattr(proc, "stderr", "") or "")
    log_path.write_text(stdout)
    calls = stdout.count("harness validate")
    return {
        "validate_calls": calls,
        "valid_first_try": None,  # codex telemetry is coarser; scored below
        "agent_saw_ok": '"ok": true' in stdout,
        "session_wall_s": round(time.monotonic() - t0, 1),
        "session_timed_out": timed_out,
    }


def score_attempt(wt: Path, attempt_dir: Path) -> dict:
    """Independent scoring in the attempt worktree: final validate, then a
    rollout for pass@1 (HAR-1 gates included — trusted baseline holds
    because the worktree is at main and the agent may not edit frozen
    files; a frozen edit shows up here as a refusal, which is itself the
    record)."""
    graph = wt / GRAPH_REL
    if not graph.exists():
        return {"graph_written": False, "final_valid": False, "launched": False, "pass1": 0.0}
    shutil.copy(graph, attempt_dir / "agent_h1.yaml")
    val = _run(
        ["uv", "run", "harness", "validate", GRAPH_REL, "--embodiment", "franka"],
        cwd=wt,
        timeout=600,
    )
    try:
        final_valid = json.loads(val.stdout.splitlines()[-1])["ok"]
    except (json.JSONDecodeError, IndexError, KeyError):
        final_valid = False
    if not final_valid:
        (attempt_dir / "validate.json").write_text(val.stdout)
        return {"graph_written": True, "final_valid": False, "launched": False, "pass1": 0.0}
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
            f"h1-{attempt_dir.name}",
        ],
        cwd=wt,
        timeout=3600,
    )
    try:
        report = json.loads(roll.stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        report = {"ok": False, "error": (roll.stderr or roll.stdout)[-400:]}
    (attempt_dir / "rollout.json").write_text(json.dumps(report, indent=1))
    launched = bool(report.get("ok")) or bool(report.get("episodes"))
    return {
        "graph_written": True,
        "final_valid": True,
        "launched": launched,
        "pass1": report.get("pass1", 0.0),
        "failures": report.get("failures"),
        "refused": report.get("refused"),
    }


def run_attempt(agent: str, index: int, out: Path) -> dict:
    attempt_dir = out / agent / f"attempt_{index:02d}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    wt = make_worktree(REPO_ROOT, attempt_dir)
    try:
        _run(["uv", "sync", "--extra", "sim"], cwd=wt, timeout=600)
        session = (run_claude_session if agent == "claude" else run_codex_session)(
            wt, attempt_dir / "session.jsonl"
        )
        score = score_attempt(wt, attempt_dir)
    finally:
        remove_worktree(REPO_ROOT, wt)
    record = {"agent": agent, "attempt": index, **session, **score}
    (attempt_dir / "record.json").write_text(json.dumps(record, indent=1))
    return record


def summarize(records: list[dict]) -> dict:
    n = len(records)
    valid_first = sum(1 for r in records if r.get("valid_first_try"))
    within3 = sum(
        1
        for r in records
        if r.get("final_valid") and r.get("validate_calls", 99) <= 3 and r.get("pass1", 0) > 0
    )
    return {
        "attempts": n,
        "zero_shot_valid": valid_first,
        "zero_shot_valid_rate": round(valid_first / n, 3) if n else None,
        "final_valid": sum(1 for r in records if r.get("final_valid")),
        "launched": sum(1 for r in records if r.get("launched")),
        "working_within_3_cycles": within3,
        "mean_validate_calls": round(sum(r.get("validate_calls", 0) for r in records) / n, 2)
        if n
        else None,
        "mean_pass1": round(sum(r.get("pass1") or 0 for r in records) / n, 3) if n else None,
        "h1_zero_shot_target_80pct": (valid_first / n >= 0.8) if n else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="claude", choices=["claude", "codex"])
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--start", type=int, default=0, help="resume from attempt index")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    records, infra_errors = [], []
    for i in range(args.start, args.start + args.attempts):
        print(f"[h1] {args.agent} attempt {i} ...", file=sys.stderr)
        try:
            record = run_attempt(args.agent, i, args.out)
        except Exception as bad:  # runner infrastructure failure, not the agent's
            infra_errors.append({"attempt": i, "error": str(bad)})
            print(f"[h1] attempt {i} runner error: {bad}", file=sys.stderr)
            continue
        records.append(record)
        brief = {
            k: record.get(k) for k in ("valid_first_try", "validate_calls", "final_valid", "pass1")
        }
        print(f"[h1] attempt {i}: {json.dumps(brief)}", file=sys.stderr)

    results = {
        "experiment": "H1 (design doc 8.2.4)",
        "agent": args.agent,
        "task": "T1",
        "episodes_per_attempt": ROLLOUT_EPISODES,
        "records": records,
        "summary": summarize(records),
        "runner_errors": infra_errors,
        "ok": not infra_errors,
    }
    path = args.out / f"h1_results_{args.agent}.json"
    path.write_text(json.dumps(results, indent=1))
    print(json.dumps({"ok": results["ok"], "results": str(path), "summary": results["summary"]}))
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
