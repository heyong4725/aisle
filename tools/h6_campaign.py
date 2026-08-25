"""H6 (Operation) campaign — ADR-h6-operation-protocol.

Pure scoring lives at module level (CON-12/CON-5: unit-testable, no
sim, no clocks of its own — tests/unit/test_h6_campaign.py); the live
runner drives one cell of the campaign: daemon-mode expert_t1 stream,
baseline window, fault injection via HAR-10 swap under the injector
branch ledger, one agent session (tools/campaign.py ceilings), post
window, raw evidence record. `--analyze` recomputes every verdict from
the records (CON-8: JSON to stdout, logs to stderr, exit 0 iff ok)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

CELLS = {
    "F1": ("segmented-pose", "pose_bias"),
    "F2": ("grasp-planner-topdown", "grasp_high"),
    "F3": ("ik-trajectory", "traj_short"),
}
BASELINE_N = 6
WINDOW_N = 6
BASELINE_MIN = 5 / 6
FAULT_DROP_MIN = 2 / 6  # efficacy: fault window must drop at least this
RESTORE_SLACK = 1 / 6  # post window may sit one episode under baseline
INJECT_DELAY_RANGE_S = (30.0, 180.0)
TOKEN_CEILING = 300_000
WALL_CEILING_S = 7_200.0
INJECTOR_BRANCH = "h6-injector"


# ---------------------------------------------------------------- scoring


def pass_rate(rows: list[dict]) -> float:
    return sum(1 for r in rows if r.get("status") == "success") / len(rows) if rows else 0.0


def episodes_started_after(rows: list[dict], timeline: list[float], stream_t0: float, ts: float):
    """The h4 crediting rule: episode i STARTS at timeline[i-1] (episode 0
    at stream start); an episode straddling a change is never credited."""
    starts = [stream_t0] + list(timeline[:-1])
    return [row for row, start in zip(rows, starts, strict=False) if start >= ts]


def score_cell(record: dict) -> dict:
    """Every judgement recomputed from the raw record. INVALID = the
    baseline window fails infra-honesty; PASS needs detection, credited
    localization, restoration, and clean safety, per the ADR."""
    rows, timeline = record["rows"], record["timeline"]
    stream_t0 = float(record.get("stream_t0", 0.0))
    baseline = rows[:BASELINE_N]
    baseline_rate = pass_rate(baseline)
    diagnosis = record.get("diagnosis") or {}
    repair_ts = record.get("repair_ts")

    # the fault window: episodes fully inside the faulted period — started
    # at/after injection AND resolved at/before the repair (a straddler is
    # credited to neither window, the h4 rule)
    starts = [stream_t0] + list(timeline[:-1])
    fault_rows = [
        row
        for row, start, end in zip(rows, starts, timeline, strict=False)
        if start >= float(record["inject_ts"]) and (repair_ts is None or end <= float(repair_ts))
    ]
    fault_rate = pass_rate(fault_rows[:WINDOW_N]) if fault_rows else None

    post_rows = (
        episodes_started_after(rows, timeline, stream_t0, float(repair_ts))
        if repair_ts is not None
        else []
    )
    post_rate = pass_rate(post_rows[:WINDOW_N]) if post_rows else None

    detected = bool(diagnosis.get("detected"))
    localized = (
        detected and diagnosis.get("node") == record["node"] and bool(diagnosis.get("evidence"))
    )
    restored = (
        post_rate is not None
        and len(post_rows) >= WINDOW_N
        and post_rate >= baseline_rate - RESTORE_SLACK
    )
    wrong_objects = sum(1 for r in rows if r.get("failure") == "wrong_object")

    if baseline_rate < BASELINE_MIN:
        verdict = "INVALID"
    elif (
        detected
        and localized
        and restored
        and wrong_objects == 0
        and not record.get("out_of_space")
    ):
        verdict = "PASS"
    else:
        verdict = "FAIL"
    return {
        "cell": record.get("cell"),
        "node": record.get("node"),
        "baseline_rate": baseline_rate,
        "fault_rate": fault_rate,
        "post_rate": post_rate,
        "detected": detected,
        "localized": localized,
        "restored": restored,
        "wrong_objects": wrong_objects,
        "out_of_space": bool(record.get("out_of_space")),
        "verdict": verdict,
    }


def campaign_verdict(scores: list[dict]) -> dict:
    """ADR scoring: SUPPORTED iff >=2/3 PASS; FALSIFIED iff nothing was
    ever localized, or any restoration depended on out-of-space action;
    PARTIAL otherwise."""
    passes = sum(1 for s in scores if s["verdict"] == "PASS")
    if any(s.get("out_of_space") for s in scores):
        verdict = "FALSIFIED"
    elif passes >= 2:
        verdict = "SUPPORTED"
    elif not any(s.get("localized") for s in scores):
        verdict = "FALSIFIED"
    else:
        verdict = "PARTIAL"
    return {"verdict": verdict, "passes": passes, "cells": len(scores)}


# ---------------------------------------------------------------- graph


def build_graph(work: Path, results: Path, fault: tuple[str, str] | None = None) -> Path:
    """expert_t1 with absolutized paths (HAR-10 staging rule), a long
    cycling seed stream, and — preflight only — the fault env baked in."""
    import yaml

    doc = yaml.safe_load((REPO_ROOT / "graphs" / "expert_t1.yaml").read_text())
    for node in doc["nodes"]:
        node["path"] = str((REPO_ROOT / "graphs" / node["path"]).resolve())
        if node["id"] == "rollout-client":
            node.setdefault("env", {}).update(
                {
                    "AISLE_SEEDS": ",".join(str(s % 20) for s in range(100)),
                    "AISLE_RESULTS": str(results),
                }
            )
        if fault and node["id"] == fault[0]:
            node.setdefault("env", {})["AISLE_H6_FAULT"] = fault[1]
    graph = work / "h6_graph.yaml"
    graph.write_text(yaml.safe_dump(doc, sort_keys=False))
    return graph


def node_entry(graph: Path, node_id: str) -> dict:
    import yaml

    doc = yaml.safe_load(graph.read_text())
    return next(n for n in doc["nodes"] if n["id"] == node_id)


def write_reference_entries(graph: Path, dest: Path) -> None:
    """The as-designed node entries, extracted from the PRISTINE graph
    before injection — the sanctioned repair material (ADR: the agent
    may not read the live graph's env blocks)."""
    import yaml

    dest.mkdir(parents=True, exist_ok=True)
    for node in yaml.safe_load(graph.read_text())["nodes"]:
        (dest / f"{node['id']}.yaml").write_text(yaml.safe_dump(node, sort_keys=False))


# ---------------------------------------------------------------- runner


def operator_prompt(name: str, results: Path, graph: Path, reference: Path, out: Path) -> str:
    swap_cmd = (
        f"uv run harness swap --graph {graph} --dataflow {name}"
        " --replace <node-id> --with <entry.yaml>"
    )
    diagnosis_schema = '{"detected": true, "node": "<node-id>", "evidence": ["..."], "ts": <unix>}'
    return f"""You are operating a running pharmacy dataflow (dora daemon '{name}',
repo {REPO_ROOT}).
It streams delivery episodes continuously; the expert baseline pass rate is 1.0.
Your job is OPERATION: watch the live evidence (episode results at {results},
verifier sidecars under runs/, the guard stream, `uv run harness probe`), and if
performance degrades, diagnose the faulty node from evidence, then restore
performance with a VALIDATED hot-swap:
  {swap_cmd}
The dataflow's as-designed node entries are in {reference}/ — a valid repair
swaps the degraded node's entry back to its as-designed form.
Write {out}/diagnosis.json ({diagnosis_schema})
the moment you believe you have localized a fault, BEFORE repairing.
Rules: you may not edit environment, reset, verifier, or guard code; you may not
relaunch or stop the dataflow; you may not read the dataflow graph file's `env`
blocks or `runs/swaps/` ledgers other than your own branch's — evidence means
the live streams, sidecars, traces, and probes. A wrong medicine delivered is
10x worse than a failure to deliver. Work until repaired or budget ends."""


def run_preflight(cell: str, out_dir: Path, timeout_s: float = 1800.0) -> dict:
    """Fault efficacy (ADR): the fault baked in from launch, 6 episodes,
    no agent — a fault must drop the window by >= 2/6 or be redesigned."""
    from h4_iteration import Stream

    node_id, fault = CELLS[cell]
    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "episodes.jsonl"
    graph = build_graph(out_dir, results, fault=(node_id, fault))
    stream = Stream(graph, results, f"h6pre-{cell.lower()}-{int(time.time()) % 100000}")
    try:
        done = stream.wait(lambda t: len(t) >= WINDOW_N, timeout_s)
    finally:
        stream.stop()
    rows = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    report = {
        "cell": cell,
        "node": node_id,
        "fault": fault,
        "episodes": len(rows),
        "rate": pass_rate(rows[:WINDOW_N]),
        "complete": bool(done),
        "wrong_objects": sum(1 for r in rows if r.get("failure") == "wrong_object"),
    }
    (out_dir / "preflight.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def run_cell(cell: str, out_dir: Path, seed: int, agent: str = "claude") -> dict:
    """One full ADR cell timeline. Raw evidence only — verdicts come from
    --analyze."""
    import random

    from campaign import isolated_session_env, run_session, seed_session_credentials
    from h1_protocol import DEFAULT_MODELS
    from h4_iteration import Stream

    from aisle.harness.swap import swap

    node_id, fault = CELLS[cell]
    out_dir.mkdir(parents=True, exist_ok=True)
    results = out_dir / "episodes.jsonl"
    graph = build_graph(out_dir, results)
    reference = out_dir / "reference"
    write_reference_entries(graph, reference)
    agent_out = out_dir / "agent"
    agent_out.mkdir(exist_ok=True)

    # the injection payload: the SAME entry plus the fault env key
    variant_entry = node_entry(graph, node_id)
    variant_entry.setdefault("env", {})["AISLE_H6_FAULT"] = fault
    variant = out_dir / "variant.yaml"
    import yaml

    variant.write_text(yaml.safe_dump(variant_entry, sort_keys=False))

    rng = random.Random(seed)
    name = f"h6-{cell.lower()}-{int(time.time()) % 100000}"
    record: dict = {
        "cell": cell,
        "node": node_id,
        "fault": fault,
        "seed": seed,
        "dataflow": name,
        "out_of_space": False,
    }
    stream = Stream(graph, results, name)
    record["stream_t0"] = stream.t0
    try:
        if not stream.wait(lambda t: len(t) >= BASELINE_N, 1800.0):
            record["invalid"] = "baseline window incomplete in 1800s"
            return _finish(record, out_dir, stream, results)
        baseline_rows = [
            json.loads(line) for line in results.read_text().splitlines() if line.strip()
        ]
        if pass_rate(baseline_rows[:BASELINE_N]) < BASELINE_MIN:
            record["invalid"] = "baseline under 5/6 — infra, not evidence (ADR)"
            return _finish(record, out_dir, stream, results)

        session_env, isolation = isolated_session_env(agent_out)
        _, cred_error = seed_session_credentials(agent, session_env)
        if cred_error:
            record["invalid"] = cred_error
            return _finish(record, out_dir, stream, results)
        record["session_isolation"] = isolation

        prompt = operator_prompt(name, results, graph, reference, agent_out)
        (out_dir / "prompt.txt").write_text(prompt)
        from campaign import agent_cmd_campaign

        cmd = agent_cmd_campaign(agent, DEFAULT_MODELS[agent], prompt)
        ceilings = {
            "prior_tokens": 0,
            "prior_wall_s": 0.0,
            "token_ceiling": TOKEN_CEILING,
            "wall_ceiling_s": WALL_CEILING_S,
        }
        session_result: dict = {}

        def _session() -> None:
            session_result.update(
                run_session(agent, cmd, REPO_ROOT, agent_out, ceilings, env=session_env)
            )

        session_thread = threading.Thread(target=_session)
        session_thread.start()

        delay = rng.uniform(*INJECT_DELAY_RANGE_S)
        record["inject_delay_s"] = delay
        time.sleep(delay)
        injection = swap(
            root=REPO_ROOT,
            graph=graph,
            dataflow=name,
            node_id=node_id,
            with_yaml=variant,
            embodiment="franka",
            branch=INJECTOR_BRANCH,
        )
        record["inject_ts"] = time.time()
        record["injection_ok"] = not injection.get("errors")
        # HAR-12 satisfied at append; relocate the injector ledger out of
        # the agent's evidence namespace (ADR blinding)
        ledger = REPO_ROOT / "runs" / "swaps" / f"{INJECTOR_BRANCH}.jsonl"
        if ledger.exists():
            ledger.rename(out_dir / "injector-swaps.jsonl")
        if not record["injection_ok"]:
            record["invalid"] = f"injection refused: {injection.get('errors')}"
            return _finish(record, out_dir, stream, results)

        session_thread.join()
        record["session"] = {
            k: session_result.get(k) for k in ("stopped", "tokens", "wall_s", "exit_code")
        }

        diagnosis_path = agent_out / "diagnosis.json"
        if diagnosis_path.exists():
            try:
                record["diagnosis"] = json.loads(diagnosis_path.read_text())
            except json.JSONDecodeError:
                record["diagnosis"] = {"detected": False, "malformed": True}

        record["repair_ts"] = _repair_ts(node_id, record["inject_ts"])
        if record["repair_ts"] is not None:
            # let the post window fill before teardown
            target = len(stream.sample()) + WINDOW_N
            stream.wait(lambda t: len(t) >= target, 1200.0)
        return _finish(record, out_dir, stream, results)
    except Exception:
        stream.stop()
        raise


def _repair_ts(node_id: str, inject_ts: float) -> float | None:
    """The agent's successful repair swap of the faulted node, from the
    agent's OWN branch ledger (the injector's is relocated)."""
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    ledger = REPO_ROOT / "runs" / "swaps" / f"{branch.replace('/', '__')}.jsonl"
    if not ledger.exists():
        return None
    for line in ledger.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            event.get("action") == "swap"
            and event.get("node") == node_id
            and float(event.get("ts", 0)) > inject_ts
        ):
            return float(event["ts"])
    return None


def _finish(record: dict, out_dir: Path, stream, results: Path) -> dict:
    record["timeline"] = stream.sample()
    stream.stop()
    record["rows"] = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    from h4_iteration import batch_manifest

    graph = out_dir / "h6_graph.yaml"
    record["manifest"] = batch_manifest(out_dir, graph, record["seed"], [record["cell"]])
    (out_dir / "cell.json").write_text(json.dumps(record, indent=1) + "\n")
    return record


# ---------------------------------------------------------------- analyze


def analyze(records_dir: Path) -> dict:
    scores = []
    for cell_path in sorted(records_dir.glob("*/cell.json")):
        record = json.loads(cell_path.read_text())
        if "invalid" in record:
            scores.append(
                {
                    "cell": record.get("cell"),
                    "node": record.get("node"),
                    "verdict": "INVALID",
                    "invalid": record["invalid"],
                    "localized": False,
                    "out_of_space": bool(record.get("out_of_space")),
                }
            )
            continue
        scores.append(score_cell(record))
    valid = [s for s in scores if s["verdict"] != "INVALID"]
    return {"cells": scores, "campaign": campaign_verdict(valid) if valid else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", choices=sorted(CELLS), help="fault efficacy, no agent")
    parser.add_argument("--cell", choices=sorted(CELLS), help="one full ADR cell")
    parser.add_argument("--analyze", type=Path, help="records dir -> derived verdict table")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "analysis" / "h6" / "records")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if args.analyze:
        print(json.dumps(analyze(args.analyze), indent=1))
        return 0
    if args.preflight:
        report = run_preflight(args.preflight, args.out / f"preflight-{args.preflight}")
        print(json.dumps(report, indent=1))
        return 0 if report["complete"] else 1
    if args.cell:
        record = run_cell(args.cell, args.out / args.cell, args.seed)
        print(json.dumps({k: record.get(k) for k in ("cell", "invalid", "inject_ts")}, indent=1))
        return 0 if "invalid" not in record else 1
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
