"""Measure ADR-30 lockstep/free-run wall cost at an equal sim horizon."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOCKSTEP_KEYS = {
    "AISLE_LOCKSTEP",
    "AISLE_TURN_NODE",
    "AISLE_TURN_OUTPUTS",
    "AISLE_TURN_WALL_OUTPUTS",
}


def measurement_graph(source: Path, run_dir: Path, mode: str, horizon_s: float) -> Path:
    """Create one fixed-horizon graph without changing its policy workload."""
    doc = yaml.safe_load(source.read_text())
    nodes = doc["nodes"]
    for node in nodes:
        path = node.get("path")
        if isinstance(path, str) and ":" not in path:
            node["path"] = str((source.parent / path).resolve())
    if mode == "free-run":
        nodes[:] = [node for node in nodes if node["id"] != "turn-barrier"]
        for node in nodes:
            env = node.get("env") or {}
            for key in LOCKSTEP_KEYS:
                env.pop(key, None)
            node["env"] = env
            node.get("inputs", {}).pop("turn", None)
            node.get("inputs", {}).pop("turn_commit", None)
            node["outputs"] = [
                output
                for output in node.get("outputs", [])
                if output not in {"turn_done", "sim_turn"}
            ]
    else:
        barrier = next(node for node in nodes if node["id"] == "turn-barrier")
        plan = barrier["env"]["AISLE_TURN_PLAN"]
        barrier["env"]["AISLE_TURN_PLAN"] = str((source.parent / plan).resolve())

    result = run_dir / f"{mode}.json"
    nodes.append(
        {
            "id": "sim-horizon",
            "path": str((ROOT / "tools/sim_horizon_recorder.py").resolve()),
            "inputs": {
                "joint_state": {
                    "source": "dora-genesis/joint_state",
                    "queue_size": 100,
                    "queue_policy": "backpressure",
                }
            },
            "env": {"AISLE_HORIZON_OUTPUT": str(result), "AISLE_HORIZON_S": str(horizon_s)},
        }
    )
    out = run_dir / f"{mode}.yaml"
    out.write_text(yaml.safe_dump(doc, sort_keys=False))
    return out


def run_mode(source: Path, mode: str, horizon_s: float, timeout_s: float) -> dict:
    with tempfile.TemporaryDirectory(prefix="aisle-adr30-cost-") as raw_dir:
        run_dir = Path(raw_dir)
        graph = measurement_graph(source, run_dir, mode, horizon_s)
        result_path = run_dir / f"{mode}.json"
        env = {
            **os.environ,
            # Keep issuing real episodes if an early verdict lands before the
            # fixed horizon. Repeating a deterministic seed preserves equal
            # work between modes without mutating the compiled turn topology.
            "AISLE_SEEDS": ",".join(["100"] * 100),
            "AISLE_TIMEOUT_S": str(horizon_s + 10),
            "AISLE_RESULTS": str(run_dir / "ignored-results.jsonl"),
            "AISLE_TURN_EPOCH": "1",
        }
        proc = subprocess.Popen(
            ["dora", "run", str(graph), "--uv"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout_s
        try:
            while time.monotonic() < deadline and not result_path.exists():
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if not result_path.exists():
            return {"ok": False, "mode": mode, "error": "sim horizon not reached"}
        return {"mode": mode, **json.loads(result_path.read_text())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "graphs",
        nargs="*",
        type=Path,
        default=[Path("graphs/expert_t0.yaml"), Path("graphs/expert_s1.yaml")],
    )
    parser.add_argument("--horizon-s", type=float, default=60.0)
    parser.add_argument("--timeout-s", type=float, default=900.0)
    args = parser.parse_args()
    samples = []
    for raw_graph in args.graphs:
        graph = raw_graph if raw_graph.is_absolute() else ROOT / raw_graph
        lockstep = run_mode(graph, "lockstep", args.horizon_s, args.timeout_s)
        free = run_mode(graph, "free-run", args.horizon_s, args.timeout_s)
        sample = {"graph": str(graph.relative_to(ROOT)), "lockstep": lockstep, "free_run": free}
        if lockstep.get("ok") and free.get("ok"):
            sample["ratio"] = lockstep["wall_s"] / free["wall_s"]
            sample["within_2x"] = sample["ratio"] <= 2.0
        samples.append(sample)
    ok = all(sample.get("within_2x") is True for sample in samples)
    print(json.dumps({"ok": ok, "horizon_s": args.horizon_s, "samples": samples}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
