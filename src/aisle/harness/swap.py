"""Hot-swap and live-probe operations (SPEC 070 HAR-10..12; design doc
§9.1 decision 1). The H4 mechanism: iterate on a RUNNING dataflow instead
of relaunching, with the validator still the gatekeeper for every
mutation. CON-8: callers emit JSON; helpers here return dicts.

The dora interaction is a thin injectable seam (`runner`) so unit tests
never need a live dataflow; the default drives the `dora` CLI
(node add / connect / remove — present since 1.0.0-rc.4).
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import yaml

from aisle.harness.ideas import open_ideas
from aisle.harness.validate import validate


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["dora", *cmd], capture_output=True, text=True, timeout=120)


def swapped_graph_doc(graph_path: Path, node_id: str, replacement: dict) -> dict:
    """The POST-SWAP graph document: the named node replaced in place,
    everything else untouched. Refuses unknown node ids."""
    doc = yaml.safe_load(graph_path.read_text())
    nodes = doc.get("nodes") or []
    for index, node in enumerate(nodes):
        if node.get("id") == node_id:
            nodes[index] = replacement
            return doc
    raise SystemExit(json.dumps({"ok": False, "error": f"node {node_id!r} not in {graph_path}"}))


def swap_event(root: Path, branch: str, event: dict) -> dict:
    """HAR-12: the append-only swap/probe event log feeding the H4
    iteration-latency table. Records the open idea (if any) so latency can
    be measured idea-open -> first episode under the change."""
    ideas = [i.get("id") for i in open_ideas(root, branch)]
    entry = {"ts": time.time(), "open_idea": ideas[-1] if ideas else None, **event}
    path = root / "runs" / "swaps" / f"{branch.replace('/', '__')}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def swap(
    root: Path,
    graph: Path,
    dataflow: str,
    node_id: str,
    with_yaml: Path,
    embodiment: str,
    branch: str,
    runner=_default_runner,
) -> dict:
    """HAR-10: validate the FULL post-swap graph (every SPEC 060 check)
    BEFORE any runtime mutation; only then add/connect the replacement and
    remove the old node."""
    replacement = yaml.safe_load(with_yaml.read_text())
    if not isinstance(replacement, dict) or replacement.get("id") != node_id:
        return {
            "ok": False,
            "error": "replacement yaml must be a single node doc with the SAME id "
            "(edges are preserved by identity)",
        }
    doc = swapped_graph_doc(graph, node_id, replacement)
    staged = graph.parent / f".swap-{node_id}.yaml"
    staged.write_text(yaml.safe_dump(doc, sort_keys=False))
    try:
        report = validate(staged, root, embodiment, allow_unproven=False)
        if not report["ok"]:
            return {"ok": False, "refused": report}
        for cmd in (
            ["node", "remove", "-d", dataflow, node_id],
            ["node", "add", "-d", dataflow, "--from-yaml", str(staged)],
        ):
            proc = runner(cmd)
            if proc.returncode != 0:
                return {
                    "ok": False,
                    "error": f"dora {' '.join(cmd[:2])} failed: {(proc.stderr or '')[-200:]}",
                }
    finally:
        staged.unlink(missing_ok=True)
    event = swap_event(root, branch, {"action": "swap", "dataflow": dataflow, "node": node_id})
    return {"ok": True, "swapped": node_id, "dataflow": dataflow, "ts": event["ts"]}


def probe(
    root: Path,
    dataflow: str,
    topic: str,
    seconds: float,
    branch: str,
    runner=_default_runner,
) -> dict:
    """HAR-11: attach a temporary read-only inspector to a live topic and
    detach after the window. oracle_state is refused (VAL-6 has no probe
    exemption); probes have no outputs so they can never publish."""
    if topic.endswith("/oracle_state"):
        return {"ok": False, "error": "probes may not read ground truth (VAL-6)"}
    probe_id = f"probe-{int(time.time())}"
    node_doc = {
        "id": probe_id,
        "path": str(Path(__file__).with_name("trace_recorder.py")),
        "inputs": {"probe": {"source": topic, "queue_size": 100}},
        "env": {"AISLE_TRACE_DIR": str(root / "runs" / "probes" / probe_id)},
    }
    staged = root / "runs" / "probes" / f"{probe_id}.yaml"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(yaml.safe_dump(node_doc, sort_keys=False))
    proc = runner(["node", "add", "-d", dataflow, "--from-yaml", str(staged)])
    if proc.returncode != 0:
        return {"ok": False, "error": f"attach failed: {(proc.stderr or '')[-200:]}"}
    time.sleep(seconds)
    detach = runner(["node", "remove", "-d", dataflow, probe_id])
    event = swap_event(root, branch, {"action": "probe", "dataflow": dataflow, "topic": topic})
    return {
        "ok": detach.returncode == 0,
        "probe": probe_id,
        "traces": str(root / "runs" / "probes" / probe_id),
        "ts": event["ts"],
    }
