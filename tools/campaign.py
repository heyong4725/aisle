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
import re
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
DEV_SEEDS = "0..49"
HOLDOUT_SEEDS = "100..107"
TEE_JOIN_S = 10.0  # stream-drain grace after the session exits
BASELINE_COMPAT_TEMPLATE = REPO_ROOT / "tools" / "campaign_baseline_sitecustomize.py"


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
            print(f"[campaign] WARNING codex usage anomaly: new_input={new_input}", file=sys.stderr)
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


TIER_EMBODIMENT = {
    "T1": "franka",
    "T2": "franka",
    "T3": "franka",
    "T4": "franka",
    "S1": "mobile",
    "S2": "mobile",
    "S3": "mobile",
}


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
- You are a NON-INTERACTIVE session: the moment you end your turn the
  session is OVER, permanently — nothing re-invokes you. NEVER background
  a rollout or "wait for results": run `harness rollout` as ONE
  synchronous command with a long tool timeout (S-tier rollouts take
  TENS OF MINUTES; e.g. Bash timeout 2400000 ms) and block until its
  JSON returns. A rollout is not failed just because it is slow.
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
        "baseline_compat_sha256": hashlib.sha256(BASELINE_COMPAT_TEMPLATE.read_bytes()).hexdigest(),
        "session_spawn": {
            "stdin": "devnull",
            "confinement": "none (ADR-h2 point 5)",
        },
        # issue #96 / PR #98 review: the isolation policy is part of the
        # treatment — resuming an unisolated (pre-#98) campaign with an
        # isolated runner would mix two treatments in one record. Old
        # records carry no key (None) and fail the resume identity check.
        "session_isolation_policy": "isolated-home-baseline-compat-v2",
    }


TREATMENT_IDENTITY = (
    "commit",
    "agent",
    "model",
    "dev_seeds",
    "holdout_seeds",
    "session_isolation_policy",
)


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
    # expert graphs are part of the env-hashed frozen set (env_hash.py)
    paths = [*FROZEN_DIRS, *FROZEN_FILES, "graphs/expert_*.yaml"]
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


