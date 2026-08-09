"""Attestation of the EXECUTED dataflow (issue #128) and the rung-filtered
recorder subscription (TC-9): an L1 run's trace must be self-evidencing —
physically unable to contain ground-truth pose — rather than relying on the
bridge's restraint alone."""

from pathlib import Path

import pytest
import yaml

from aisle.harness.rollout import instrumented_graph

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def _recorder_inputs(graph_path, tmp_path):
    (tmp_path / "run").mkdir(exist_ok=True)
    out = instrumented_graph(graph_path, REPO_ROOT, tmp_path / "run")
    doc = yaml.safe_load(out.read_text())
    recorder = next(n for n in doc["nodes"] if n["id"] == "trace-recorder")
    return recorder["inputs"]


def test_recorder_never_subscribes_to_rung_forbidden_bridge_topics(tmp_path):
    """Issue #128 question 2: the recorder auto-subscribes to every DECLARED
    output — including one the graph declares but the rung forbids the
    bridge from ever publishing (the #128 repro shape). Filtering by rung
    makes an L1 trace provable on its own terms: no poses endpoint exists
    in the artifact at all."""
    doc = yaml.safe_load((REPO_ROOT / "graphs" / "expert_t1.yaml").read_text())
    bridge = next(n for n in doc["nodes"] if n["id"] == "dora-genesis")
    bridge["outputs"].append("poses")  # declared, unrouted, never published at L1
    graph = tmp_path / "expert_t1_declared_poses.yaml"
    # keep node paths resolvable exactly as graphs/ layout expects
    for node in doc["nodes"]:
        node["path"] = str((REPO_ROOT / "graphs" / node["path"]).resolve())
    graph.write_text(yaml.safe_dump(doc, sort_keys=False))

    inputs = _recorder_inputs(graph, tmp_path)
    assert "dora-genesis__poses" not in inputs
    assert "dora-genesis__seg_overhead" in inputs  # the rung's own topic stays


def test_recorder_still_subscribes_to_poses_at_l0(tmp_path):
    """The filter is the RUNG's, not a blanket ban: at L0 `poses` is the
    sanctioned pose source and its trace endpoint is evidence, not leakage."""
    inputs = _recorder_inputs(REPO_ROOT / "graphs" / "expert_t0.yaml", tmp_path)
    assert "dora-genesis__poses" in inputs
