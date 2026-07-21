"""SPEC 070 rollout metrics and instrumentation (HAR-1, HAR-3, HAR-4) —
pure pieces, no dora, no sim (CON-12)."""

from pathlib import Path

import pytest
import yaml

from aisle.harness.rollout import compute_metrics, instrumented_graph, parse_seed_range

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def episode(status, failure=None, retries=0):
    return {"status": status, "failure": failure, "retries": retries, "t_end": 10.0}


def test_pass8_is_in_context_retries_never_best_of_8():
    """HAR-3: pass@8 counts an episode that succeeded within <=8 IN-CONTEXT
    retries; a first-attempt success counts toward both; a success after
    retries counts toward pass8 only. It is NEVER best-of-8 independent
    episodes: the metric is per-episode, so N episodes contribute exactly
    N samples to both denominators."""
    episodes = [
        episode("success"),  # pass1 and pass8
        episode("success", retries=3),  # pass8 only
        episode("fail", "timeout"),
        episode("fail", "dropped", retries=8),
    ]
    metrics = compute_metrics(episodes)
    assert metrics["pass1"] == pytest.approx(1 / 4)
    assert metrics["pass8"] == pytest.approx(2 / 4)
    assert metrics["failures"] == {"timeout": 1, "dropped": 1}


def test_failure_histogram_covers_ver3_classes():
    episodes = [episode("fail", c) for c in ("wrong_object", "dropped", "timeout")]
    assert compute_metrics(episodes)["failures"] == {
        "wrong_object": 1,
        "dropped": 1,
        "timeout": 1,
    }


def test_seed_range_forms():
    assert parse_seed_range("0..3") == [0, 1, 2, 3]
    assert parse_seed_range("7") == [7]
    assert parse_seed_range("1,4,9") == [1, 4, 9]


def test_instrumented_graph_adds_recorder_and_absolutizes(tmp_path):
    """HAR-4: the executable copy gains a trace-recorder wired to every
    traceable topic that exists in the graph, node paths are absolute
    (dora cwd = the run dir), and the ORIGINAL graph file is untouched."""
    original = (REPO_ROOT / "graphs" / "expert_t0.yaml").read_text()
    out = instrumented_graph(REPO_ROOT / "graphs" / "expert_t0.yaml", REPO_ROOT, tmp_path)
    doc = yaml.safe_load(out.read_text())
    recorder = next(n for n in doc["nodes"] if n["id"] == "trace-recorder")
    sources = {port: spec["source"] for port, spec in recorder["inputs"].items()}
    assert sources["joint_state"] == "dora-genesis/joint_state"
    assert sources["oracle_state"] == "dora-genesis/oracle_state"
    assert "rgb_overhead" in sources
    for node in doc["nodes"]:
        assert Path(node["path"]).is_absolute()
    assert (REPO_ROOT / "graphs" / "expert_t0.yaml").read_text() == original


def test_rollout_refuses_unsafe_or_reused_run_ids(tmp_path):
    """PR #11 review: a traversal-shaped run_id must never touch paths
    outside runs/, and an existing run must never be overwritten. Also:
    non-T0 tiers refuse rather than run mislabeled."""
    from aisle.harness.rollout import rollout

    common = dict(
        root=tmp_path,
        graph=REPO_ROOT / "graphs" / "expert_t0.yaml",
        episodes=1,
        seeds=[0],
        reset_mode="teleport",
        verifier="oracle",
        branch="b",
        no_idea_gate=True,
    )
    bad = rollout(tier="T0", run_id="../escape", **common)
    assert bad["ok"] is False and "unsafe run_id" in bad["error"]
    (tmp_path / "runs" / "taken").mkdir(parents=True)
    reused = rollout(tier="T0", run_id="taken", **common)
    assert reused["ok"] is False and "already exists" in reused["error"]
    tiered = rollout(tier="T1", run_id="fresh", **common)
    assert tiered["ok"] is False and "Phase 2" in tiered["error"]