def attach_historical_baseline_compat(wt: Path, session_dir: Path, pin: str, env: dict) -> dict:
    """Make the immutable campaign selector work in pre-PR-166 checkouts.

    The research agent runs ``uv run harness`` FROM the pinned worktree, so
    changing only the current runner/CLI cannot affect a historical pin.  A
    session-local ``sitecustomize`` patches the old rollout gate at Python
    startup, validates the OID against protected server main, and records the
    OID.  The historical tree remains byte-exact.  Unknown old interfaces fail
    before budget spend instead of silently following moving main.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", pin):
        raise InfraError(f"campaign compatibility requires a full commit OID, got {pin!r}")
    cli_path = wt / "src" / "aisle" / "harness" / "cli.py"
    rollout_path = wt / "src" / "aisle" / "harness" / "rollout.py"
    try:
        cli_text = cli_path.read_text()
        rollout_text = rollout_path.read_text()
    except OSError as error:
        raise InfraError(
            f"cannot inspect historical harness for baseline support: {error}"
        ) from error
    native = "AISLE_ENV_BASELINE" in cli_text and "_COMMIT_OID" in rollout_text
    if native:
        return {"mode": "native", "pin": pin}
    required = {
        str(cli_path): "--env-baseline" in cli_text,
        str(rollout_path): all(
            anchor in rollout_text for anchor in ("def resolve_trusted_baseline(", "def run_gates(")
        ),
    }
    missing = [path for path, supported in required.items() if not supported]
    if missing:
        raise InfraError(
            "historical campaign checkout predates the supported baseline gate: "
            + ", ".join(missing)
        )
    try:
        template = BASELINE_COMPAT_TEMPLATE.read_bytes()
        compat_dir = session_dir / "baseline_compat"
        compat_dir.mkdir(parents=True, exist_ok=True)
        (compat_dir / "sitecustomize.py").write_bytes(template)
    except OSError as error:
        raise InfraError(f"cannot install historical baseline compatibility: {error}") from error
    # Replace, do not append, an ambient operator PYTHONPATH: it is not part of
    # the treatment and could shadow the historical worktree's package.
    env["PYTHONPATH"] = str(compat_dir)
    return {
        "mode": "injected",
        "pin": pin,
        "pythonpath": str(compat_dir),
        "sha256": hashlib.sha256(template).hexdigest(),
    }


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
        print(f"[campaign] swept {len(killed)} leaked worktree process(es)", file=sys.stderr)
    return killed


def campaign_metrics(
    wt: Path, session_t0: float, since: float | None = None, pin: str | None = None
) -> dict:
    """ADR-h2 point 7, from harness-written artifacts only: chronological
    pass1 trajectory, wall time to the first verified success, wrong_object
    total (H5), episode totals. Each rollout entry carries its manifest's
    provenance (git_sha / env_baseline / env_baseline_oid / env_attested)
    so the committed record is auditable; with `pin` set, first_success is
    derived ONLY from admissible rollouts — trusted-baseline runs at the
    treatment pin (PR #76 review: a local skill-eval success must never
    supply the headline verdict metric)."""
    rollouts = []
    first_success: float | None = None
    wrong_object = 0
    episodes_total = 0
    for manifest_path in sorted((wt / "runs").glob("*/manifest.json")):
        episodes_path = manifest_path.parent / "episodes.jsonl"
        if not episodes_path.exists():
            continue
        # `since` scopes EVERY aggregate (first_success, wrong_object,
        # totals) to one scenario in a persistent worktree — filtering
        # only the trajectory contaminated the H3 headline (PR #48 review)
        if since is not None and episodes_path.stat().st_mtime < since:
            continue
        episodes = [
            json.loads(line) for line in episodes_path.read_text().splitlines() if line.strip()
        ]
        mtime = episodes_path.stat().st_mtime
        successes = sum(1 for e in episodes if e.get("status") == "success")
        wrong_object += sum(1 for e in episodes if e.get("failure") == "wrong_object")
        episodes_total += len(episodes)
        manifest = json.loads(manifest_path.read_text())
        admissible = pin is None or (
            manifest.get("git_sha") == pin
            and manifest.get("env_baseline") == pin
            and manifest.get("env_baseline_oid") == pin
        )
        if successes and admissible and (first_success is None or mtime < first_success):
            first_success = mtime
        rollouts.append(
            {
                "run_id": manifest.get("run_id"),
                "mtime": mtime,
                "episodes": len(episodes),
                "pass1": round(successes / len(episodes), 3) if episodes else 0.0,
                "git_sha": manifest.get("git_sha"),
                "env_baseline": manifest.get("env_baseline"),
                "env_baseline_oid": manifest.get("env_baseline_oid"),
                "env_attested": manifest.get("env_attested"),
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


def isolated_session_env(out: Path, env_baseline_oid: str | None = None) -> tuple[dict, dict]:
    """Issue #96: campaign sessions must not inherit the operator's
    config/home — the S3-r3 agent's third action was reading ~/.claude
    memory (annotated transcript, event [21]): ambient operator state
    outside the treatment's defined persistence surface (ADR-h3 D3),
    invisible to the wipe machinery. HOME and CLAUDE_CONFIG_DIR are
    rebound to a per-session scratch home so the only knowledge channels
    are protocol-defined ones; the returned record makes the isolation
    machine-detectable in every scenario record (absence in future
    audits = unisolated session). Auth caveat: credential stores keyed
    off HOME/config break under this — launchers MUST auth-probe with
    this env and refuse the campaign on failure (fail closed), never
    silently fall back to the operator home."""
    home = out / "agent_home"
    rotated = None
    if home.exists():
        # PR #98 review P2: an aborted attempt leaves state in agent_home
        # (H2 reuses session_XX on resume; the auth-probe dir is fixed) —
        # "per-session scratch" must be FRESH every launch. Rotate the
        # occupied home aside, preserving abort artifacts for audit.
        n = 1
        while (dest := out / f"agent_home-superseded{n}").exists():
            n += 1
        home.rename(dest)
        # the rotated home may hold an aborted attempt's live credential
        # seed — scrub it NOW; audit artifacts persist, tokens do not
        scrub_session_credentials(dest)
        rotated = str(dest)
    config = home / ".claude"
    config.mkdir(parents=True, exist_ok=True)
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CLAUDE_CONFIG_DIR"] = str(config)
    # PR #98 review round 2: agent CLIs honor explicit home overrides
    # that BYPASS the HOME rebind — an operator-exported CODEX_HOME (or
    # XDG_* base dirs) would expose the operator's directories despite
    # the isolation record. Pin every such override into the scratch.
    env["CODEX_HOME"] = str(codex_home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    env["XDG_STATE_HOME"] = str(home / ".local" / "state")
    # Issue #91: the runner, not each rollout, selects the campaign's
    # immutable trust anchor.  The harness CLI consumes this as its
    # default while an explicit flag remains available for human dev
    # runs.  Never inherit an unrelated operator pin into a probe.
    if env_baseline_oid is None:
        env.pop("AISLE_ENV_BASELINE", None)
    else:
        env["AISLE_ENV_BASELINE"] = env_baseline_oid
    record = {
        "home": str(home),
        "claude_config_dir": str(config),
        "codex_home": str(codex_home),
        "xdg_rebound": True,
    }
    if env_baseline_oid is not None:
        record["env_baseline_oid"] = env_baseline_oid
    if rotated:
        record["rotated_prior_home"] = rotated
    return env, record


# the dedicated campaign login (issue #96 follow-up): sessions never see
# the operator's credentials — the operator logs the CAMPAIGN identity in
# once (`CODEX_HOME=~/.codex-campaign codex login` /
# `CLAUDE_CONFIG_DIR=~/.claude-campaign claude` + /login), and each
# scratch home receives ONLY the credential file from that dir. An
# explicit allow-list copy: credentials are not knowledge.
CAMPAIGN_LOGIN = {
    "claude": (Path.home() / ".claude-campaign", ".credentials.json"),
    "codex": (Path.home() / ".codex-campaign", "auth.json"),
}


def seed_session_credentials(agent: str, env: dict) -> tuple[dict | None, str | None]:
    """Copy the campaign login's credential file into the isolated home.
    Returns (record_entry, error): a missing or unreadable campaign login
    is an ERROR the launcher must refuse on (CON-8) with the setup
    command — never a silent fallback to the operator's own login."""
    login_dir, cred_name = CAMPAIGN_LOGIN[agent]
    src = login_dir / cred_name
    if not src.exists():
        hint = (
            f"CODEX_HOME={login_dir} codex login"
            if agent == "codex"
            else f"CLAUDE_CONFIG_DIR={login_dir} claude  # then /login"
        )
        return None, (
            f"no campaign login for {agent!r}: {src} missing — create it once with: {hint}"
        )
    dest_dir = Path(env["CODEX_HOME"] if agent == "codex" else env["CLAUDE_CONFIG_DIR"])
    dest = dest_dir / cred_name
    try:
        # PR #100 rounds 1-2: failures surface as the CON-8 refusal, the
        # file is PRIVATE FROM CREATION (0600 at open — a write-then-chmod
        # left a world-readable window and, on chmod failure, a lingering
        # token), and any failure removes whatever was written
        payload = src.read_bytes()
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            # PR #100 round 3: os.write may write FEWER bytes than asked
            # (short write) — loop to completion, and treat zero progress
            # as an error so a truncated token never reports success
            view = memoryview(payload)
            while view:
                n = os.write(fd, view)
                if n <= 0:
                    raise OSError(f"short write seeding {dest} ({len(view)} bytes left)")
                view = view[n:]
        finally:
            os.close(fd)
    except OSError as exc:
        dest.unlink(missing_ok=True)  # never leave a partial token behind
        return None, f"credential seed failed for {agent!r}: {exc}"
    return {"credential_seed": f"{agent}-campaign login ({cred_name})"}, None


