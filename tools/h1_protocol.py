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
# codex: gpt-5.2-codex is refused for ChatGPT-account auth (400); the
# account's served model, verified by explicit-pin probe, is gpt-5.6-sol
DEFAULT_MODELS = {"claude": "claude-fable-5", "codex": "gpt-5.6-sol"}

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


def audit_workspace(
    porcelain: str, allowed: tuple[str, ...] = (GRAPH_REL, "src/aisle/harness/cli.py")
) -> list[str]:
    """Files the session changed besides the allowed graph (the
    no-cheating audit; scoring is immune regardless, via the clean
    worktree)."""
    violations = []
    for line in porcelain.splitlines():
        path = line[3:].strip().strip('"')
        if path and path not in allowed:
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


def check_resume_treatment(existing: dict | None, current: dict) -> str | None:
    """A resumed segment MUST be the same experiment (PR #27 r3): commit,
    model, CLI version, prompt, and budgets all have to match, or the
    union would silently mix treatments under the current label."""
    if not existing:
        return None
    prior = existing.get("treatment") or {}
    for key in (
        "commit",
        "agent",
        "agent_cli_version",
        "model",
        "prompt_sha256",
        "session_wall_budget_s",
        "claude_max_turns",
        "episodes_per_attempt",
    ):
        if prior.get(key) != current.get(key):
            return (
                f"resume treatment mismatch on {key!r}: existing={prior.get(key)!r} "
                f"current={current.get(key)!r} — use a fresh --out for a new treatment"
            )
    return None


def episodes_scored(records: list[dict], per_rollout: int) -> int:
    """The protocol's explicit episode accounting (PR #27 r3): one scored
    rollout per VALID first graph, plus one per valid final graph that
    DIFFERS from the first (identical finals reuse the first's score)."""
    total = 0
    for r in records:
        if (r.get("first_graph") or {}).get("valid"):
            total += per_rollout
        if (r.get("final_graph") or {}).get("valid") and not r.get("final_same_as_first"):
            total += per_rollout
    return total


def merge_runner_errors(
    existing: dict | None, records: list[dict], new_errors: list[dict]
) -> list[dict]:
    """Prior errors whose attempt index now has a COMPLETE record were
    resolved by the re-run; everything else is retained. ok must reflect
    the RETAINED union, not just the new segment (PR #27 r3)."""
    have = {int(r["attempt"]) for r in records}
    retained = [
        e for e in ((existing or {}).get("runner_errors") or []) if int(e["attempt"]) not in have
    ]
    return retained + new_errors


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


SHIM_TARGET = "src/aisle/harness/cli.py"
SHIM_ANCHOR = "def main() -> int:"
SHIM_SNIPPET = """def main() -> int:
    # H1 runner shim (ADR-h1-protocol): snapshot the graph CONSUMED BY THE
    # FIRST `harness validate` call — the race-free zero-shot artifact.
    # Installed on the worktree's EDITABLE source (a venv entry-point
    # wrapper gets regenerated by `uv run` sync — the r6 smoke measured
    # exactly that failure).
    import shutil as _h1_shutil
    import sys as _h1_sys
    from pathlib import Path as _H1Path

    try:  # the shim must be TRANSPARENT: its own defects may never
        # perturb the agent's validate (r7: a bad snap path failed the
        # call and the agent adaptively worked around it)
        _h1_graph = _H1Path({graph!r})
        _h1_snap = _H1Path({snap!r})
        if "validate" in _h1_sys.argv[1:] and _h1_graph.exists() and not _h1_snap.exists():
            _h1_snap.parent.mkdir(parents=True, exist_ok=True)
            _h1_shutil.copy(_h1_graph, _h1_snap)
    except Exception:
        pass
"""


def install_first_graph_shim(wt: Path, attempt_dir: Path) -> Path:
    """Patch the worktree's editable aisle.harness.cli so the FIRST
    validate invocation snapshots exactly the graph it validated."""
    target = wt / SHIM_TARGET
    if not target.exists():
        raise InfraError(f"no shim target at {target}")
    src = target.read_text()
    if SHIM_ANCHOR not in src:
        raise InfraError(f"shim anchor missing in {SHIM_TARGET}")
    snap = attempt_dir / "first_graph.yaml"
    snippet = SHIM_SNIPPET.format(graph=str(wt / GRAPH_REL), snap=str(snap))
    target.write_text(src.replace(SHIM_ANCHOR, snippet, 1))
    return snap


SANDBOX_PROFILE = """(version 1)
(allow default)
(deny file-write*)
(allow file-write*
  (subpath {worktree})
  (subpath {scratch})
  (subpath {attempt})
  (subpath {tmpdir})
  (subpath "/private/var/folders")
  (subpath "/private/tmp")
  (subpath "/tmp")
  (subpath "/dev")
  (subpath {home_claude})
  (literal {home_claude_json})
  (subpath {home_codex})
  (subpath {home_cache})
  (subpath {home_lib_caches}))
(deny file-read* (subpath {h1_out}))
(allow file-read* (subpath {attempt}))
"""


