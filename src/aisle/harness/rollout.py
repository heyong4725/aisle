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


def run_gates(root: Path, graph: Path, branch: str, no_idea_gate: bool) -> dict:
    """HAR-2: refuse on env-hash mismatch, on validation failure, and on a
    missing OPEN idea (unless --no-idea-gate — humans only; logged)."""
    hash_proc = subprocess.run(
        [sys.executable, str(root / "tools" / "env_hash.py"), "--check", "--root", str(root)],
        capture_output=True,
        text=True,
    )
    if hash_proc.returncode != 0:
        return {"ok": False, "gate": "env_hash", "detail": hash_proc.stdout.strip()}
    env_hash = json.loads(hash_proc.stdout)["env_hash"]
    validation = validate(graph, root, "franka", allow_unproven=False)
    if not validation["ok"]:
        return {"ok": False, "gate": "validate", "detail": validation["errors"]}
    if no_idea_gate:
        return {"ok": True, "env_hash": env_hash, "idea": None, "no_idea_gate": True}
    ideas = open_ideas(root, branch)
    if not ideas:
        return {
            "ok": False,
            "gate": "idea",
            "detail": f"no OPEN idea for branch {branch!r} (HAR-8); "
            "log one with `harness report log --idea ...`",
        }
    return {"ok": True, "env_hash": env_hash, "idea": ideas[-1]["id"], "no_idea_gate": False}


def instrumented_graph(graph: Path, root: Path, run_dir: Path) -> Path:
    """The input graph plus a trace-recorder node (HAR-4) with absolutized
    node paths, written under the run dir (dora's cwd becomes the run dir,
    which also scopes orphan cleanup)."""
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
            "path": str(root / "src" / "aisle" / "harness" / "trace_recorder.py"),
            "inputs": inputs,
            "env": {"AISLE_TRACE_DIR": str(run_dir / "traces")},
        }
    )
    out_path = run_dir / "graph.yaml"
    out_path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return out_path


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
) -> dict:
    """HAR-1: the full run. Returns the report dict (CON-8: caller emits)."""
    if reset_mode != "teleport":
        return {"ok": False, "error": "behavioral reset is Phase 2 (RST-2)"}
    if verifier != "oracle":
        return {"ok": False, "error": "realistic verifier is Phase 2"}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id):
        return {"ok": False, "error": f"unsafe run_id {run_id!r}"}
    if (root / "runs" / run_id).exists():
        return {"ok": False, "error": f"run_id {run_id!r} already exists; refusing to overwrite"}
    gates = run_gates(root, graph, branch, no_idea_gate)
    if not gates["ok"]:
        return {"ok": False, "refused": gates}

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

    env = {
        **os.environ,
        "AISLE_SEEDS": ",".join(str(s) for s in seeds),
        # the caller-selected tier propagates to the graph (HAR-1): the
        # rollout client stamps it into every goal, and the SELECTED graph
        # determines its tier-specific wiring
        "AISLE_TIER": tier,
        "AISLE_TIMEOUT_S": str(EPISODE_TIMEOUT_S),
        "AISLE_RESULTS": str(results_path),
    }
    started = time.monotonic()
    deadline = started + (timeout_s or (GENESIS_BUILD_BUDGET_S + PER_EPISODE_BUDGET_S * episodes))
    proc = subprocess.Popen(
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
    episode_records: list[dict] = []
    stalled = False
    last_size = -1
    last_growth = time.monotonic()
    try:
        while time.monotonic() < deadline:
            if results_path.exists() and results_path.read_bytes().count(b"\n") >= episodes:
                break
            if proc.poll() is not None:
                break
            # liveness: a dead bridge leaves `dora run` alive but the trace
            # stream frozen (a NaN crash burned 17 idle minutes in the T09
            # diag run) — bail once traces stop growing. Before the FIRST
            # data the genesis build is running (minutes, no traces yet),
            # so the pre-data grace is much longer (an early fire killed
            # the building bridge at 180 s)
            size = sum(f.stat().st_size for f in traces_dir.glob("*") if f.is_file())
            if size != last_size:
                last_size = size
                last_growth = time.monotonic()
            elif time.monotonic() - last_growth > (PRE_DATA_STALL_S if last_size <= 0 else STALL_S):
                stalled = True
                break
            time.sleep(2.0)
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=20)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        reap_orphans(run_dir)

    if results_path.exists():
        episode_records = [
            json.loads(line) for line in results_path.read_text().splitlines() if line.strip()
        ]
    wall_s = time.monotonic() - started
    metrics = compute_metrics(episode_records)
    videos = sorted(str(p.relative_to(root)) for p in traces_dir.glob("*.mp4"))
    manifest = {
        "run_id": run_id,
        "git_sha": git_sha,
        "env_hash": env_hash,
        "platform": platform_module.platform(),
        "graph": str(graph),
        "graph_hash": _graph_hash(graph),
        "tier": tier,
        "seeds": seeds,
        "reset": reset_mode,
        "verifier": verifier,
        "idea": gates.get("idea"),
        "no_idea_gate": gates.get("no_idea_gate", False),
        # HAR-5: best-effort token accounting
        "tokens_log": os.environ.get("ANTHROPIC_TOKENS_LOG"),
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
    }
