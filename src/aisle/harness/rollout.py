"""Rollout runner (SPEC 070 HAR-1..5).

`harness rollout --graph G --tier T --episodes N --seeds a..b --reset
teleport` gates (HAR-2: env hash, validation, open idea), instruments the
graph with a trace recorder, drives it via the rollout-client's env
config, and writes runs/<run_id>/ with the manifest (HAR-4), per-episode
results, Arrow traces, and the overhead video. pass@8 follows HAR-3's
in-context-retry semantics: the episode records carry a retries count
(0 while the task-state-machine runs single-attempt, ADR-10) — pass@8 is
NEVER computed as best-of-8 independent episodes.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform as platform_module
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

from aisle.harness.ideas import open_ideas
from aisle.harness.reaper import reap_orphans
from aisle.harness.validate import validate

# every declared node/output endpoint is traced (HAR-4); image topics
# record metadata-only rows — pixels live in the mp4 (ADR-11)
# hard-won budgets (see ADR-11): the per-episode verifier budget in SIM
# seconds; the one-off genesis build and per-episode WALL budgets; and
# the stall detector's thresholds (pre-data = the build produces no
# traces; post-data = a dead bridge freezes the stream)
EPISODE_TIMEOUT_S = 60
GENESIS_BUILD_BUDGET_S = 420
PER_EPISODE_BUDGET_S = 150
PRE_DATA_STALL_S = 600
STALL_S = 180
# retail tiers (RS-6, ADR-18): store-sim rtf ~0.1 on the dev machine — the
# fixed-seed S1 episode runs ~101.5 sim s / ~25 wall min plus a ~2.5 min
# store build (first green run: 28:39 total). The desk budgets above would
# kill a HEALTHY retail episode at 60 sim s / 150 wall s (PR #21)
RETAIL_EPISODE_TIMEOUT_S = 600
RETAIL_PER_EPISODE_BUDGET_S = 2100


def tier_budgets(tier: str) -> tuple[int, int]:
    """(episode timeout in SIM seconds, per-episode WALL budget in seconds)
    for a tier: `harness rollout` is the public path for EVERY tier (HAR-1,
    RS-6), so retail tiers get nightly-suite-scale budgets, desk tiers keep
    the tight ADR-11 ones."""
    if tier in ("S1", "S2", "S3"):
        return RETAIL_EPISODE_TIMEOUT_S, RETAIL_PER_EPISODE_BUDGET_S
    return EPISODE_TIMEOUT_S, PER_EPISODE_BUDGET_S


def parse_seed_range(spec: str) -> list[int]:
    """'0..49' -> [0..49]; '3' -> [3]; '1,4,7' -> [1, 4, 7]."""
    if ".." in spec:
        a, b = spec.split("..", 1)
        return list(range(int(a), int(b) + 1))
    return [int(s) for s in spec.split(",")]


def compute_metrics(episodes: list[dict]) -> dict:
    """HAR-1/HAR-3: pass1 counts first-attempt successes; pass8 counts an
    episode as passed if it succeeded within <=8 IN-CONTEXT retries
    (retries field, 0 today) — never best-of-8 independent episodes."""
    n = len(episodes)
    pass1 = sum(1 for e in episodes if e["status"] == "success" and e.get("retries", 0) == 0)
    pass8 = sum(1 for e in episodes if e["status"] == "success" and e.get("retries", 0) <= 8)
    failures: dict[str, int] = {}
    for e in episodes:
        if e["status"] != "success":
            reason = e.get("failure") or "unknown"
            failures[reason] = failures.get(reason, 0) + 1
    return {
        "pass1": pass1 / n if n else 0.0,
        "pass8": pass8 / n if n else 0.0,
        "failures": failures,
    }


def load_campaign_budget(root: Path) -> dict:
    """ADR-21: the campaign ceilings from harness/budget.toml (FROZEN — a
    research agent must not raise its own budget)."""
    import tomllib

    with open(root / "harness" / "budget.toml", "rb") as f:
        return tomllib.load(f)["campaign"]


_LEDGER = "runs/campaign_ledger.jsonl"


def _entry_hash(prev_hash: str, entry: dict) -> str:
    """Tamper-evident chain (ADR-21 round 3): each ledger entry hashes its
    predecessor's hash + its own canonical content — an edited or dropped
    line breaks every hash after it, and each run manifest records its
    entries' hashes so the audit can cross-verify."""
    canon = json.dumps({k: entry[k] for k in sorted(entry) if k != "hash"}, sort_keys=True)
    return hashlib.sha256((prev_hash + canon).encode()).hexdigest()


