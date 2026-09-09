"""MON-12: actual bridge rendering contributes retained operation evidence."""

import importlib.util
import shutil

import pytest

pytestmark = [
    pytest.mark.graph,
    pytest.mark.sim,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None or shutil.which("dora") is None,
        reason="sim extra or Dora CLI not installed",
    ),
]


def test_real_bridge_records_render_work_after_reset(tmp_path, dataflow):
    """MON-12/TC-6: real reset, camera outputs and steps retain distinct work counts."""
    from aisle.harness.simulator_work import summarize_work

    output = tmp_path / "records.jsonl"
    journal = tmp_path / "work.jsonl"
    graph = dataflow.write(
        tmp_path,
        output,
        bridge_env={
            "AISLE_SIM_WORK_PATH": str(journal),
            "AISLE_SIM_WORK_RUN_ID": "render-integration",
            "AISLE_SIM_WORK_LAUNCH": "0",
        },
        driver_env={"DRIVER_MODE": "reset", "DRIVER_RESET_SEEDS": "7"},
        driver_waits_for_bridge_info=True,
        step_without_reset=False,
        duration_s=3,
        recorder_await="rgb_wrist:1",
        recorder_await_tail_s=3,
        recorder_await_sim_ns=100_000_000,
    )
    dataflow.run_until_settled(graph, output, deadline_s=420)
    records = dataflow.read(output)
    assert any(row["id"] == "reset_done" for row in records)
    assert any(row["id"] == "rgb_overhead" for row in records)
    assert any(row["id"] == "rgb_wrist" for row in records)
    report = summarize_work(journal, run_id="render-integration", launch=0)
    assert report["schema_version"] == "aisle.simulator-work-journal.v3"
    assert report["completed"]["build"] == 1
    assert report["completed"]["reset"] >= 1
    assert report["completed"]["render"] > 0
    assert report["completed"]["step"] > 0
    assert report["operation_wall_ns"]["render"] > 0
    assert report["completed_env_steps"] == report["completed"]["physics_step"]
    assert report["completed"]["physics_step"] >= report["completed"]["step"] + 1
    assert len(report["physics_source_sha256"]) == 64
    # The fixture intentionally stops the live bridge after capture, so its
    # retained prefix proves completed calls, not complete producer coverage.