def _sbpl_string(path: str | Path) -> str:
    """Encode a path as a macOS Sandbox Profile Language string literal."""
    return json.dumps(str(path))


def sandbox_wrap(
    cmd: list[str], wt: Path, scratch: Path, attempt_dir: Path, out: Path
) -> list[str]:
    """macOS write-confinement for the bypassed session: writes only inside
    the session worktree/scratch/attempt dir/caches, and the H1 results
    tree is unreadable (prior attempts cannot leak in). The scored
    artifacts stay sound regardless (clean-worktree scoring); this bounds
    collateral damage and cross-attempt visibility."""
    import os
    import tempfile as _tf

    home = Path.home()
    profile = SANDBOX_PROFILE.format(
        worktree=_sbpl_string(wt),
        scratch=_sbpl_string(scratch),
        attempt=_sbpl_string(attempt_dir),
        tmpdir=_sbpl_string(os.environ.get("TMPDIR", "/tmp")),
        home_claude=_sbpl_string(home / ".claude"),
        home_claude_json=_sbpl_string(home / ".claude.json"),
        home_codex=_sbpl_string(home / ".codex"),
        home_cache=_sbpl_string(home / ".cache"),
        home_lib_caches=_sbpl_string(home / "Library" / "Caches"),
        h1_out=_sbpl_string(out),
    )
    pf = _tf.NamedTemporaryFile("w", suffix=".sb", delete=False)
    pf.write(profile)
    pf.close()
    return ["sandbox-exec", "-f", pf.name, *cmd]


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
        # codex's NATIVE sandbox provides the write confinement that
        # sandbox-exec provides for claude: workspace-write, no approvals
        "--sandbox",
        "workspace-write",
        "-c",
        "approval_policy=never",
        TASK_PROMPT,
    ]


