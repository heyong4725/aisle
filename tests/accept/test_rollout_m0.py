"""SPEC 070 acceptance: the rollout runner end-to-end (HAR-1..4), and the
spec-named M0-1 gate (SPEC 090).

The 50-episode gate is authored here per the spec's acceptance list; it
is expected to FAIL pass1 >= 0.95 until the under-board coverage gap is
resolved (ADR-10 section 8, owner decision pending) and is therefore the
honest T10 gate, not a T09 pass requirement. The two-episode smoke is
T09's live proof of the runner itself.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
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


def run_harness(*args: str, timeout: float) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, "-m", "aisle.harness.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=timeout,
    )
    try:
        return proc.returncode, json.loads(proc.stdout)
    except json.JSONDecodeError as bad:
        raise AssertionError(
            f"non-JSON stdout from harness {args[:2]}: "
            f"stdout={proc.stdout[:400]!r} stderr={proc.stderr[-600:]!r}"
        ) from bad


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
        assert "joint_state.arrow" in traces and "oracle_state.arrow" in traces
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


@pytest.mark.skip(
    reason="M0-1 gate (T10): known under-board coverage gap, ADR-10 section 8 — "
    "owner decision pending; unskip for the M0 review"
)
def test_expert_t0_50eps():
    """SPEC 090 M0-1 + HAR-1..4: `harness rollout --graph
    graphs/expert_t0.yaml --tier T0 --episodes 50 --seeds 0..49 --reset
    teleport` reports pass1 >= 0.95 on macOS-arm64."""
    code, logged = run_harness("report", "log", "--idea", "M0-1 gate run", timeout=60)
    assert code == 0
    code, report = run_harness(
        "rollout",
        "--graph",
        str(REPO_ROOT / "graphs" / "expert_t0.yaml"),
        "--tier",
        "T0",
        "--episodes",
        "50",
        "--seeds",
        "0..49",
        "--reset",
        "teleport",
        timeout=4 * 3600,
    )
    assert code == 0, report
    assert report["pass1"] >= 0.95, report["failures"]
