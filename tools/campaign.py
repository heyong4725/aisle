"""Single-scenario research campaign runner (ADR-h2-campaign-protocol;
design doc §8.3 item 6, hypothesis H2).

One pinned worktree + one research session under harness/CLAUDE.research.md.
Rollouts happen INSIDE the session through the trusted gate (frozen set,
idea gate, episode/wall ledger — all harness-enforced); this runner
enforces only what the harness cannot: the token ceiling (from the agent
CLI's own stream telemetry, HAR-5) and the campaign wall ceiling. After
the session it audits the frozen paths, scores the deliverable graph on
HELD-OUT seeds in the session worktree, and computes the H2 metrics from
the artifacts the harness already wrote. CON-8: JSON to stdout, logs to
stderr, exit 0 iff the campaign record was written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from env_hash import FROZEN_DIRS, FROZEN_FILES  # noqa: E402
from h1_protocol import DEFAULT_MODELS, InfraError, make_worktree  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DELIVERABLE = "graphs/agent_campaign.yaml"
HOLDOUT_EPISODES = 8
POLL_S = 5.0
TEE_JOIN_S = 10.0  # stream-drain grace after the session exits


# ---------------------------------------------------------------- telemetry


def _tok(usage: dict, field: str) -> int:
    """A usage component as a non-negative int; malformed or negative
    vendor values count 0 instead of crashing the runner mid-session or
    REDUCING cumulative spend (PR #41 review)."""
    try:
        return max(0, int(usage.get(field) or 0))
    except (TypeError, ValueError):
        return 0


def parse_usage_claude(lines: list[str]) -> int:
    """Cumulative NEW-token spend from claude stream-json (HAR-5, budget
    semantics decided at the dry run — numbers in ADR-h2 limitations):
    input + cache_creation + output, EXCLUDING cache re-reads — each
    token counts once when first processed ("unique context
    throughput")."""
    total = 0
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        usage = (event.get("message") or {}).get("usage") or {}
        total += (
            _tok(usage, "input_tokens")
            + _tok(usage, "cache_creation_input_tokens")
            + _tok(usage, "output_tokens")
        )
    return total


def parse_usage_codex(lines: list[str]) -> int:
    """Cumulative NEW-token spend from codex --json, mirroring the claude
    rule: codex `input_tokens` INCLUDES the cached slice, so new input =
    input - cached, plus output. turn.completed events only (item.started
    duplicates never carry usage — PR #33 lesson)."""
    total = 0
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage") or {}
        new_input = _tok(usage, "input_tokens") - _tok(usage, "cached_input_tokens")
        if new_input < 0:
            # cached > input means the vendor's accounting scales diverged;
            # clamping silently would systematically undercount (PR #41)
            print(f"[h2] WARNING codex usage anomaly: new_input={new_input}", file=sys.stderr)
        total += max(0, new_input) + _tok(usage, "output_tokens")
    return total


PARSE_USAGE = {"claude": parse_usage_claude, "codex": parse_usage_codex}


class UsageCounter:
    """Issue #42: the runner is the SOLE authority on token spend. Lines
    are fed from the LIVE stdout pipe and accumulated in memory; the
    on-disk log is a tee for post-hoc analysis, never the count's source
    — an unconfined session rewriting its log cannot move the ceiling."""

    def __init__(self, agent: str):
        self._parse = PARSE_USAGE[agent]
        self.total = 0

    def feed(self, line: str) -> None:
        self.total += self._parse([line])


def budget_stop(
    tokens: int, token_ceiling: int, wall_s: float, wall_ceiling_s: float
) -> str | None:
    """The runner-enforced ceilings (ADR-h2 point 3); None = keep going."""
    if tokens >= token_ceiling:
        return "token_budget"
    if wall_s >= wall_ceiling_s:
        return "wall_budget"
    return None


# ---------------------------------------------------------------- protocol


def _parse_seeds(spec: str) -> set[int]:
    if ".." in spec:
        a, b = spec.split("..", 1)
        return set(range(int(a), int(b) + 1))
    return {int(s) for s in spec.split(",")}


def validate_seed_ranges(dev: str, holdout: str) -> str | None:
    """ADR-h2 point 4: held-out seeds MUST be disjoint from the dev range
    the agent may roll; overlap refuses the campaign."""
    overlap = _parse_seeds(dev) & _parse_seeds(holdout)
    if overlap:
        return f"dev and holdout seed ranges must be disjoint; both contain {sorted(overlap)[:5]}"
    return None


TIER_EMBODIMENT = {"T1": "franka", "S1": "mobile", "S2": "mobile", "S3": "mobile"}


def campaign_prompt(
    tier: str, token_ceiling: int, wall_h: float, dev_seeds: str, note: str = ""
) -> str:
    extra = f"\n{note}" if note else ""
    return f"""You are the RESEARCH agent for an AISLE {tier} campaign.{extra}

Read harness/CLAUDE.research.md FIRST — it is your entire contract
(goal, hard rules, the copy-paste tool loop, failure taxonomy).

Campaign parameters:
- Tier: {tier}. Development seeds: {dev_seeds} (held-out scoring seeds are
  withheld from you).
- Budgets: {token_ceiling:,} tokens and {wall_h:g} h wall for this campaign;
  episode and wall ceilings are also enforced by the harness ledger.
- Your deliverable is {DELIVERABLE}: keep it pointed at your current best
  system at ALL times — it is scored on held-out seeds after your session
  ends, exactly as `harness rollout` would run it.

Iterate: compose, validate, log the idea, roll out, read traces, close the
idea, improve. Maximize verified success within budget; remember the 10x
wrong-medicine penalty."""


def campaign_treatment(
    agent: str, model: str, oid: str, dev_seeds: str, holdout_seeds: str
) -> dict:
    try:
        version = subprocess.run(
            [agent, "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # CLI absent or hung (e.g. CI): treatment stays constructible — a
        # real campaign fails at spawn with a proper InfraError instead
        version = "not-installed"
    prompt = campaign_prompt("T1", 0, 0.0, dev_seeds)  # sha over the TEMPLATE shape
    return {
        "commit": oid,
        "agent": agent,
        "agent_cli_version": version,
        "model": model,
        "prompt_template_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "dev_seeds": dev_seeds,
        "holdout_seeds": holdout_seeds,
        "holdout_episodes": HOLDOUT_EPISODES,
        "claude_max_turns": None,  # campaigns are long-form (ADR-h2 point 1)
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "session_spawn": {
            "stdin": "devnull",
            "confinement": "none (ADR-h2 point 5)",
        },
    }


TREATMENT_IDENTITY = ("commit", "agent", "model", "dev_seeds", "holdout_seeds")


def agent_cmd_campaign(agent: str, model: str, prompt: str) -> list[str]:
    if agent == "claude":
        return [
            "claude",
            "-p",
            prompt,
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--verbose",
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
        # parity with the claude arm's v1 no-sandbox decision (ADR-h2 p5)
        "--sandbox",
        "danger-full-access",
        "-c",
        "approval_policy=never",
        prompt,
    ]


# ---------------------------------------------------------------- artifacts


def audit_frozen(wt: Path, oid: str) -> list[str]:
    """ADR-h2 point 5: diff the frozen paths in the worktree against the
    pinned OID; any drifted path is reported (the gate would have refused
    rollouts under drift, but the audit makes tampering visible even
    without a rollout)."""
    paths = [*FROZEN_DIRS, *FROZEN_FILES]
    diff = subprocess.run(
        ["git", "diff", "--name-only", oid, "--", *paths],
        cwd=wt,
        capture_output=True,
        text=True,
    )
    return [line for line in diff.stdout.splitlines() if line.strip()]


def resolve_commit(repo: Path, rev: str | None) -> str:
    """The campaign's pinned OID: an explicit rev (clean-rerun support —
    committed analysis of the SAME experiment is an experimental input, so
    replication arms must pin a commit that predates it; the codex H2
    contamination lesson) or HEAD. Unknown revs refuse (CON-8 JSON)."""
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{rev or 'HEAD'}^{{commit}}"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(json.dumps({"ok": False, "error": f"unknown --commit rev {rev!r}"}))
    return proc.stdout.strip()


def sweep_worktree(wt: Path) -> list[int]:
    """Post-session orphan sweep (PR #44 follow-up; dora-rs/dora#2856):
    kill any process whose command line references a script under the
    worktree — campaign rollouts leak dora nodes that idle or spin
    forever. Never touches bystanders."""
    needle = str(wt)
    killed: list[int] = []
    ps = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True)
    for line in ps.stdout.splitlines():
        pid_str, _, command = line.strip().partition(" ")
        if needle in command and pid_str.isdigit() and int(pid_str) != os.getpid():
            try:
                os.kill(int(pid_str), signal.SIGKILL)
                killed.append(int(pid_str))
            except (ProcessLookupError, PermissionError):
                pass
    if killed:
        print(f"[h2] swept {len(killed)} leaked worktree process(es)", file=sys.stderr)
    return killed


def campaign_metrics(wt: Path, session_t0: float) -> dict:
    """ADR-h2 point 7, from harness-written artifacts only: chronological
    pass1 trajectory, wall time to the first verified success, wrong_object
    total (H5), episode totals."""
    rollouts = []
    first_success: float | None = None
    wrong_object = 0
    episodes_total = 0
    for manifest_path in sorted((wt / "runs").glob("*/manifest.json")):
        episodes_path = manifest_path.parent / "episodes.jsonl"
        if not episodes_path.exists():
            continue
        episodes = [
            json.loads(line) for line in episodes_path.read_text().splitlines() if line.strip()
        ]
        mtime = episodes_path.stat().st_mtime
        successes = sum(1 for e in episodes if e.get("status") == "success")
        wrong_object += sum(1 for e in episodes if e.get("failure") == "wrong_object")
        episodes_total += len(episodes)
        if successes and (first_success is None or mtime < first_success):
            first_success = mtime
        rollouts.append(
            {
                "run_id": json.loads(manifest_path.read_text()).get("run_id"),
                "mtime": mtime,
                "episodes": len(episodes),
                "pass1": round(successes / len(episodes), 3) if episodes else 0.0,
            }
        )
    rollouts.sort(key=lambda r: r["mtime"])
    return {
        "rollouts": rollouts,
        "episodes_total": episodes_total,
        "wrong_object_total": wrong_object,
        "first_success_wall_s": (
            round(first_success - session_t0, 1) if first_success is not None else None
        ),
    }


# ---------------------------------------------------------------- the run


def _default_budgets(root: Path) -> tuple[int, float]:
    with open(root / "harness" / "budget.toml", "rb") as f:
        campaign = tomllib.load(f)["campaign"]
    return int(campaign["tokens"]), float(campaign["wall_h"])


def run_session(agent: str, cmd: list[str], wt: Path, out: Path, ceilings: dict) -> dict:
    """Spawn the session, count token spend from the LIVE stdout pipe
    (issue #42: the on-disk log is a tee, never the count's source), kill
    the process group at a ceiling. Returns the session record."""
    import threading

    log_path = out / "session.jsonl"
    stderr_path = out / "session.stderr"
    samples_path = out / "token_samples.jsonl"
    t0 = time.monotonic()
    stopped = "agent_done"
    counter = UsageCounter(agent)
    tee_failure: list[str] = []
    with open(log_path, "w") as log, open(stderr_path, "w") as err:
        proc = subprocess.Popen(
            cmd,
            cwd=wt,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=err,
            # errors="replace": non-UTF8 session bytes must never kill the
            # reader — strict decode was a silent fail-OPEN (PR #43 review)
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )

        def tee() -> None:
            # FAIL CLOSED (PR #43 review): if the tee dies (disk full,
            # closed handle), the counter freezes and the token ceiling is
            # silently disabled — so kill the session and record infra
            try:
                for line in proc.stdout:
                    log.write(line)
                    log.flush()
                    counter.feed(line)
            except Exception as exc:  # noqa: BLE001 — any tee death is infra
                tee_failure.append(repr(exc))
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        reader = threading.Thread(target=tee, daemon=True)
        reader.start()
        with open(samples_path, "a") as samples:
            while proc.poll() is None:
                time.sleep(POLL_S)
                wall_s = time.monotonic() - t0
                samples.write(
                    json.dumps({"wall_s": round(wall_s, 1), "tokens": counter.total}) + "\n"
                )
                samples.flush()
                reason = budget_stop(
                    ceilings["prior_tokens"] + counter.total,
                    ceilings["token_ceiling"],
                    ceilings["prior_wall_s"] + wall_s,
                    ceilings["wall_ceiling_s"],
                )
                if reason:
                    stopped = reason
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=30)
                    break
        rc = proc.wait()
        reader.join(timeout=TEE_JOIN_S)
        if reader.is_alive():
            # an escaped grandchild holds the pipe open past killpg: the
            # drain is incomplete — attribute a possibly-short count
            print("[h2] WARNING stream drain incomplete after session exit", file=sys.stderr)
        try:
            proc.stdout.close()
        except OSError:
            pass
        total = counter.total  # pinned before the log handle closes
    if tee_failure:
        raise InfraError(
            f"telemetry tee failed ({tee_failure[0]}) — the token ceiling "
            "could not be trusted; session killed (not an agent outcome)"
        )
    if stopped == "agent_done" and rc != 0:
        raise InfraError(f"{agent} CLI exited rc={rc} (not an agent outcome)")
    return {
        "stopped": stopped,
        "rc": rc,
        "tokens": total,
        "wall_s": round(time.monotonic() - t0, 1),
    }


def score_holdout(wt: Path, holdout_seeds: str, session_index: int, tier: str = "T1") -> dict:
    """ADR-h2 point 4: the deliverable graph on held-out seeds, run by the
    RUNNER in the session worktree through the standard pipeline."""
    if not (wt / DELIVERABLE).exists():
        return {"ok": False, "error": f"no deliverable at {DELIVERABLE}"}
    cmd = [
        "uv",
        "run",
        "harness",
        "rollout",
        "--graph",
        DELIVERABLE,
        "--tier",
        tier,
        "--embodiment",
        TIER_EMBODIMENT[tier],
        "--episodes",
        str(HOLDOUT_EPISODES),
        "--seeds",
        holdout_seeds,
        "--run-id",
        f"campaign-holdout-{session_index:02d}",
        # protocol spend, not agent spend: the logged local baseline (the
        # worktree IS the pinned commit) and no idea gate (runner machinery)
        "--env-baseline",
        "local",
        "--no-idea-gate",
    ]
    proc = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, timeout=3600)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"holdout scoring produced no JSON (rc={proc.returncode})"}


