"""T08 DoD: the hand-written expert graph passes locally — seeded episodes
through the REAL pipeline (dora-genesis, guard, oracle-pose, grasp
planner, ik-trajectory, verifier, reset service, rollout client) end in
verifier SUCCESS (design doc §8.1.4; M0-1's 50-episode gate lands at
T10)."""

import importlib.util
import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.graph,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None or shutil.which("dora") is None,
        reason="sim extra or dora CLI not installed",
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_expert_graph(tmp_path, graph_name: str, env_overrides: dict) -> tuple:
    """One seeded episode through a graphs/ file verbatim, from a TEMP COPY
    with absolutized node paths: dora spawns nodes with cwd = the yaml's
    directory, and the orphan reaper is scoped by that cwd — running from
    the shared graphs/ dir would let cleanup SIGKILL unrelated developer
    runs (PR #10 review)."""
    import yaml as yaml_module

    graph_doc = yaml_module.safe_load((REPO_ROOT / "graphs" / graph_name).read_text())
    for node in graph_doc["nodes"]:
        node["path"] = str((REPO_ROOT / "graphs" / node["path"]).resolve())
    graph_path = tmp_path / graph_name
    graph_path.write_text(yaml_module.safe_dump(graph_doc, sort_keys=False))

    from aisle.harness.rollout import scrub_bringup_env

    results = tmp_path / "results.jsonl"
    # the graph owns its rung and bring-up settings (TC-9/ADR-25): an ambient
    # AISLE_PERCEPTION=L1 in a dev shell would otherwise pop `poses` out of
    # the T0 bridge and starve oracle-pose into a baffling timeout
    env = {**scrub_bringup_env(dict(os.environ)), "AISLE_RESULTS": str(results), **env_overrides}
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
        stdout, stderr = proc.communicate(timeout=540)
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
    return results, stderr


def test_expert_t0_episodes_succeed(tmp_path):
    """SPEC 090 M0-1 (local slice), VER-2, RST-1, VAL-5/6: a seeded
    top-level episode runs through graphs/expert_t0.yaml verbatim and
    closes with status=success. (Expert v0 covers top-level placements;
    under-board levels are the documented coverage gap for M0-1 — see
    ADR-10 and the T08 PR.)"""
    results, stderr = _run_expert_graph(
        tmp_path,
        "expert_t0.yaml",
        {"AISLE_SEEDS": "3", "AISLE_TARGET_MEDS": "ibuprofen", "AISLE_TIMEOUT_S": "60"},
    )

    assert results.exists(), f"no results written; stderr tail: {(stderr or '')[-3000:]}"
    records = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    assert len(records) == 1, (records, (stderr or "")[-2000:])
    assert records[0]["status"] == "success", (records[0], (stderr or "")[-2000:])


def test_expert_t1_l1_episode_succeeds(tmp_path):
    """TC-9 end-to-end at rung L1: the same seeded episode that passes at
    L0 runs through graphs/expert_t1.yaml verbatim — the bridge publishes
    seg_overhead and NOT poses, segmented-pose estimates the target's pose
    from the seg/depth pair, and the episode closes with status=success.
    This is the one test that exercises the rung gate inside the bridge's
    publish() and the estimated-pose path together in a live dataflow."""
    results, stderr = _run_expert_graph(
        tmp_path,
        "expert_t1.yaml",
        {"AISLE_SEEDS": "3", "AISLE_TARGET_MEDS": "ibuprofen", "AISLE_TIMEOUT_S": "60"},
    )

    assert results.exists(), f"no results written; stderr tail: {(stderr or '')[-3000:]}"
    records = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    assert len(records) == 1, (records, (stderr or "")[-2000:])
    assert records[0]["status"] == "success", (records[0], (stderr or "")[-2000:])


def test_expert_t1_l2_episode_succeeds(tmp_path):
    """TC-9 end-to-end at rung L2 (idea I7): the seeded episode runs through
    graphs/expert_t1_l2.yaml verbatim — the bridge publishes NEITHER
    simulator ground-truth pose nor segmentation, detected-pose identifies the
    target from RGB under the measured identity-margin floor, same-stamp sensor
    depth supplies metric geometry, and the episode closes with status=success.

    The target is cetirizine, never confused in the I7 shelf measurement.
    Ibuprofen is deliberately NOT this test's target: OWLv2 systematically
    scores its box below an overlapping rival (measured margin -0.028 live),
    so the floor refuses every attempt and the episode times out honestly as
    never_grasped — correct floor behavior, pinned by the acceptance run's
    refusal statistics rather than by a single-episode success test."""
    results, stderr = _run_expert_graph(
        tmp_path,
        "expert_t1_l2.yaml",
        {"AISLE_SEEDS": "3", "AISLE_TARGET_MEDS": "cetirizine", "AISLE_TIMEOUT_S": "60"},
    )

    assert results.exists(), f"no results written; stderr tail: {(stderr or '')[-3000:]}"
    records = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    assert len(records) == 1, (records, (stderr or "")[-2000:])
    assert records[0]["status"] == "success", (records[0], (stderr or "")[-2000:])
