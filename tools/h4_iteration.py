"""H4 iteration-latency measurement (design doc §8.3 item 5, §6 H4;
SPEC 070 HAR-12; ADR-h4-iteration-protocol).

Pure metric logic lives at module level (CON-12/CON-5: unit-testable,
no sim, no clocks of its own); the live runner drives dora daemon mode
and records raw evidence JSONL that `--analyze` recomputes from — the
published table is derived from the record, never hand-written (CON-8:
JSON to stdout, logs to stderr, exit 0 iff ok).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as platform_mod
import random
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_ID = "grasp-planner-topdown"
PATHS = ("relaunch", "hotswap")


# ---------------------------------------------------------------- metric


def episode_starts(timeline: list[float], stream_t0: float) -> list[float]:
    """Episode i's start time: the previous episode's result ts (episode
    0 starts at the stream's start). Teleport reset at T0 is sub-second,
    so result(i-1) ~= start(i) well under the effect size (ADR-h4 §4)."""
    if not timeline:
        return []
    return [stream_t0] + list(timeline[:-1])


def credited_episode(
    timeline: list[float], stream_t0: float, change_ok_ts: float
) -> tuple[int, float] | None:
    """The FIRST episode that STARTED at or after the change completed
    (HAR-12 as bounded by ADR-h4 §3: a straddling episode is never
    credited — the change can kill or corrupt it). Returns
    (episode_index, result_ts), or None if no post-change episode has
    completed yet."""
    starts = episode_starts(timeline, stream_t0)
    for index, (start, result) in enumerate(zip(starts, timeline, strict=True)):
        if start >= change_ok_ts:
            return index, result
    return None


def latency_from_record(rec: dict) -> float | None:
    """Recompute one repetition's latency from its raw evidence:
    t_idea -> credited episode's result ts. None iff the record shows
    no creditable episode (an infra failure, reported, never guessed)."""
    credited = credited_episode(
        list(rec["timeline"]), float(rec["stream_t0"]), float(rec["change_ok_ts"])
    )
    if credited is None:
        return None
    return credited[1] - float(rec["t_idea"])


def stale_node_pids(node_entries: list[dict], ps_lines: list[str], repo_root: str) -> list[int]:
    """PIDs safe to reap after `dora stop`: they were THIS dataflow's own
    nodes (from `dora node list -d <name>`) AND their current command
    line still references this checkout. NEVER a global pattern kill
    (PR #79 review P1: a global pattern kill matches every AISLE
    experiment on the host and can corrupt concurrent campaigns)."""
    pids: list[int] = []
    for entry in node_entries:
        try:
            pid = int(str(entry.get("pid", "")).strip())
        except ValueError:
            continue
        for line in ps_lines:
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0] == str(pid) and repo_root in parts[1]:
                pids.append(pid)
                break
    return pids


def timeline_from_polls(poll: list) -> list[float]:
    """result ts of episode i = the first poll ts at which count > i."""
    out: list[float] = []
    for ts, count in poll:
        while len(out) < count:
            out.append(ts)
    return out


def analyze(records: list[dict]) -> dict:
    """The per-path latency table, recomputed from raw records only."""
    table: dict = {}
    for path_name in PATHS:
        latencies = []
        failed = 0
        for rec in records:
            if rec.get("path") != path_name:
                continue
            latency = latency_from_record(rec)
            if latency is None:
                failed += 1
            else:
                latencies.append(round(latency, 1))
        latencies.sort()
        n = len(latencies)
        median = None
        if n:
            median = latencies[n // 2] if n % 2 else (latencies[n // 2 - 1] + latencies[n // 2]) / 2
        table[path_name] = {
            "n": n,
            "failed": failed,
            "latencies_s": latencies,
            "median_s": None if median is None else round(median, 1),
            "min_s": latencies[0] if latencies else None,
            "max_s": latencies[-1] if latencies else None,
        }
    r, h = table["relaunch"]["median_s"], table["hotswap"]["median_s"]
    table["median_ratio_relaunch_over_hotswap"] = (
        round(r / h, 2) if r is not None and h not in (None, 0) else None
    )
    return table


# ---------------------------------------------------------------- runner


def sh(cmd: list[str]) -> subprocess.CompletedProcess:
    print("+", " ".join(map(str, cmd)), file=sys.stderr)
    return subprocess.run(cmd, capture_output=True, text=True)


class Stream:
    """One daemon-mode dataflow and its poll-derived episode timeline.
    A BACKGROUND sampler thread polls the results stream at 4 Hz for the
    stream's whole lifetime — synchronous polling left gaps during phase
    delays and CLI calls, collapsing every episode completed in a gap
    onto one timestamp and mis-crediting pre-change episodes (a 3.4 s
    "latency" artifact caught in the PR #79 rework)."""

    def __init__(self, graph: Path, results: Path, name: str) -> None:
        import threading

        self.results = results
        self.name = name
        self.poll: list[tuple[float, int]] = []
        results.write_text("")
        self.t0 = time.time()
        started = sh(["dora", "start", str(graph), "--uv", "--name", name, "--detach"])
        if started.returncode != 0:
            raise RuntimeError(f"dora start failed: {(started.stderr or '')[-300:]}")
        self._stop_sampling = threading.Event()

        def _sampler() -> None:
            while not self._stop_sampling.is_set():
                self._sample_once()
                self._stop_sampling.wait(0.25)

        self._thread = threading.Thread(target=_sampler, daemon=True)
        self._thread.start()

    def _sample_once(self) -> None:
        count = len(self.results.read_text().splitlines()) if self.results.exists() else 0
        self.poll.append((time.time(), count))

    def sample(self) -> list[float]:
        return timeline_from_polls(list(self.poll))

    def wait(self, condition, timeout_s: float) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if condition(self.sample()):
                return True
            time.sleep(0.5)
        return False

    def node_entries(self) -> list[dict]:
        proc = sh(["dora", "node", "list", "-d", self.name, "--format", "json"])
        entries = []
        for line in (proc.stdout or "").splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return entries

    def stop(self) -> None:
        # snapshot THIS dataflow's node pids before stop, then reap only
        # those whose live cmdline still references this checkout
        # (PR #79 review P1: no global pattern kill — concurrent AISLE
        # experiments on the host must be untouchable)
        self._stop_sampling.set()
        entries = self.node_entries()
        sh(["dora", "stop", "--name", self.name, "--grace-duration", "5s"])
        time.sleep(2)
        ps = subprocess.run(["ps", "ax", "-o", "pid=,command="], capture_output=True, text=True)
        for pid in stale_node_pids(entries, (ps.stdout or "").splitlines(), str(REPO_ROOT)):
            try:
                os.kill(pid, 9)
                print(f"[h4] reaped stale node pid {pid}", file=sys.stderr)
            except ProcessLookupError:
                pass
        time.sleep(1)


def log_idea(text: str) -> float:
    t = time.time()
    sh(["uv", "run", "harness", "report", "log", "--idea", text, "--expect", "latency sample"])
    return t


def build_graph(work: Path, seeds: int, results: Path) -> tuple[Path, Path]:
    import yaml

    doc = yaml.safe_load((REPO_ROOT / "graphs" / "expert_t0.yaml").read_text())
    for node in doc["nodes"]:
        node["path"] = str((REPO_ROOT / "graphs" / node["path"]).resolve())
        if node["id"] == "rollout-client":
            node.setdefault("env", {}).update(
                {
                    "AISLE_SEEDS": ",".join(str(s) for s in range(seeds)),
                    "AISLE_RESULTS": str(results),
                }
            )
    graph = work / "h4_graph.yaml"
    graph.write_text(yaml.safe_dump(doc, sort_keys=False))
    variant = work / "variant.yaml"
    variant.write_text(yaml.safe_dump(next(n for n in doc["nodes"] if n["id"] == NODE_ID)))
    return graph, variant


def batch_manifest(out_dir: Path, graph: Path, seed: int, order: list[str]) -> dict:
    """CON-5 provenance for the whole batch (PR #79 review P1): the full
    tuple recorded through the ADR-24 facts path, honestly labelled — a
    dev measurement with a local baseline and no post-run audit makes NO
    reproducibility claim (CON-5 as amended)."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import env_hash as envh

    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True
        ).stdout.strip()
    )
    env_hash_val, _ = envh.compute_env_hash(REPO_ROOT)
    att = envh.dist_attestation(REPO_ROOT, None, ["sim"])
    dora_version = subprocess.run(["dora", "--version"], capture_output=True, text=True)
    manifest = {
        "git_sha": git_sha,
        # PR #79 re-review P1: a sha only identifies the code if the
        # tree was CLEAN — a dirty tree is recorded and disqualifies
        # the batch from citing the sha as its code identity
        "git_dirty": dirty,
        "platform": platform_mod.platform(),
        "env_hash": env_hash_val,
        "env_fingerprint": att.get("fingerprint"),
        "env_attested": False,
        "dist_problems": att.get("problems"),
        "attestation_note": (
            "dev measurement: local baseline, no post-run audit — per CON-5 "
            "as amended (ADR-24) this evidence makes NO reproducibility claim"
        ),
        "graph_sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
        "seeds": "0..29 (episode stream)",
        "order_seed": seed,
        "order": order,
        "phase_model": "idea arrival delayed uniform(0, 25.0)s after stream health check",
        "dora_cli": (dora_version.stdout or "").strip(),
        "dora_api_rev": "7eb4a5f8b",
        "coordinator_port": os.environ.get("DORA_COORDINATOR_PORT", "6013 (default)"),
    }
    (out_dir / f"manifest-seed{seed}.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def run_experiment(out_dir: Path, reps: int, seed: int = 0) -> dict:
    import yaml

    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "episodes.jsonl"
    graph, variant = build_graph(out_dir, 30, results)
    record_path = out_dir / "h4_latency.jsonl"
    records: list[dict] = []
    # PR #79 review P1 (phase lock): a fixed R,H order with ideas logged
    # the instant a result lands pins every hot-swap idea to the worst
    # arrival phase (the next episode is ALWAYS a straddler). Randomize
    # BOTH the path order and the idea-arrival phase, seeded (CON-5).
    rng = random.Random(seed)
    order = ["relaunch"] * reps + ["hotswap"] * reps
    rng.shuffle(order)
    batch_manifest(out_dir, graph, seed, order)
    nonce = int(time.time()) % 100000  # unique dataflow names across invocations
    stream_index = 0
    stream = Stream(graph, results, f"h4-{nonce}-{stream_index}")
    try:
        for rep, path_name in enumerate(order):
            if not stream.wait(lambda t: len(t) >= 1, 600):
                raise RuntimeError(f"rep {rep}: stream produced no episode in 600s")
            phase_delay = rng.uniform(0.0, 25.0)
            time.sleep(phase_delay)
            t_idea = log_idea(f"H4 rep {rep} ({path_name}): null-variant {NODE_ID}")
            if path_name == "hotswap":
                swap = sh(
                    [
                        "uv",
                        "run",
                        "harness",
                        "swap",
                        "--graph",
                        str(graph),
                        "--dataflow",
                        stream.name,
                        "--replace",
                        NODE_ID,
                        "--with",
                        str(variant),
                        "--embodiment",
                        "franka",
                    ]
                )
                report = json.loads(swap.stdout or "{}")
                if not report.get("ok"):
                    raise RuntimeError(f"rep {rep}: swap failed: {report}")
                change_ok = time.time()
                stream_t0 = stream.t0
                ok = stream.wait(
                    lambda t, t0=stream_t0, c=change_ok: credited_episode(t, t0, c) is not None,
                    600,
                )
            else:
                stream.stop()
                stream_index += 1
                # the graph "edit": the null-variant written through the
                # same document path the swap uses (ADR-h4 §2)
                doc = yaml.safe_load(graph.read_text())
                idx = next(i for i, n in enumerate(doc["nodes"]) if n["id"] == NODE_ID)
                doc["nodes"][idx] = yaml.safe_load(variant.read_text())
                graph.write_text(yaml.safe_dump(doc, sort_keys=False))
                check = sh(
                    ["uv", "run", "harness", "validate", str(graph), "--embodiment", "franka"]
                )
                if '"ok": true' not in (check.stdout or ""):
                    raise RuntimeError(f"rep {rep}: validate failed: {(check.stdout or '')[-300:]}")
                change_ok = time.time()
                stream = Stream(graph, results, f"h4-{nonce}-{stream_index}")
                stream_t0 = stream.t0
                ok = stream.wait(
                    lambda t, t0=stream_t0, c=change_ok: credited_episode(t, t0, c) is not None,
                    600,
                )
            rec = {
                "rep": rep,
                "path": path_name,
                "stream": stream.name,
                "stream_t0": stream_t0,
                "t_idea": t_idea,
                "change_ok_ts": change_ok,
                "timeline": stream.sample(),
                "timed_out": not ok,
                "phase_delay_s": round(phase_delay, 3),
            }
            rec["latency_s"] = latency_from_record(rec)
            records.append(rec)
            with open(record_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"[h4] rep {rep} {path_name}: {rec['latency_s']}s", file=sys.stderr)
    finally:
        stream.stop()
        # HAR-7/HAR-12 evidence rides WITH the batch (PR #79 re-review
        # P1: the idea/swap logs were forgotten at evidence-assembly
        # time) — copy the branch's append-only logs into the out dir
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=REPO_ROOT, capture_output=True, text=True
        ).stdout.strip()
        safe = branch.replace("/", "__")
        for kind in ("ideas", "swaps"):
            src = REPO_ROOT / "runs" / kind / f"{safe}.jsonl"
            if src.exists():
                (out_dir / f"{kind}.jsonl").write_text(src.read_text())
    return analyze(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs" / "h4")
    parser.add_argument("--reps", type=int, default=6, help="repetitions PER PATH")
    parser.add_argument("--seed", type=int, default=0, help="order/phase RNG seed (CON-5)")
    parser.add_argument("--analyze", type=Path, default=None, help="recompute from a record")
    args = parser.parse_args()
    try:
        if args.analyze:
            records = [
                json.loads(line) for line in args.analyze.read_text().splitlines() if line.strip()
            ]
            print(json.dumps({"ok": True, "table": analyze(records)}, indent=1))
            return 0
        # resolve: AISLE_RESULTS lands in node env, and nodes run with
        # the GRAPH dir as cwd — a relative --out silently nests
        table = run_experiment(args.out.resolve(), args.reps, args.seed)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, "table": table}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
