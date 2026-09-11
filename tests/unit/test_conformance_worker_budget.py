"""MON-8/MON-13: prepared workers survive the declared engineering rollout budget."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "accept"))
pytestmark = pytest.mark.unit


@pytest.mark.parametrize("seeds", [[7], [7, 8]])
def test_engineering_budget_retains_rollout_and_audit_time_after_preparation(seeds):
    """MON-8/MON-12: controller preparation cannot consume the rollout and evidence allowance."""
    from conformance_run_fixture import run_budget_limits

    limits = run_budget_limits({"tier": "T1", "verifier": "oracle", "seeds": seeds})
    # The retained typed run0014 reached controller launch after 690.11 seconds
    # of its 1170-second tool budget. Its successful episode then lost shutdown
    # and collection to the enclosing deadline. Replay that preparation cost
    # against the full declared rollout, leaving time for final runtime audits
    # and indexed RPC retention rather than assuming the episode ends early.
    preparation_s = 690.11
    remaining = limits["tool_wall_ceiling_s"] - preparation_s - limits["timeout_s"]
    assert remaining >= 600
    assert limits["wall_ceiling_s"] - limits["tool_wall_ceiling_s"] >= 600


@pytest.mark.parametrize("seeds", [[7], [7, 8]])
def test_worker_declarations_cover_the_complete_rollout(tmp_path, seeds):
    """MON-8/MON-13: every arm's worker budget covers build and all admitted episodes."""
    from conformance_run_fixture import prepare_worker_declarations, run_budget_limits

    from aisle.harness.matched_runtime import capture_runtime

    controller = tmp_path / "controller"
    controller.mkdir()
    views = {arm: tmp_path / arm for arm in ("typed", "monolithic")}
    for view in views.values():
        view.mkdir()
    retained = tmp_path / "retained"
    retained.mkdir()
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    python = runtime_root / "python"
    python.write_text("#!/bin/sh\nexit 1\n")
    python.chmod(0o755)
    runtime = capture_runtime([runtime_root])
    adapter = tmp_path / "synthetic-adapter"
    adapter.write_text('#!/bin/sh\nshift 2\nexec "$@"\n')
    adapter.chmod(0o755)
    development = {"tier": "T1", "verifier": "oracle", "seeds": seeds}
    limits = run_budget_limits(development)
    declarations = prepare_worker_declarations(
        tmp_path / "workers",
        controller=controller,
        views=views,
        evidence_root=retained,
        snapshot_storage=snapshots,
        runtime=runtime,
        python=python,
        adapter=adapter,
        development=development,
    )
    workers = [*declarations["typed"][0].values(), *declarations["monolithic"]]
    assert len(workers) == 5
    assert all(row["timeout_s"] == limits["timeout_s"] for row in workers)
    assert all(row["timeout_s"] < limits["tool_wall_ceiling_s"] for row in workers)
