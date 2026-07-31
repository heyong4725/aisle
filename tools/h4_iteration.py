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
import json
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
    """One daemon-mode dataflow and its poll-derived episode timeline."""

    def __init__(self, graph: Path, results: Path, name: str) -> None:
        self.results = results
        self.name = name
        self.poll: list[tuple[float, int]] = []
        results.write_text("")
        self.t0 = time.time()
        started = sh(["dora", "start", str(graph), "--uv", "--name", name, "--detach"])
        if started.returncode != 0:
            raise RuntimeError(f"dora start failed: {(started.stderr or '')[-300:]}")

    def sample(self) -> list[float]:
        count = len(self.results.read_text().splitlines()) if self.results.exists() else 0
        self.poll.append((time.time(), count))
        return timeline_from_polls(self.poll)

    def wait(self, condition, timeout_s: float) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if condition(self.sample()):
                return True
            time.sleep(0.5)
        return False

    def stop(self) -> None:
        sh(["dora", "stop", "--name", self.name, "--grace-duration", "5s"])
        time.sleep(2)
        subprocess.run(["pkill", "-f", "src/aisle/"], capture_output=True)
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


def run_experiment(out_dir: Path, reps: int) -> dict:
    import yaml

    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "episodes.jsonl"
    graph, variant = build_graph(out_dir, 30, results)
    record_path = out_dir / "h4_latency.jsonl"
    records: list[dict] = []
    order = [p for _ in range(reps) for p in ("relaunch", "hotswap")]
    nonce = int(time.time()) % 100000  # unique dataflow names across invocations
    stream_index = 0
    stream = Stream(graph, results, f"h4-{nonce}-{stream_index}")
    try:
        for rep, path_name in enumerate(order):
            if not stream.wait(lambda t: len(t) >= 1, 600):
                raise RuntimeError(f"rep {rep}: stream produced no episode in 600s")
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
            }
            rec["latency_s"] = latency_from_record(rec)
            records.append(rec)
            with open(record_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"[h4] rep {rep} {path_name}: {rec['latency_s']}s", file=sys.stderr)
    finally:
        stream.stop()
    return analyze(records)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs" / "h4")
    parser.add_argument("--reps", type=int, default=6, help="repetitions PER PATH")
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
        table = run_experiment(args.out.resolve(), args.reps)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, "table": table}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
