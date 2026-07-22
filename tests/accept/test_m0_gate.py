"""SPEC 090 acceptance: the M0 gate.

M0-1/M0-2 share one module-scoped 50-episode rollout (the spec's M0-2 is
"re-running M0-1", so the second full run happens inside the M0-2 test).
The run directories are kept under runs/ as evidence for the ADR-M0
sign-off (M0-6). M0-4 is enforced by tools/trace_check.py --strict in CI;
M0-6 is a human act, not a test.
"""

import importlib.util
import shutil
import subprocess
import sys
import uuid

import pytest
from accept_helpers import REPO_ROOT, run_harness

pytestmark = [
    pytest.mark.accept,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None or shutil.which("dora") is None,
        reason="sim extra or dora CLI not installed",
    ),
]

GRAPH = REPO_ROOT / "graphs" / "expert_t0.yaml"
THRESHOLDS = REPO_ROOT / "src" / "aisle" / "verifier" / "thresholds.toml"
SO101_URDF = REPO_ROOT / "assets" / "so101" / "so101.urdf"


def rollout_50(run_id: str, timeout: float = 4 * 3600) -> dict:
    """One spec-literal M0-1 invocation, bracketed by an ideas-log entry
    so the HAR-2 gate is exercised for real."""
    code, logged = run_harness("report", "log", "--idea", f"M0 gate run {run_id}", timeout=60)
    assert code == 0, logged
    try:
        code, report = run_harness(
            "rollout",
            "--graph",
            str(GRAPH),
            "--tier",
            "T0",
            "--episodes",
            "50",
            "--seeds",
            "0..49",
            "--reset",
            "teleport",
            "--run-id",
            run_id,
            timeout=timeout,
        )
        assert code == 0, report
        assert report["ok"] is True and len(report["episodes"]) == 50
        return report
    finally:
        run_harness(
            "report",
            "close",
            "--id",
            logged["id"],
            "--observed",
            f"m0 run {run_id} finished",
            "--verdict",
            "flat",
            timeout=60,
        )


@pytest.fixture(scope="module")
def m0_first_run() -> dict:
    return rollout_50(f"m0-1-{uuid.uuid4().hex[:6]}")


def test_m0_1_pass1_at_least_95(m0_first_run):
    """SPEC 090 M0-1 (HAR-1, SCN-3, BRG-2, VER-4, TC-2): 50 episodes over
    seeds 0..49 on graphs/expert_t0.yaml report pass1 >= 0.95 on
    macOS-arm64."""
    assert m0_first_run["pass1"] >= 0.95, m0_first_run["failures"]


def test_m0_2_rerun_reproduces_status_vector(m0_first_run):
    """SPEC 090 M0-2 (CON-5): re-running M0-1 with identical seeds
    reproduces the identical per-episode status vector."""
    rerun = rollout_50(f"m0-2-{uuid.uuid4().hex[:6]}")
    first = [(e["seed"], e["status"]) for e in m0_first_run["episodes"]]
    second = [(e["seed"], e["status"]) for e in rerun["episodes"]]
    assert first == second


def test_m0_3_mutated_frozen_file_refuses_rollout():
    """SPEC 090 M0-3 (CON-7, HAR-2): tools/env_hash.py --check passes on
    the committed hash, and a single mutated byte in
    verifier/thresholds.toml makes `harness rollout` refuse at the
    env-hash gate. The file is restored byte-for-byte afterwards."""
    check = subprocess.run(
        [sys.executable, "tools/env_hash.py", "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert check.returncode == 0, check.stdout + check.stderr

    original = THRESHOLDS.read_bytes()
    try:
        THRESHOLDS.write_bytes(original + b"# mutated byte\n")
        code, report = run_harness(
            "rollout",
            "--graph",
            str(GRAPH),
            "--tier",
            "T0",
            "--episodes",
            "1",
            "--seeds",
            "0",
            "--reset",
            "teleport",
            "--no-idea-gate",
            timeout=300,
        )
        assert code != 0
        assert report["ok"] is False
        assert report["refused"]["gate"] == "env_hash", report
    finally:
        THRESHOLDS.write_bytes(original)


def _so101_ready() -> bool:
    """M0-5 has two independent blockers: the asset (ADR-6) and so101
    support in the motion nodes (ik-trajectory is Panda-only; its manifest
    is the mechanical record). Assets landing alone must not fire a
    doomed 50-episode run."""
    manifest = (REPO_ROOT / "registry" / "manifests" / "ik-trajectory.yaml").read_text()
    return SO101_URDF.exists() and "so101" in manifest


@pytest.mark.skipif(
    not _so101_ready(),
    reason="so101 blocked: asset acquisition (ADR-6) and/or ik-trajectory so101 "
    "support (manifest is franka-only); the HAR-2 gate refuses the swap "
    "(EMBODIMENT_MISMATCH) until both land",
)
def test_m0_5_so101_profile_swap_pass1_at_least_80():
    """SPEC 090 M0-5 (TC-5, SCN-4): the same T0 graph with --embodiment
    so101 (profile swap only, zero YAML edits) reaches pass1 >= 0.80."""
    run_id = f"m0-5-{uuid.uuid4().hex[:6]}"
    code, logged = run_harness("report", "log", "--idea", f"M0-5 so101 {run_id}", timeout=60)
    assert code == 0, logged
    try:
        code, report = run_harness(
            "rollout",
            "--graph",
            str(GRAPH),
            "--tier",
            "T0",
            "--embodiment",
            "so101",
            "--episodes",
            "50",
            "--seeds",
            "0..49",
            "--reset",
            "teleport",
            "--run-id",
            run_id,
            timeout=4 * 3600,
        )
        assert code == 0, report
        assert report["pass1"] >= 0.80, report["failures"]
    finally:
        run_harness(
            "report",
            "close",
            "--id",
            logged["id"],
            "--observed",
            "m0-5 finished",
            "--verdict",
            "flat",
            timeout=60,
        )