def run_session(
    agent: str, model: str, wt: Path, attempt_dir: Path, scratch: Path, out: Path, sandbox: bool
) -> dict:
    """One session under the shared wall budget. The first-graph snapshot
    comes from the validate SHIM (race-free); on timeout the whole process
    GROUP is killed (agent-spawned children included); a nonzero CLI exit
    that is not our timeout kill is an infrastructure failure — never an
    agent statistic (PR #27 r3)."""
    import os
    import signal as _signal

    snap = install_first_graph_shim(wt, attempt_dir)
    graph_path = wt / GRAPH_REL
    cmd = agent_cmd(agent, model)
    if agent == "codex":
        # parity with the claude SBPL profile: the shim's snapshot target
        # (attempt_dir) lies OUTSIDE the session workspace, and codex's
        # native workspace-write sandbox blocks it unless declared — without
        # this the shim silently captures nothing (take-3 attempt 0)
        cmd = cmd[:-1] + [
            "-c",
            f'sandbox_workspace_write.writable_roots=["{attempt_dir}"]',
            cmd[-1],
        ]
    if sandbox and agent == "claude" and sys.platform == "darwin":
        cmd = sandbox_wrap(cmd, wt, scratch, attempt_dir, out)
    sandbox_profile = Path(cmd[2]) if cmd[:2] == ["sandbox-exec", "-f"] else None
    t0 = time.monotonic()
    timed_out = False
    stderr_path = attempt_dir / "session.stderr"
    try:
        with open(attempt_dir / "session.jsonl", "w") as log, open(stderr_path, "w") as stderr:
            proc = subprocess.Popen(
                cmd,
                cwd=wt,
                # codex exec reads stdin when it is not a TTY ("Reading
                # additional input from stdin..." then rc=1 under nohup);
                # the session must not depend on how the RUNNER was launched
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )
            try:
                proc.wait(timeout=SESSION_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                timed_out = True
                try:
                    os.killpg(proc.pid, _signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=30)
    finally:
        if sandbox_profile is not None:
            sandbox_profile.unlink(missing_ok=True)
    if proc.returncode != 0 and not timed_out:
        detail = stderr_path.read_text(errors="replace").strip()
        suffix = f": {detail[-1000:]}" if detail else ""
        raise InfraError(f"{agent} CLI exited rc={proc.returncode} (not an agent outcome){suffix}")
    lines = (attempt_dir / "session.jsonl").read_text(errors="replace").splitlines()
    telemetry = (parse_claude_events if agent == "claude" else parse_codex_events)(lines)
    porcelain = _run(["git", "status", "--porcelain"], cwd=wt, timeout=60).stdout
    # the flag must reflect HOW the snapshot was captured, not whether the
    # agent tried to validate (a sandbox-broken validate counts as a call
    # but never runs the shim — the r4 smoke caught exactly this)
    via_shim = snap.exists()
    if telemetry["validate_calls"] > 0 and not via_shim:
        raise InfraError(
            "shim did not capture the first validated graph despite "
            f"{telemetry['validate_calls']} validate call(s) — zero-shot "
            "attribution would be unsound (runner defect, not agent outcome)"
        )
    first_captured = via_shim
    if not first_captured and graph_path.exists():
        # the agent composed but NEVER validated: its one composition IS
        # the zero-shot artifact (flagged; §8.2.4's (a) is then decided
        # entirely by the scorer's validate)
        text = graph_path.read_text(errors="replace")
        if is_parseable_graph(text):
            snap.write_text(text)
            first_captured = True
    return {
        **telemetry,
        "session_wall_s": round(time.monotonic() - t0, 1),
        "session_timed_out": timed_out,
        "session_rc": proc.returncode,
        "first_graph_captured": first_captured,
        "first_graph_via_shim": via_shim,
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
            # ADR-h1-protocol: H1 scoring is PROTOCOL spend, not campaign
            # spend — the local override neither charges nor consults the
            # campaign ledger (ADR-21 semantics) and is recorded in every
            # manifest; the protocol's own episode accounting lands in the
            # results (total_episodes_scored) and is bounded up front
            "--env-baseline",
            "local",
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


def run_attempt(
    agent: str, model: str, oid: str, index: int, out: Path, scratch: Path, sandbox: bool
) -> dict:
    attempt_dir = out / agent / f"attempt_{index:02d}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    session_wt = scratch / f"{agent}_{index:02d}_session"
    try:
        make_worktree(oid, session_wt)
        session = run_session(agent, model, session_wt, attempt_dir, scratch, out, sandbox)
        first_text = (
            (attempt_dir / "first_graph.yaml").read_text(errors="replace")
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
    none_score = {"valid": False, "launched": False, "launch_outcome": "no_graph", "pass1": 0.0}

    def _score(text: str, tag: str) -> dict:
        # a FRESH pinned worktree per scored graph (PR #27 r3 P2): no warm
        # caches, run dirs, or ledger state shared between first and final
        wt = scratch / f"{agent}_{index:02d}_score_{tag}"
        try:
            make_worktree(oid, wt)
            return score_graph(text, wt, f"h1-{agent}-{index:02d}-{tag}", attempt_dir, tag)
        finally:
            remove_worktree(wt)

    record["first_graph"] = _score(first_text, "first") if first_text else dict(none_score)
    record["final_same_as_first"] = final_text is not None and final_text == first_text
    if final_text is None:
        record["final_graph"] = dict(none_score)
    elif record["final_same_as_first"]:
        record["final_graph"] = dict(record["first_graph"])
    else:
        record["final_graph"] = _score(final_text, "final")
    record["valid_first_try"] = record["first_graph"]["valid"]
    (attempt_dir / "record.json").write_text(json.dumps(record, indent=1))
    return record


def treatment(agent: str, model: str, oid: str, sandbox: bool = True) -> dict:
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
        "sandbox": sandbox,
        "env_baseline": "local (protocol spend, not campaign spend; per-manifest logged)",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", default="claude", choices=["claude", "codex"])
    parser.add_argument("--model", default=None, help="explicit model id (pinned in results)")
    parser.add_argument("--attempts", type=int, default=20)
    parser.add_argument("--start", type=int, default=0, help="resume from attempt index (merges)")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="disable the sandbox-exec write confinement (recorded in treatment)",
    )
    args = parser.parse_args()

    args.out = args.out.resolve()  # r7: a relative --out sent the shim's
    # snapshot path into the SESSION worktree's cwd instead of the runner's
    model = args.model or DEFAULT_MODELS[args.agent]
    oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"h1_results_{args.agent}.json"
    existing = json.loads(path.read_text()) if path.exists() else None
    current_treatment = treatment(args.agent, model, oid, sandbox=not args.no_sandbox)
    mismatch = check_resume_treatment(existing, current_treatment)
    if mismatch:
        print(json.dumps({"ok": False, "error": mismatch}))
        return 1

    records, infra_errors = [], []
    with tempfile.TemporaryDirectory(prefix="aisle-h1-") as scratch_s:
        scratch = Path(scratch_s)
        for i in range(args.start, args.start + args.attempts):
            print(f"[h1] {args.agent} attempt {i} ...", file=sys.stderr)
            try:
                record = run_attempt(
                    args.agent, model, oid, i, args.out, scratch, not args.no_sandbox
                )
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
    all_errors = merge_runner_errors(existing, merged, infra_errors)
    results = {
        "experiment": "H1 (design doc 8.2.4)",
        "task": "T1",
        "treatment": current_treatment,
        "records": merged,
        "summary": summarize(merged),
        "total_episodes_scored": episodes_scored(merged, ROLLOUT_EPISODES),
        "runner_errors": all_errors,
        "ok": not all_errors,
    }
    path.write_text(json.dumps(results, indent=1))
    print(json.dumps({"ok": results["ok"], "results": str(path), "summary": results["summary"]}))
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
