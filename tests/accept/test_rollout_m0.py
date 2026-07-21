"""SPEC 070 acceptance: the rollout runner end-to-end (HAR-1..4). The
two-episode smoke is T09's live proof of the runner itself; the SPEC 090
M0 gates live in tests/accept/test_m0_gate.py."""

import importlib.util
import json
import shutil

import pytest
from accept_helpers import REPO_ROOT, run_harness

pytestmark = [
    pytest.mark.accept,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None or shutil.which("dora") is None,
        reason="sim extra or dora CLI not installed",
    ),
]


def test_rollout_two_episodes_end_to_end(tmp_path):
    """HAR-1, HAR-2, HAR-4 live: `harness rollout` gates on a real OPEN
    idea, drives two seeded episodes through graphs/expert_t0.yaml, and
    writes runs/<id>/ with the manifest, per-episode records, Arrow
    traces, and the overhead video."""
    code, logged = run_harness(
        "report", "log", "--idea", "T09 acceptance smoke", "--expect", "runner works", timeout=60
    )
    assert code == 0, logged
    run_id = f"accept-smoke-{logged['id']}"
    code, report = run_harness(
        "rollout",
        "--graph",
        str(REPO_ROOT / "graphs" / "expert_t0.yaml"),
        "--tier",
        "T0",
        "--episodes",
        "2",
        "--seeds",
        "3..4",
        "--reset",
        "teleport",
        "--run-id",
        run_id,
        timeout=1500,
    )
    run_dir = REPO_ROOT / "runs" / run_id
    try:
        assert code == 0, report
        assert report["ok"] is True and len(report["episodes"]) == 2
        for key in ("pass1", "pass8", "failures", "traces_dir", "videos", "durations"):
            assert key in report, key
        manifest = json.loads((run_dir / "manifest.json").read_text())
        for key in ("run_id", "git_sha", "env_hash", "platform", "graph_hash", "idea"):
            assert manifest[key], key
        assert manifest["no_idea_gate"] is False  # the REAL gate was exercised
        traces = {p.name for p in (run_dir / "traces").iterdir()}
        # endpoint-qualified names: <producer>__<topic>.arrow (PR #11)
        assert "dora-genesis__joint_state.arrow" in traces
        assert "dora-genesis__oracle_state.arrow" in traces
        assert "verifier-oracle__episode_result.arrow" in traces  # text payloads
        assert "dora-genesis__reset_done.arrow" in traces
        assert "reset__reset_done.arrow" in traces  # BOTH producers kept
        assert "overhead.mp4" in traces  # HAR-4 video
        # the traces query CLI reads what the run recorded (HAR-6)
        code, stats = run_harness(
            "traces",
            "query",
            "--run",
            run_id,
            "--topic",
            "joint_state",
            "--summarize",
            timeout=120,
        )
        assert code == 0 and stats["n"] > 0 and stats["rate_hz"] > 0
    finally:
        code, _ = run_harness(
            "report",
            "close",
            "--id",
            logged["id"],
            "--observed",
            "smoke complete",
            "--verdict",
            "flat",
            timeout=60,
        )
        shutil.rmtree(run_dir, ignore_errors=True)
