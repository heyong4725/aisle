"""SPEC 200 acceptance: the scripted S1 expert completes a fixed-seed
order-picking episode end-to-end (the retail suite's integration gate,
analogous to M0-1). Marker `accept`: full dora graph with genesis."""

import importlib.util
import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.accept,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None or shutil.which("dora") is None,
        reason="sim extra or dora CLI not installed",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_scripted_order_pick(tmp_path):
    """RS-6/RS-7 (integration): graphs/expert_s1.yaml verbatim — store
    bridge, guard (arm+base), waypoint-nav, order-reader, task-planner,
    s1-expert, verifier-retail, rollout --tier S1 — completes the seed-1
    order (1 amoxicillin + 1 omeprazole, both L1-sourceable, ADR-18) with
    a success episode_result carrying the RS-6 fields."""
    import yaml as yaml_module

    graph_doc = yaml_module.safe_load((REPO_ROOT / "graphs" / "expert_s1.yaml").read_text())
    for node in graph_doc["nodes"]:
        node["path"] = str((REPO_ROOT / "graphs" / node["path"]).resolve())
    graph_path = tmp_path / "expert_s1.yaml"
    graph_path.write_text(yaml_module.safe_dump(graph_doc, sort_keys=False))

    results = tmp_path / "results.jsonl"
    env = {
        **os.environ,
        "AISLE_SEEDS": "1",
        "AISLE_TIMEOUT_S": "600",
        "AISLE_RESULTS": str(results),
    }
    proc = subprocess.Popen(
        ["dora", "run", str(graph_path), "--uv"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=780)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            stdout, stderr = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate()
    finally:
        from conftest import _reap_orphan_nodes

        _reap_orphan_nodes(tmp_path)

    assert results.exists(), f"no results written; stderr tail: {(stderr or '')[-3000:]}"
    records = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    assert len(records) == 1, (records, (stderr or "")[-2000:])
    record = records[0]
    assert record["status"] == "success", (record, (stderr or "")[-3000:])
    # RS-6: the episode record carries the retail scoring fields
    assert record["success"] is True
    assert record["penalties"] == []
    assert record["verifier"] == "retail-oracle"
    assert record["seed"] == 1
