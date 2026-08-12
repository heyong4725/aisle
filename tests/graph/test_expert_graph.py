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


def _run_expert_graph(
    tmp_path, graph_name: str, env_overrides: dict, record_topics: dict | None = None
) -> tuple:
    """One seeded episode through a graphs/ file verbatim, from a TEMP COPY
    with absolutized node paths: dora spawns nodes with cwd = the yaml's
    directory, and the orphan reaper is scoped by that cwd — running from
    the shared graphs/ dir would let cleanup SIGKILL unrelated developer
    runs (PR #10 review).

    `record_topics` ({input_name: "producer/output"}) taps those topics with
    the trace-recorder fixture into tmp_path/'trace.jsonl'. dora run does
    NOT forward node stderr to this process, so an assertion about what a
    node did inside the run has to travel on a topic (PR #176 review)."""
    import yaml as yaml_module

    graph_doc = yaml_module.safe_load((REPO_ROOT / "graphs" / graph_name).read_text())
    if record_topics:
        graph_doc["nodes"].append(
            {
                "id": "trace-recorder",
                "path": str((REPO_ROOT / "tests" / "fixtures" / "nodes" / "recorder.py").resolve()),
                "inputs": {
                    name: {"source": source, "queue_size": 400}
                    for name, source in record_topics.items()
                },
                "env": {
                    "RECORDER_OUT": str(tmp_path / "trace.jsonl"),
                    # outlive the episode: the recorder is torn down with the
                    # dataflow, and its output is line-buffered, so every row
                    # written before teardown survives
                    "RECORDER_DURATION_S": "3600",
                },
            }
        )
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


def test_expert_t2_episode_closes_without_wrong_object(tmp_path):
    """CON-5 layer (d), T2 end-to-end safety smoke (design doc §3 tier
    table; idea I13, closed `up`):
    the seeded episode runs through graphs/expert_t2.yaml verbatim — the
    scene renders label textures with COLORS PERMUTED across meds
    (no-color-prior), so detected-pose's color-worded identity is noise
    and only positions survive; the state machine tours candidates with
    read_move/move_done, ocr-label reads each parked face under the
    pre-registered margin floor, and promotes a candidate only after a
    matching read. The oracle verifier (sim identity, color-blind) is the
    judge, so a color-prior shortcut CANNOT pass this safety test by luck
    at a shuffled seed.

    The generous timeout covers the tour: up to five read poses plus
    one OWLv2 query (~2 s) each, before the ordinary grasp. Full-episode
    success is deliberately NOT asserted here: CON-5 classifies outcomes
    as statistical, and the measured T2 baseline is 2/25 (analysis/t2),
    so one seed is not a valid success-rate gate. This live test pins the
    asymmetric safety invariant; deterministic scan mechanics and frame
    freshness are covered by unit tests, while the committed curve is the
    multi-episode performance evidence."""
    results, stderr = _run_expert_graph(
        tmp_path,
        "expert_t2.yaml",
        {"AISLE_SEEDS": "3", "AISLE_TARGET_MEDS": "cetirizine", "AISLE_TIMEOUT_S": "150"},
        record_topics={
            "move_done": "ik-trajectory/move_done",
            "read_result": "ocr-label/read_result",
        },
    )

    assert results.exists(), f"no results written; stderr tail: {(stderr or '')[-3000:]}"
    records = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
    assert len(records) == 1, (records, (stderr or "")[-2000:])
    record = records[0]
    assert record["status"] in {"success", "fail"}, record
    assert record.get("failure") in {None, "never_grasped", "collision", "timeout", "dropped"}, (
        record
    )
    assert (record["status"] == "success") == (record["failure"] is None), record

    # The MECHANISM assertion (PR #176 review). Dropping the success gate
    # is defensible under CON-5 layer (d), but on its own it would leave
    # the determinism fix unproven: this test then passes on almost any
    # outcome, including the never_grasped signature issue #153 reports as
    # the FAILURE mode. So assert the barrier itself, live: every read must
    # have answered from a wrist frame STRICTLY newer than its own park,
    # and at least one park must have armed a barrier (a silently
    # unbarriered tour is the regression this pins).
    rows = [
        json.loads(line)
        for line in (tmp_path / "trace.jsonl").read_text().splitlines()
        if line.strip()
    ]
    # Pair each read with the park that armed it IN ORDER: the tour reuses
    # request_ids across passes (a re-toured candidate is `read0.0` again),
    # so keying a dict by request_id would compare one pass's read against
    # another pass's barrier.
    armed, checked, stale = {}, 0, []
    for row in rows:
        rid = row["metadata"].get("request_id")
        if row["id"] == "move_done":
            barrier = json.loads(row["text"]).get("frame_after_sim_time_ns")
            if barrier is not None:
                armed[rid] = int(barrier)
        elif row["id"] == "read_result" and rid in armed:
            barrier = armed.pop(rid)
            frame = row["metadata"].get("sim_time_ns")
            if frame is None:  # a refusal carries no frame stamp
                continue
            checked += 1
            if int(frame) <= barrier:
                stale.append((rid, barrier, int(frame)))
    assert checked, f"no barriered read completed in the episode; records={records}"
    assert not stale, f"read(s) answered from a frame at or before their park: {stale}"