def scrub_session_credentials(home: Path) -> list[str]:
    """Remove the seeded credential files at their EXACT canonical
    locations — home/.codex/auth.json and home/.claude/.credentials.json
    (where the seed writes, and where a CLI's own token refresh rewrites).
    Never a recursive name match: an agent's workspace may legitimately
    contain same-named files that are audit artifacts (PR #100 round 2).
    Called after every probe and session (and on home rotation, which
    would otherwise preserve an aborted attempt's live token
    indefinitely). Returns the scrubbed paths, recorded for audit; scrub
    failures are NOT swallowed — a token we cannot delete is loud."""
    scrubbed = []
    for agent, (_, cred_name) in CAMPAIGN_LOGIN.items():
        cred = home / (".codex" if agent == "codex" else ".claude") / cred_name
        if cred.is_file():
            cred.unlink()
            scrubbed.append(str(cred))
    return scrubbed


def probe_agent_auth(agent: str, model: str, env: dict, cwd: Path) -> str | None:
    """Issue #96 fail-closed companion to isolated_session_env: HOME/
    config isolation breaks credential stores keyed off the operator
    home, so the launcher runs ONE trivial prompt under the isolated env
    before any budget is spent. Returns an error string (refuse the
    campaign) or None. Never silently falls back to the operator home."""
    cmd = agent_cmd_campaign(agent, model, "Reply with exactly: ok")
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"auth probe failed to run under isolated env: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        return f"auth probe exited {proc.returncode} under isolated env: {tail}"
    return None


