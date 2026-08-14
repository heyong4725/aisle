"""Fleet mode (design doc §8.4.3): N logical agents on ONE batched sim.

`fleet_graph` stamps a single-env expert graph into an N-agent fleet
graph: the bridge and budget-guard stay SHARED (the bridge gains
AISLE_N_ENVS, the guard's per-env state and prefix dispatch already
handle N command sources), every policy node is cloned per agent with
`AISLE_ENV_PIN=<k>` (nodes drop other envs' messages, senders stamp
their env), and each agent's rollout client writes its own results
file. `harness fleet` generates, validates, launches, waits, and
aggregates — the §8.4.3 "100-line scheduler", made concrete.

MVP scope (documented): L0-rung graphs (cameras render env 0 only,
ADR-7 — L0 policy and the oracle verifier need no pixels), teleport
resets, local budget baseline. The fleet-scaling DoD plots additionally
need real agent sessions driving separate worktrees; this module is the
substrate they schedule onto.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

# nodes that stay SHARED in a fleet graph; everything else is per-agent
SHARED_NODES = ("dora-genesis", "budget-guard", "reset", "turn-barrier")
# guard inputs that must be re-pointed per agent with suffixed input ids
GUARD_FANIN = {"joint_cmd": "joint_cmd", "gripper_cmd": "gripper_cmd"}


def _agent_id(node_id: str, agent: int) -> str:
    return f"{node_id}-a{agent}"


def fleet_graph(doc: dict, agents: int) -> dict:
    """Stamp a single-env graph into an N-agent fleet graph (pure)."""
    if agents < 2:
        raise ValueError(f"fleet needs >=2 agents, got {agents}")
    nodes = doc["nodes"]
    by_id = {n["id"]: n for n in nodes}
    if "dora-genesis" not in by_id or "budget-guard" not in by_id:
        raise ValueError("fleet_graph needs a dora-genesis bridge and a budget-guard")
    policy_ids = [n["id"] for n in nodes if n["id"] not in SHARED_NODES]

    out_nodes: list[dict] = []
    for node in nodes:
        if node["id"] not in SHARED_NODES:
            continue
        clone = copy.deepcopy(node)
        if clone["id"] == "dora-genesis":
            clone.setdefault("env", {})["AISLE_N_ENVS"] = str(agents)
        if clone["id"] == "budget-guard":
            # fan-in: one suffixed input pair per agent's executor
            inputs = {
                k: v
                for k, v in clone["inputs"].items()
                if k not in GUARD_FANIN and not k.startswith(("joint_cmd", "gripper_cmd"))
            }
            for agent in range(agents):
                for base in GUARD_FANIN:
                    source_node, _, source_topic = by_id["budget-guard"]["inputs"][base][
                        "source"
                    ].partition("/")
                    inputs[f"{base}_{agent}"] = {
                        "source": f"{_agent_id(source_node, agent)}/{source_topic}",
                        "queue_size": 100,
                    }
            clone["inputs"] = inputs
        if clone["id"] == "reset":
            # the reset service is stateless per request: shared, with its
            # request stream fanned in from every agent's client
            source_node, _, source_topic = clone["inputs"]["reset"]["source"].partition("/")
            inputs = {k: v for k, v in clone["inputs"].items() if k != "reset"}
            for agent in range(agents):
                inputs[f"reset_{agent}"] = {
                    "source": f"{_agent_id(source_node, agent)}/{source_topic}",
                    "queue_size": 100,
                }
            clone["inputs"] = inputs
        out_nodes.append(clone)

    for agent in range(agents):
        for node_id in policy_ids:
            clone = copy.deepcopy(by_id[node_id])
            clone["id"] = _agent_id(node_id, agent)
            env = clone.setdefault("env", {})
            env["AISLE_ENV_PIN"] = str(agent)
            if "AISLE_TURN_NODE" in env:
                env["AISLE_TURN_NODE"] = clone["id"]
            inputs = {}
            for key, spec in clone.get("inputs", {}).items():
                if isinstance(spec, str):  # timer shorthand
                    inputs[key] = spec
                    continue
                source_node, _, source_topic = spec["source"].partition("/")
                if source_node in policy_ids:
                    spec = {**spec, "source": f"{_agent_id(source_node, agent)}/{source_topic}"}
                inputs[key] = spec
            clone["inputs"] = inputs
            out_nodes.append(clone)

    # One terminal barrier closes the whole batched scene. Replicating it
    # would leave the bridge's single commit edge dangling and would let
    # independent agents advance shared physics. Expand each cloned policy's
    # watermark edge while retaining shared guard/reset closures once.
    barrier = next(node for node in out_nodes if node["id"] == "turn-barrier")
    fixed_inputs = {
        name: spec
        for name, spec in barrier.get("inputs", {}).items()
        if not name.startswith("done_")
    }
    done_sources: list[str] = []
    for name, spec in by_id["turn-barrier"].get("inputs", {}).items():
        if not name.startswith("done_"):
            continue
        source_node, _, source_output = spec["source"].partition("/")
        if source_node in policy_ids:
            done_sources.extend(
                f"{_agent_id(source_node, agent)}/{source_output}" for agent in range(agents)
            )
        else:
            done_sources.append(spec["source"])
    for index, source in enumerate(sorted(done_sources)):
        fixed_inputs[f"done_{index}"] = {
            "source": source,
            "queue_size": 4,
            "queue_policy": "backpressure",
        }
    barrier["inputs"] = fixed_inputs
    return {**doc, "nodes": out_nodes}


def aggregate(results_paths: list[Path]) -> dict:
    """Per-agent + fleet-level metrics from the per-agent results files."""
    from aisle.harness.rollout import compute_metrics

    per_agent = []
    episodes_all: list[dict] = []
    for path in results_paths:
        episodes = (
            [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if path.exists()
            else []
        )
        episodes_all.extend(episodes)
        per_agent.append(
            {"results": str(path), "episodes": len(episodes)} | compute_metrics(episodes)
        )
    return {
        "agents": len(results_paths),
        "per_agent": per_agent,
        "fleet": compute_metrics(episodes_all),
        "episodes_total": len(episodes_all),
    }


def run_fleet(
    graph: Path,
    agents: int,
    episodes: int,
    seeds: list[int],
    out_dir: Path,
    timeout_s: float,
    launch,
    root: Path | None = None,
) -> dict:
    """Generate, launch (via the injected `launch(graph_path, env)`),
    wait for every agent's results, aggregate. `launch` returns a poll()
    callable; injection keeps this pure enough to unit-test."""
    import yaml

    doc = yaml.safe_load(graph.read_text())
    # the stamped graph lives in out_dir, not graphs/: absolutize node
    # paths against the BASE graph's directory or dora cannot find them
    for node in doc["nodes"]:
        node["path"] = str((graph.parent / node["path"]).resolve())
    fleet_doc = fleet_graph(doc, agents)
    out_dir.mkdir(parents=True, exist_ok=True)
    results_paths = [out_dir / f"results_a{agent}.jsonl" for agent in range(agents)]
    for agent in range(agents):
        for node in fleet_doc["nodes"]:
            if node["id"] == _agent_id("rollout-client", agent):
                # each agent works a DISTINCT seed lane (offset by
                # 1000*agent): identical seeds put both envs on identical
                # marginal-contact trajectories, which the wall-coupled
                # timing (issue #71) then flips in lockstep — and the
                # fleet study wants independent workloads anyway
                lane = [s + 1000 * agent for s in seeds[:episodes]]
                node.setdefault("env", {}).update(
                    AISLE_RESULTS=str(results_paths[agent].resolve()),
                    AISLE_SEEDS=",".join(str(s) for s in lane),
                )
    from aisle.harness.registry import load_manifests
    from aisle.harness.validate import compile_turn_plan

    repo_root = (root or graph.resolve().parent.parent).resolve()
    manifest_list, manifest_errors = load_manifests(repo_root)
    if manifest_errors:
        raise ValueError(f"cannot compile fleet turn plan: {manifest_errors}")
    manifests = {manifest["id"]: manifest for _, manifest in manifest_list}
    plan = compile_turn_plan(fleet_doc["nodes"], manifests)
    plan_path = out_dir / "fleet-turn-plan.json"
    plan_path.write_text(
        json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    barrier = next(node for node in fleet_doc["nodes"] if node["id"] == "turn-barrier")
    barrier.setdefault("env", {})["AISLE_TURN_PLAN"] = str(plan_path.resolve())
    graph_path = out_dir / "fleet_graph.yaml"
    graph_path.write_text(yaml.safe_dump(fleet_doc, sort_keys=False))

    poll = launch(graph_path)
    started = time.monotonic()
    while time.monotonic() - started < timeout_s:
        done = all(
            path.exists() and len(path.read_text().splitlines()) >= episodes
            for path in results_paths
        )
        if done or poll() is not None:
            break
        time.sleep(2.0)
    report = aggregate(results_paths)
    report["graph"] = str(graph_path)
    report["wall_s"] = round(time.monotonic() - started, 1)
    return report