def load_existing(out: Path, current: dict) -> dict | None:
    record_path = out / "campaign.json"
    if not record_path.exists():
        return None
    existing = json.loads(record_path.read_text())
    prior = existing.get("treatment") or {}
    for key in TREATMENT_IDENTITY:
        if prior.get(key) != current.get(key):
            raise SystemExit(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"resume treatment mismatch on {key!r}: "
                        f"existing={prior.get(key)!r} current={current.get(key)!r}",
                    }
                )
            )
    return existing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=("claude", "codex"), default="claude")
    parser.add_argument("--model", default=None)
    parser.add_argument("--tier", default="T1", choices=tuple(TIER_EMBODIMENT))
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs" / "h2")
    parser.add_argument(
        "--commit",
        default=None,
        help="pin the campaign worktree at this rev (default HEAD); replication "
        "arms MUST predate any committed analysis of the same experiment",
    )
    parser.add_argument("--budget-tokens", type=int, default=None)
    parser.add_argument("--wall-h", type=float, default=None)
    parser.add_argument("--dev-seeds", default="0..49")
    parser.add_argument("--holdout-seeds", default="100..107")
    parser.add_argument("--keep-worktree", action="store_true")
    args = parser.parse_args()

    error = validate_seed_ranges(args.dev_seeds, args.holdout_seeds)
    if error:
        print(json.dumps({"ok": False, "error": error}))
        return 1
    model = args.model or DEFAULT_MODELS[args.agent]
    default_tokens, default_wall = _default_budgets(REPO_ROOT)
    token_ceiling = args.budget_tokens or default_tokens
    wall_h = args.wall_h or default_wall

    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    oid = resolve_commit(REPO_ROOT, args.commit)
    treatment = campaign_treatment(args.agent, model, oid, args.dev_seeds, args.holdout_seeds)
    treatment["token_ceiling"] = token_ceiling
    treatment["wall_ceiling_h"] = wall_h
    existing = load_existing(args.out, treatment)
    sessions = (existing or {}).get("sessions", [])
    prior_tokens = sum(s["tokens"] for s in sessions)
    prior_wall_s = sum(s["wall_s"] for s in sessions)

    wt = args.out / "worktree"
    if not wt.exists():
        print(f"[h2] creating worktree at {oid[:8]}", file=sys.stderr)
        make_worktree(oid, wt)
    session_index = len(sessions)
    prompt = campaign_prompt(args.tier, token_ceiling, wall_h, args.dev_seeds)
    cmd = agent_cmd_campaign(args.agent, model, prompt)
    session_dir = args.out / f"session_{session_index:02d}"
    session_dir.mkdir(exist_ok=True)
    print(f"[h2] session {session_index} starting ({args.agent}/{model})", file=sys.stderr)
    t0_epoch = time.time()
    session = run_session(
        args.agent,
        cmd,
        wt,
        session_dir,
        {
            "prior_tokens": prior_tokens,
            "prior_wall_s": prior_wall_s,
            "token_ceiling": token_ceiling,
            "wall_ceiling_s": wall_h * 3600.0,
        },
    )
    session["t0_epoch"] = t0_epoch
    sessions.append(session)

    sweep_worktree(wt)  # the session's rollouts may have leaked nodes
    drift = audit_frozen(wt, oid)
    holdout = score_holdout(wt, args.holdout_seeds, session_index, args.tier)
    sweep_worktree(wt)  # ...and so may the holdout rollout
    metrics = campaign_metrics(wt, session_t0=sessions[0]["t0_epoch"])
    record = {
        "ok": not drift,
        "treatment": treatment,
        "sessions": sessions,
        "tokens_spent": prior_tokens + session["tokens"],
        "wall_spent_s": round(prior_wall_s + session["wall_s"], 1),
        "frozen_drift": drift,
        "holdout": {
            k: holdout.get(k) for k in ("ok", "error", "pass1", "pass8", "failures", "run_id")
        },
        "metrics": metrics,
    }
    (args.out / "campaign.json").write_text(json.dumps(record, indent=1))
    print(
        json.dumps(
            {
                "ok": record["ok"],
                "record": str(args.out / "campaign.json"),
                **{
                    "stopped": session["stopped"],
                    "tokens_spent": record["tokens_spent"],
                    "holdout_pass1": record["holdout"].get("pass1"),
                    "first_success_wall_s": metrics["first_success_wall_s"],
                    "wrong_object_total": metrics["wrong_object_total"],
                    "frozen_drift": drift,
                },
            }
        )
    )
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