def budget_ledger(root: Path) -> list[dict]:
    path = root / _LEDGER
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verify_ledger(root: Path) -> bool:
    """True iff every entry's chain hash verifies."""
    prev = ""
    for entry in budget_ledger(root):
        if entry.get("hash") != _entry_hash(prev, entry):
            return False
        prev = entry["hash"]
    return True


def _ledger_spend(entries: list[dict]) -> tuple[int, float]:
    """(episodes, wall_s) spent: settled runs at their actuals, UNSETTLED
    reservations at their reserved episodes (a crashed run stays charged —
    conservative accounting, ADR-21 round 3)."""
    settled = {e["run_id"]: e for e in entries if e.get("kind") == "settle"}
    episodes = wall = 0.0
    for e in entries:
        if e.get("kind") == "settle":
            episodes += int(e.get("episodes", 0))
            wall += float(e.get("wall_s", 0.0))
        elif e.get("kind") == "reserve" and e["run_id"] not in settled:
            episodes += int(e.get("episodes", 0))
    return int(episodes), wall


def budget_remaining(root: Path) -> dict:
    """Episodes/wall remaining under the campaign ceilings; the token
    ceiling is reported for the external accountant (HAR-5: only the LLM
    harness can count tokens — manifests carry the log for the audit)."""
    ceilings = load_campaign_budget(root)
    spent_eps, spent_wall = _ledger_spend(budget_ledger(root))
    return {
        "episodes_left": int(ceilings["episodes"]) - spent_eps,
        "wall_h_left": round(float(ceilings["wall_h"]) - spent_wall / 3600.0, 3),
        "tokens_ceiling": int(ceilings["tokens"]),
    }


class _LedgerLock:
    """O_EXCL lockfile: reserve is check-then-append under one lock, so
    concurrent rollouts cannot both pass a nearly-exhausted ceiling."""

    def __init__(self, root: Path, timeout_s: float = 10.0):
        self.path = root / "runs" / "campaign_ledger.lock"
        self.timeout_s = timeout_s

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"campaign ledger locked: {self.path}") from None
                time.sleep(0.05)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _append_ledger(root: Path, entry: dict) -> str:
    entries = budget_ledger(root)
    prev = entries[-1]["hash"] if entries else ""
    entry["hash"] = _entry_hash(prev, entry)
    path = root / _LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry["hash"]


def reserve_budget(root: Path, run_id: str, episodes: int) -> dict:
    """ADR-21 round 3: atomically CHECK AND RESERVE before launch. Returns
    {"ok": True, "entry": <hash>, "remaining": {...}} or a gate-shaped
    refusal. The reservation charges the episode ceiling immediately; an
    interrupted run therefore never runs off-ledger."""
    try:
        with _LedgerLock(root):
            remaining = budget_remaining(root)
            if remaining["episodes_left"] < episodes:
                return {
                    "ok": False,
                    "gate": "budget",
                    "detail": f"campaign episode budget exhausted: "
                    f"{remaining['episodes_left']} left, {episodes} requested "
                    f"(harness/budget.toml, ADR-21)",
                }
            if remaining["wall_h_left"] <= 0.0:
                return {
                    "ok": False,
                    "gate": "budget",
                    "detail": "campaign wall-clock budget exhausted (harness/budget.toml, ADR-21)",
                }
            entry_hash = _append_ledger(
                root, {"kind": "reserve", "run_id": run_id, "episodes": episodes}
            )
            return {"ok": True, "entry": entry_hash, "remaining": remaining}
    except TimeoutError as stuck:
        return {"ok": False, "gate": "budget", "detail": str(stuck)}


def settle_budget(root: Path, run_id: str, episodes: int, wall_s: float) -> str:
    """The reconciliation entry (written in `finally`): actual episodes and
    wall seconds supersede the reservation in the accounting."""
    with _LedgerLock(root):
        return _append_ledger(
            root,
            {"kind": "settle", "run_id": run_id, "episodes": episodes, "wall_s": round(wall_s, 1)},
        )


