"""M3: neural-env ranking agreement (ADR-m3-protocol; next-phases §5.3).

`--build-population` copies the launchable H1 first-graphs + recorded
Genesis pass@1 into analysis/m3/population/ (committed provenance).
`--run` swaps each graph's environment node for world-model-env v0 and
runs seeds 0..7 free-run. `--analyze` recomputes Spearman rho (average
ranks) + top-half screening agreement from the records — the table is
derived, never hand-written. CON-8: JSON stdout, logs stderr.
UNATTESTED free-run dev measurement (the ADR's declared scope)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
H1_RUNS = Path("/Users/yonghe/src/aisle/runs/h1")
POP_DIR = REPO_ROOT / "analysis" / "m3" / "population"
SEEDS = "0,1,2,3,4,5,6,7"  # the H1 protocol (ROLLOUT_EPISODES=8)
RUN_TIMEOUT_S = 600.0


# ---------------------------------------------------------------- metrics


def _avg_ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    """Average-rank Spearman via Pearson over ranks (tie-correct)."""
    if len(a) != len(b) or len(a) < 2:
        return None
    ra, rb = _avg_ranks(a), _avg_ranks(b)
    ma = sum(ra) / len(ra)
    mb = sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    if da == 0 or db == 0:
        return None
    return round(num / (da * db), 4)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def screening_agreement(genesis: list[float], surrogate: list[float]) -> float | None:
    """Fraction placed on the same side of the population median (ties on
    the median count as agreement iff both sit on it)."""
    if len(genesis) != len(surrogate) or not genesis:
        return None
    mg, ms = _median(genesis), _median(surrogate)
    same = sum(
        1
        for g, s in zip(genesis, surrogate, strict=True)
        if (g > mg) == (s > ms) and (g < mg) == (s < ms)
    )
    return round(same / len(genesis), 3)


def analyze(records: list[dict]) -> dict:
    done = [r for r in records if r.get("pass1_surrogate") is not None]
    genesis = [float(r["pass1_genesis"]) for r in done]
    surrogate = [float(r["pass1_surrogate"]) for r in done]
    return {
        "population": len(records),
        "completed": len(done),
        "spearman_rho": spearman(genesis, surrogate),
        "screening_agreement": screening_agreement(genesis, surrogate),
        "table": [
            {
                "id": r["id"],
                "pass1_genesis": r["pass1_genesis"],
                "pass1_surrogate": r.get("pass1_surrogate"),
                "surrogate_failures": r.get("surrogate_failures"),
                "infra": r.get("infra"),
            }
            for r in records
        ],
    }


# ---------------------------------------------------------------- population


def build_population() -> list[dict]:
    import yaml

    POP_DIR.mkdir(parents=True, exist_ok=True)
    manifest = []
    for agent in ("claude", "codex"):
        results = json.loads((H1_RUNS / f"h1_results_{agent}.json").read_text())
        for rec in results["records"]:
            final = rec.get("final_graph") or {}
            if not final.get("launched"):
                continue
            attempt = int(rec["attempt"])
            src = H1_RUNS / agent / f"attempt_{attempt:02d}" / "first_graph.yaml"
            if not src.exists():
                print(f"[m3] MISSING graph for {agent} attempt {attempt}", file=sys.stderr)
                continue
            text = src.read_text()
            yaml.safe_load(text)  # must parse
            gid = f"{agent}_{attempt:02d}"
            (POP_DIR / f"{gid}.yaml").write_text(text)
            manifest.append(
                {
                    "id": gid,
                    "agent": agent,
                    "attempt": attempt,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "pass1_genesis": final.get("pass1"),
                    "genesis_failures": final.get("failures"),
                }
            )
    (POP_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


# ---------------------------------------------------------------- run


def stage_graph(entry: dict, work: Path) -> Path:
    """Absolute-path staging + the environment swap (dora-genesis ->
    world-model-env, same node id so wiring holds) + client seeds."""
    import yaml

    doc = yaml.safe_load((POP_DIR / f"{entry['id']}.yaml").read_text())
    for node in doc["nodes"]:
        raw = str(node.get("path", ""))
        if raw.endswith("dora_genesis.py"):
            node["path"] = str(REPO_ROOT / "src" / "aisle" / "nodes" / "world_model_env.py")
        elif raw.startswith("../"):
            node["path"] = str((REPO_ROOT / raw.removeprefix("../")).resolve())
        if node["id"] == "rollout-client":
            node.setdefault("env", {}).update(
                {
                    "AISLE_SEEDS": SEEDS,
                    "AISLE_TIER": "T1",
                    "AISLE_RESULTS": str(work / "episodes.jsonl"),
                }
            )
    graph = work / "graph.yaml"
    graph.write_text(yaml.safe_dump(doc, sort_keys=False))
    return graph


def run_one(entry: dict, out_root: Path) -> dict:
    work = out_root / entry["id"]
    work.mkdir(parents=True, exist_ok=True)
    graph = stage_graph(entry, work)
    results = work / "episodes.jsonl"
    proc = subprocess.Popen(
        ["dora", "run", str(graph), "--uv"],
        cwd=REPO_ROOT,
        stdout=open(work / "dora.stdout.log", "w"),
        stderr=open(work / "dora.stderr.log", "w"),
        start_new_session=True,
    )
    deadline = time.monotonic() + RUN_TIMEOUT_S
    rows: list[dict] = []
    while time.monotonic() < deadline:
        if results.exists():
            rows = [json.loads(x) for x in results.read_text().splitlines() if x.strip()]
            if len(rows) >= 8:
                break
        if proc.poll() is not None:
            break
        time.sleep(2)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=15)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    rows = (
        [json.loads(x) for x in results.read_text().splitlines() if x.strip()]
        if results.exists()
        else rows
    )
    out = dict(entry)
    if not rows:
        out["infra"] = "no episodes produced"
        out["pass1_surrogate"] = None
        return out
    out["pass1_surrogate"] = round(
        sum(1 for r in rows if r.get("status") == "success") / len(rows), 3
    )
    failures: dict[str, int] = {}
    for r in rows:
        if r.get("failure"):
            failures[r["failure"]] = failures.get(r["failure"], 0) + 1
    out["surrogate_failures"] = failures
    out["episodes"] = len(rows)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-population", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs" / "m3-ranking")
    parser.add_argument("--only", default=None, help="single population id (smoke)")
    args = parser.parse_args()

    if args.build_population:
        manifest = build_population()
        print(json.dumps({"ok": True, "population": len(manifest)}))
        return 0
    if args.run:
        manifest = json.loads((POP_DIR / "manifest.json").read_text())
        if args.only:
            manifest = [e for e in manifest if e["id"] == args.only]
        args.out.mkdir(parents=True, exist_ok=True)
        records = []
        for entry in manifest:
            print(f"[m3] running {entry['id']}", file=sys.stderr)
            records.append(run_one(entry, args.out))
            (args.out / "records.json").write_text(json.dumps(records, indent=1) + "\n")
        print(json.dumps({"ok": True, "ran": len(records)}))
        return 0
    if args.analyze:
        records = json.loads((args.out / "records.json").read_text())
        print(json.dumps(analyze(records), indent=1))
        return 0
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