def run_session(
    agent: str,
    cmd: list[str],
    wt: Path,
    out: Path,
    ceilings: dict,
    env: dict | None = None,
) -> dict:
    """Spawn the session, count token spend from the LIVE stdout pipe
    (issue #42: the on-disk log is a tee, never the count's source), kill
    the process group at a ceiling. `env` (issue #96) replaces the child
    environment — None inherits, for non-campaign callers. Returns the
    session record."""
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
            env=env,
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
            print("[campaign] WARNING stream drain incomplete after session exit", file=sys.stderr)
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


def score_holdout(wt: Path, holdout_seeds: str, run_tag: str, tier: str = "T1") -> dict:
    """ADR-h2 point 4: the deliverable graph on held-out seeds, run by the
    RUNNER in the session worktree through the standard pipeline."""
    if not (wt / DELIVERABLE).exists():
        # a STRUCTURED outcome, not prose: the analyzer keys its
        # no-deliverable classification on this field (PR #76 review —
        # never a substring match against the error message)
        return {
            "ok": False,
            "outcome": "no_deliverable",
            "error": f"no deliverable at {DELIVERABLE}",
        }
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
        f"campaign-holdout-{run_tag}",
        # protocol spend, not agent spend: the logged local baseline (the
        # worktree IS the pinned commit) and no idea gate (runner machinery)
        "--env-baseline",
        "local",
        "--no-idea-gate",
    ]
    # S-tier episodes carry 2100 s wall budgets EACH (the dry run's 3600 s
    # cap killed S1 scoring mid-run); T2-class desk tiers carry 400 s each
    # (rollout tier_budgets — the scan tour); a scoring timeout is a
    # recorded outcome, never a campaign abort
    if tier in ("T1", "T4"):
        timeout = 3600
    elif tier in ("T2", "T3"):
        timeout = 420 + HOLDOUT_EPISODES * 400 + 600
    else:
        timeout = 420 + HOLDOUT_EPISODES * 2100 + 600
    try:
        proc = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"holdout scoring exceeded {timeout}s"}
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
    parser.add_argument("--dev-seeds", default=DEV_SEEDS)
    parser.add_argument("--holdout-seeds", default=HOLDOUT_SEEDS)
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

    # issue #96 fail-closed (PR #98 review P1): probe auth under the
    # ISOLATED env before ANY side effect — worktree creation, session
    # dir, budget spend. An empty home that breaks credentials must
    # refuse here with CON-8 JSON, never start a metered session.
    probe_env, _ = isolated_session_env(args.out / "auth_probe")
    _, seed_error = seed_session_credentials(args.agent, probe_env)
    if seed_error:
        print(json.dumps({"ok": False, "error": seed_error}))
        return 1
    try:
        probe_error = probe_agent_auth(args.agent, model, probe_env, args.out)
    finally:
        # tokens never persist, even past an unexpected probe exception
        scrub_session_credentials(Path(probe_env["HOME"]))
    if probe_error:
        print(json.dumps({"ok": False, "error": probe_error}))
        return 1

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
    session_env, session_isolation = isolated_session_env(session_dir, env_baseline_oid=oid)
    session_isolation["baseline_compat"] = attach_historical_baseline_compat(
        wt, session_dir, oid, session_env
    )
    seed_rec, seed_error = seed_session_credentials(args.agent, session_env)
    if seed_error:
        print(json.dumps({"ok": False, "error": seed_error}))
        return 1
    session_isolation.update(seed_rec)
    try:
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
            env=session_env,
        )
    finally:
        # PR #100 review P1: the seeded token must not outlive the
        # session — runs/ artifact directories persist indefinitely
        session_isolation["credentials_scrubbed"] = scrub_session_credentials(
            Path(session_env["HOME"])
        )
    session["isolation"] = session_isolation
    session["t0_epoch"] = t0_epoch
    sessions.append(session)

    sweep_worktree(wt)  # the session's rollouts may have leaked nodes
    drift = audit_frozen(wt, oid)
    holdout = score_holdout(wt, args.holdout_seeds, f"{session_index:02d}", args.tier)
    sweep_worktree(wt)  # ...and so may the holdout rollout
    metrics = campaign_metrics(wt, session_t0=sessions[0]["t0_epoch"], pin=oid)
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