def resolve_trusted_baseline(root: Path) -> tuple[str | None, str | None]:
    """(commit OID, error): fetch refs/heads/main FROM THE REMOTE SERVER
    and resolve the fetched head (ADR-21 round 3). The baseline identity
    comes from the branch-protected server at gate time — never from a
    locally movable ref like refs/remotes/origin/main or HEAD — and the
    returned OID is content-addressed: the blobs it names cannot be
    altered without changing it. Fail-closed: no remote, no trusted gate."""
    fetch = subprocess.run(
        ["git", "fetch", "--quiet", "origin", "refs/heads/main"],
        capture_output=True,
        text=True,
        cwd=root,
    )
    if fetch.returncode != 0:
        return None, f"cannot fetch origin main: {fetch.stderr.strip() or 'no remote?'}"
    head = subprocess.run(
        ["git", "rev-parse", "FETCH_HEAD"], capture_output=True, text=True, cwd=root
    )
    if head.returncode != 0:
        return None, "cannot resolve FETCH_HEAD after fetch"
    return head.stdout.strip(), None


def run_gates(
    root: Path,
    graph: Path,
    branch: str,
    no_idea_gate: bool,
    embodiment: str = "franka",
    env_baseline: str = "origin/main",
    episodes: int = 0,
) -> dict:
    """HAR-2: refuse on env-hash mismatch (TRUSTED baseline by default,
    ADR-21: the baseline commit is fetched from the remote SERVER and
    pinned by OID — a research agent cannot satisfy it by moving local
    refs or passing its own; --env-baseline local is the dev override —
    humans only; logged), on validation failure, on a missing OPEN idea
    (unless --no-idea-gate — humans only; logged), and — for trusted runs
    only — on an exhausted campaign budget (episodes/wall reserved
    atomically by rollout(); tokens are audited externally per HAR-5).
    Local-override runs are exempt from budget refusal and never charge
    the ledger: the budget meters the campaign, not development."""
    if env_baseline not in ("origin/main", "local"):
        return {
            "ok": False,
            "gate": "env_hash",
            "detail": f"unknown baseline {env_baseline!r}: only the protected "
            "'origin/main' (server-resolved) or the logged dev override 'local' "
            "are accepted (ADR-21)",
        }
    baseline_oid = None
    hash_cmd = [sys.executable, str(root / "tools" / "env_hash.py"), "--check", "--root", str(root)]
    # ADR-24: rollouts need the sim extra — declare the selection so the
    # trusted checker attests THIS environment shape (HAR-2)
    hash_cmd += ["--extras", "sim"]
    if env_baseline != "local":
        baseline_oid, err = resolve_trusted_baseline(root)
        if err:
            return {"ok": False, "gate": "env_hash", "detail": err}
        hash_cmd += ["--baseline", baseline_oid]
    hash_proc = subprocess.run(hash_cmd, capture_output=True, text=True)
    if hash_proc.returncode != 0:
        return {"ok": False, "gate": "env_hash", "detail": hash_proc.stdout.strip()}
    hash_report = json.loads(hash_proc.stdout)
    env_hash = hash_report["env_hash"]
    dist = hash_report.get("dist")
    if env_baseline != "local":
        # ADR-24 D2/D3: trusted runs REFUSE on missing or failed
        # attestation — record-by-convention is not a gate
        if not isinstance(dist, dict):
            return {
                "ok": False,
                "gate": "dist",
                "detail": "DIST_DRIFT: no attestation evidence from the trusted "
                "checker (stale env_hash.py at the baseline?)",
            }
        if not dist.get("attested"):
            return {
                "ok": False,
                "gate": "dist",
                "detail": "DIST_DRIFT: " + "; ".join(dist.get("problems") or ["unattested"]),
            }
    remaining = budget_remaining(root)
    if env_baseline != "local":
        if episodes > 0 and remaining["episodes_left"] < episodes:
            return {
                "ok": False,
                "gate": "budget",
                "detail": f"campaign episode budget exhausted: {remaining['episodes_left']} left, "
                f"{episodes} requested (harness/budget.toml, ADR-21)",
            }
        if remaining["wall_h_left"] <= 0.0:
            return {
                "ok": False,
                "gate": "budget",
                "detail": "campaign wall-clock budget exhausted (harness/budget.toml, ADR-21)",
            }
    # validate against the embodiment that will actually run (M0-5): a
    # graph whose nodes do not support it must refuse HERE, not crash
    # hours into the rollout
    validation = validate(graph, root, embodiment, allow_unproven=False)
    if not validation["ok"]:
        return {"ok": False, "gate": "validate", "detail": validation["errors"]}
    gates = {
        "env_hash": env_hash,
        "env_baseline": env_baseline,
        # the resolved immutable identity (ADR-21 round 3): the audit
        # re-verifies blobs at this OID, not at whatever a ref says later
        "env_baseline_oid": baseline_oid,
        # ADR-24: CON-5's fifth component + the attestation verdict (facts
        # from the trusted checker; local runs record honestly, trusted
        # runs were already refused above on failure)
        "env_fingerprint": (dist or {}).get("env_fingerprint"),
        "env_attested": bool((dist or {}).get("attested")),
        "dist_problems": (dist or {}).get("problems") or [],
        "budget": remaining,
    }
    if no_idea_gate:
        return {"ok": True, **gates, "idea": None, "no_idea_gate": True}
    ideas = open_ideas(root, branch)
    if not ideas:
        return {
            "ok": False,
            "gate": "idea",
            "detail": f"no OPEN idea for branch {branch!r} (HAR-8); "
            "log one with `harness report log --idea ...`",
        }
    return {"ok": True, **gates, "idea": ideas[-1]["id"], "no_idea_gate": False}


def instrumented_graph(
    graph: Path, root: Path, run_dir: Path, trace_dir: Path | None = None, name: str = "graph.yaml"
) -> Path:
    """The input graph plus a trace-recorder node (HAR-4) with absolutized
    node paths, written under the run dir (dora's cwd becomes the run dir,
    which also scopes orphan cleanup). trace_dir/name vary per wall-clamp
    relaunch (ADR-23): the recorder opens its Arrow/video files in write
    mode, so a relaunch pointed at the SAME dir would truncate the prior
    launch's evidence (PR #58 review)."""
    doc = yaml.safe_load(graph.read_text())
    for node in doc["nodes"]:
        node["path"] = str((graph.parent / node["path"]).resolve())
    # HAR-4: EVERY declared endpoint, keyed <producer>__<topic> so two
    # producers of the same topic name (e.g. reset_done from both the
    # bridge and the reset service) stay distinct endpoints
    inputs = {
        f"{node['id']}__{topic}": {"source": f"{node['id']}/{topic}", "queue_size": 100}
        for node in doc["nodes"]
        for topic in (node.get("outputs") or [])
    }
    doc["nodes"].append(
        {
            "id": "trace-recorder",
            # .resolve(): a relative root (`--root .`) with dora cwd = the
            # run dir otherwise kills the dataflow at startup, zero episodes
            "path": str((root / "src" / "aisle" / "harness" / "trace_recorder.py").resolve()),
            "inputs": inputs,
            "env": {"AISLE_TRACE_DIR": str(trace_dir if trace_dir else run_dir / "traces")},
        }
    )
    out_path = run_dir / name
    out_path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return out_path


def _spawn_dora(exec_graph: Path, run_dir: Path, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        ["dora", "run", str(exec_graph), "--uv"],
        # cwd = the run dir: dora spawns nodes with this cwd, which is what
        # the orphan reaper filters on — with cwd=root the filter matched
        # nothing and leaked nodes raced the cleanup (T09 smoke)
        cwd=run_dir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


def _terminate(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=20)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _graph_hash(graph: Path) -> str:
    return hashlib.sha256(graph.read_bytes()).hexdigest()


def rollout(
    root: Path,
    graph: Path,
    tier: str,
    episodes: int,
    seeds: list[int],
    reset_mode: str,
    verifier: str,
    run_id: str,
    branch: str,
    no_idea_gate: bool,
    timeout_s: float | None = None,
    embodiment: str = "franka",
    env_baseline: str = "origin/main",
) -> dict:
    """HAR-1: the full run. Returns the report dict (CON-8: caller emits)."""
    # A relative root (`--root .`) must be pinned to THIS process's cwd:
    # dora runs with cwd = the run dir, so relative AISLE_RESULTS /
    # AISLE_TRACE_DIR strings would resolve to a nested runs/<id>/runs/<id>/
    # tree the stall watcher never sees (T18 live shakeout)
    root = root.resolve()
    if reset_mode != "teleport":
        return {"ok": False, "error": "behavioral reset is Phase 2 (RST-2)"}
    if verifier != "oracle":
        return {"ok": False, "error": "realistic verifier is Phase 2"}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
        return {"ok": False, "error": f"unsafe run_id {run_id!r}"}
    if (root / "runs" / run_id).exists():
        return {"ok": False, "error": f"run_id {run_id!r} already exists; refusing to overwrite"}
    gates = run_gates(root, graph, branch, no_idea_gate, embodiment, env_baseline, episodes)
    if not gates["ok"]:
        return {"ok": False, "refused": gates}
    # ADR-21 round 3: RESERVE atomically before launch (trusted runs only;
    # the ledger meters the campaign, not development) — concurrent
    # rollouts contend under the ledger lock and an interrupted run stays
    # charged at its reservation until settled
    reservation = None
    if env_baseline != "local":
        reservation = reserve_budget(root, run_id, episodes)
        if not reservation["ok"]:
            return {"ok": False, "refused": reservation}

    seeds = (seeds * ((episodes + len(seeds) - 1) // len(seeds)))[:episodes]
    run_dir = root / "runs" / run_id
    traces_dir = run_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    exec_graph = instrumented_graph(graph, root, run_dir)
    results_path = run_dir / "episodes.jsonl"

    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=root
    ).stdout.strip()
    env_hash = gates["env_hash"]

    episode_timeout_s, per_episode_budget_s = tier_budgets(tier)
    env = {
        **os.environ,
        "AISLE_SEEDS": ",".join(str(s) for s in seeds),
        # the caller-selected tier propagates to the graph (HAR-1): the
        # rollout client stamps it into every goal, and the SELECTED graph
        # determines its tier-specific wiring
        "AISLE_TIER": tier,
        # M0-5: the embodiment profile swap rides on env, zero YAML edits
        "AISLE_EMBODIMENT": embodiment,
        "AISLE_TIMEOUT_S": str(episode_timeout_s),
        "AISLE_RESULTS": str(results_path),
    }
    started = time.monotonic()
    run_budget_s = timeout_s or (GENESIS_BUILD_BUDGET_S + per_episode_budget_s * episodes)
    if env_baseline != "local":
        # ADR-21 round 3: the run is CAPPED to the campaign's remaining
        # wall budget — a single long rollout cannot blow through the
        # ceiling it passed at the gate
        run_budget_s = min(run_budget_s, gates["budget"]["wall_h_left"] * 3600.0)
    # the campaign wall cap also bounds relaunch deadline extensions
    hard_cap_s = gates["budget"]["wall_h_left"] * 3600.0 if env_baseline != "local" else None
    deadline = started + run_budget_s
    proc = _spawn_dora(exec_graph, run_dir, env)
    episode_records: list[dict] = []
    stalled = False
    clamped_seeds: list[int] = []
    relaunches = 0
    last_size = -1
    last_growth = time.monotonic()
    current_traces = traces_dir
    lines_at_launch = 0
    last_lines = 0
    last_line_t = time.monotonic()
    try:
        while time.monotonic() < deadline:
            lines = results_path.read_bytes().count(b"\n") if results_path.exists() else 0
            if lines >= episodes:
                break
            if lines != last_lines:
                last_lines = lines
                last_line_t = time.monotonic()
            if proc.poll() is not None:
                break
            # liveness: a dead bridge leaves `dora run` alive but the trace
            # stream frozen (a NaN crash burned 17 idle minutes in the T09
            # diag run) — bail once traces stop growing. Before the FIRST
            # data the genesis build is running (minutes, no traces yet),
            # so the pre-data grace is much longer (an early fire killed
            # the building bridge at 180 s)
            # the stall signal watches the CURRENT launch's dir only:
            # keyed on the whole tree, a relaunch build inherits the prior
            # launch's nonzero total and gets the short post-data grace —
            # falsely stall-killed mid-build (PR #58 self-review)
            size = sum(f.stat().st_size for f in current_traces.glob("*") if f.is_file())
            if size != last_size:
                last_size = size
                last_growth = time.monotonic()
            elif time.monotonic() - last_growth > (PRE_DATA_STALL_S if last_size <= 0 else STALL_S):
                stalled = True
                break
            # per-episode WALL clamp (ADR-23, W/S2 holdout wedge): traces
            # kept GROWING while one episode ran 4 h, so the stall detector
            # above never fired and the wedged episode ate the whole run
            # window, masking every remaining seed. An episode gets the
            # tier's per-episode wall budget (+ build grace when it is the
            # first of a launch); past that it is killed, recorded as a
            # synthetic wall_clamp failure, and the run RELAUNCHES with the
            # remaining seeds so they still get scored.
            grace = per_episode_budget_s + (
                GENESIS_BUILD_BUDGET_S if lines == lines_at_launch else 0
            )
            if time.monotonic() - last_line_t > grace:
                _terminate(proc)
                # stale nodes from the killed launch are CONCURRENT WRITERS
                # to results/traces (dora-rs/dora#2856) — reap before any
                # relaunch, not only in the finally (PR #58 review)
                reap_orphans(run_dir)
                seed = seeds[lines] if lines < len(seeds) else None
                with open(results_path, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "episode": lines,
                                "seed": seed,
                                "status": "fail",
                                "failure": "wall_clamp",
                                "success": False,
                                "synthetic": True,
                            }
                        )
                        + "\n"
                    )
                if seed is not None:
                    clamped_seeds.append(seed)
                remaining = seeds[lines + 1 :]
                if not remaining:
                    break
                relaunches += 1
                env = {**env, "AISLE_SEEDS": ",".join(str(s) for s in remaining)}
                # fresh trace dir + graph per launch: the recorder truncates
                # on open, and prior evidence must survive (HAR-4)
                relaunch_traces = traces_dir / f"relaunch-{relaunches}"
                relaunch_traces.mkdir(parents=True, exist_ok=True)
                current_traces = relaunch_traces
                exec_graph = instrumented_graph(
                    graph,
                    root,
                    run_dir,
                    trace_dir=relaunch_traces,
                    name=f"graph-r{relaunches}.yaml",
                )
                # each relaunch pays a fresh build: extend the deadline by
                # the build grace (still bounded by the campaign wall cap),
                # else consecutive wedges cut the tail seeds (PR #58 review)
                deadline += GENESIS_BUILD_BUDGET_S
                if hard_cap_s is not None:
                    deadline = min(deadline, started + hard_cap_s)
                proc = _spawn_dora(exec_graph, run_dir, env)
                lines_at_launch = last_lines = lines + 1
                last_line_t = time.monotonic()
                last_size = -1
                last_growth = time.monotonic()
                continue
            time.sleep(2.0)
    finally:
        # ADR-21 round 3: reconcile the reservation with actuals no matter
        # how the run ended — crash paths settle too
        if reservation is not None:
            settle_budget(root, run_id, len(episode_records), time.monotonic() - started)
        _terminate(proc)
        reap_orphans(run_dir)

    if results_path.exists():
        episode_records = [
            json.loads(line) for line in results_path.read_text().splitlines() if line.strip()
        ]
    wall_s = time.monotonic() - started
    metrics = compute_metrics(episode_records)
    videos = sorted(str(p.relative_to(root)) for p in traces_dir.rglob("*.mp4"))
    manifest = {
        "run_id": run_id,
        "git_sha": git_sha,
        "env_hash": env_hash,
        "platform": platform_module.platform(),
        "graph": str(graph),
        "graph_hash": _graph_hash(graph),
        "tier": tier,
        "embodiment": embodiment,
        "seeds": seeds,
        "reset": reset_mode,
        "verifier": verifier,
        "idea": gates.get("idea"),
        "no_idea_gate": gates.get("no_idea_gate", False),
        # ADR-21: which frozen-set baseline validated this run — "local" is
        # the logged dev override, auditable in every manifest — and the
        # RESOLVED immutable commit OID plus this run's ledger reservation
        # hash, so the audit can re-verify both
        "env_baseline": gates.get("env_baseline"),
        "env_baseline_oid": gates.get("env_baseline_oid"),
        # ADR-24 (HAR-4): the CON-5 fifth component + attestation verdict
        "env_fingerprint": gates.get("env_fingerprint"),
        "env_attested": gates.get("env_attested"),
        "dist_problems": gates.get("dist_problems"),
        "budget_reservation": (reservation or {}).get("entry"),
        # HAR-5: best-effort token accounting
        "tokens_log": os.environ.get("ANTHROPIC_TOKENS_LOG"),
        # ADR-23: per-episode wall clamp — which seeds were cut and how
        # many relaunches carried the remaining seeds
        "wall_clamped": clamped_seeds,
        "relaunches": relaunches,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return {
        "ok": len(episode_records) >= episodes,
        "stalled": stalled,
        "run_id": run_id,
        **metrics,
        "episodes": episode_records,
        "traces_dir": str(traces_dir.relative_to(root)),
        "videos": videos,
        "durations": {
            "wall_s": round(wall_s, 1),
            "sim_s": round(sum(e.get("t_end", 0.0) for e in episode_records), 1),
        },
        "budget": budget_remaining(root),
    }
