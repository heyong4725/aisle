"""MON-12/CSE-4: simulator operation receipts preserve incomplete work explicitly."""

import json

import pytest

pytestmark = pytest.mark.unit


def test_work_journal_counts_resets_and_steps_without_episode_clocks(tmp_path):
    """MON-12/CSE-4: work records count physical calls independently of outcome times."""
    from aisle.harness.simulator_work import WorkJournal, summarize_work

    path = tmp_path / "work.jsonl"
    ticks = iter([10, 20, 30, 60, 70, 110])
    with WorkJournal(
        path, run_id="run", launch=0, dt_ns=4, n_envs=2, clock=lambda: next(ticks)
    ) as work:
        assert work.call("build", lambda: "built") == "built"
        work.call("reset", lambda: None)
        work.call("step", lambda: None)
    report = summarize_work(path, run_id="run", launch=0)
    assert report["journal_complete"]
    assert report["attempted"] == {"build": 1, "reset": 1, "step": 1, "render": 0}
    assert report["completed_env_steps"] == 2
    assert report["completed_env_sim_ns"] == 8
    assert report["operation_wall_ns"] == {"build": 10, "reset": 30, "step": 40, "render": 0}


def test_failed_step_preserves_attempt_but_not_recorded_step_work_exact(tmp_path):
    """MON-12/CSE-4: a native step failure cannot be treated as zero physical work."""
    from aisle.harness.simulator_work import WorkJournal, summarize_work

    path = tmp_path / "work.jsonl"

    def fail():
        raise RuntimeError("physics failure")

    with pytest.raises(RuntimeError, match="physics failure"):
        with WorkJournal(path, run_id="run", launch=0, dt_ns=4, n_envs=1) as work:
            work.call("step", fail)
    report = summarize_work(path, run_id="run", launch=0)
    assert report["journal_complete"]
    assert report["attempted"]["step"] == report["failed"]["step"] == 1
    assert report["completed_env_sim_ns"] == 0
    assert report["recorded_step_work_exact"] is False


@pytest.mark.parametrize("fault", ["missing_terminal", "partial_line", "wrong_launch", "reordered"])
def test_incomplete_or_foreign_journal_cannot_certify_work(tmp_path, fault):
    """MON-13/CSE-4: truncation, identity drift and event reordering remain unverified."""
    from aisle.harness.simulator_work import WorkJournal, summarize_work

    path = tmp_path / "work.jsonl"
    with WorkJournal(path, run_id="run", launch=0, dt_ns=4, n_envs=1) as work:
        work.call("step", lambda: None)
    lines = path.read_bytes().splitlines(keepends=True)
    if fault == "missing_terminal":
        lines.pop()
    elif fault == "partial_line":
        lines[-1] = lines[-1][:-2]
    elif fault == "reordered":
        lines[1], lines[2] = lines[2], lines[1]
    else:
        header = json.loads(lines[0])
        header["launch"] = 1
        lines[0] = (json.dumps(header) + "\n").encode()
    path.write_bytes(b"".join(lines))
    report = summarize_work(path, run_id="run", launch=0)
    assert not report["journal_complete"]
    assert not report["recorded_step_work_exact"]


def test_abrupt_process_exit_retains_started_step_without_inventing_completion(tmp_path):
    """MON-12/MON-13: a killed simulator leaves an attempted step and unknown total work."""
    import subprocess
    import sys

    from aisle.harness.simulator_work import summarize_work

    path = tmp_path / "work.jsonl"
    code = (
        "import os,sys; from aisle.harness.simulator_work import WorkJournal; "
        "work=WorkJournal(sys.argv[1],run_id='run',launch=0,dt_ns=4,n_envs=1); "
        "work.call('step',lambda:os._exit(23))"
    )
    result = subprocess.run([sys.executable, "-c", code, str(path)], timeout=10, check=False)
    assert result.returncode == 23
    report = summarize_work(path, run_id="run", launch=0)
    assert report["attempted"]["step"] == 1
    assert report["completed"]["step"] == 0
    assert report["failed"]["step"] == 0
    assert not report["journal_complete"]
    assert not report["recorded_step_work_exact"]


@pytest.mark.parametrize(
    "field", ["AISLE_SIM_WORK_PATH", "AISLE_SIM_WORK_RUN_ID", "AISLE_SIM_WORK_LAUNCH"]
)
def test_partial_bridge_work_binding_refuses_execution(tmp_path, field):
    """MON-13: partial accounting configuration cannot silently select unrecorded execution."""
    from aisle.harness.simulator_work import work_context

    env = {
        "AISLE_SIM_WORK_PATH": str(tmp_path / "work.jsonl"),
        "AISLE_SIM_WORK_RUN_ID": "run",
        "AISLE_SIM_WORK_LAUNCH": "0",
    }
    del env[field]
    with pytest.raises(ValueError, match="complete"):
        work_context(env, dt_ns=4, n_envs=1)


def test_bridge_work_context_binds_actual_units_and_preserves_legacy_calls(tmp_path):
    """MON-12: explicit journal selection binds runtime units; absent selection preserves calls."""
    from aisle.harness.simulator_work import summarize_work, work_context

    with work_context({}, dt_ns=4, n_envs=1) as work:
        assert work.call("step", lambda value: value, "legacy") == "legacy"
    path = tmp_path / "work.jsonl"
    with work_context(
        {
            "AISLE_SIM_WORK_PATH": str(path),
            "AISLE_SIM_WORK_RUN_ID": "run",
            "AISLE_SIM_WORK_LAUNCH": "2",
        },
        dt_ns=8,
        n_envs=3,
    ) as work:
        work.call("step", lambda: None)
    result = summarize_work(path, run_id="run", launch=2)
    assert result["journal_complete"]
    assert result["completed_env_sim_ns"] == 24


def test_reset_scope_covers_failure_after_state_injection(tmp_path):
    """MON-12: later reset failure cannot be recorded as a completed reset operation."""
    from aisle.harness.simulator_work import WorkJournal, summarize_work

    path = tmp_path / "work.jsonl"
    injected = []
    with pytest.raises(RuntimeError, match="reset publication"):
        with WorkJournal(path, run_id="run", launch=0, dt_ns=4, n_envs=1) as work:
            with work.operation("reset"):
                injected.append(True)
                raise RuntimeError("reset publication")
    assert injected == [True]
    result = summarize_work(path, run_id="run", launch=0)
    assert result["failed"]["reset"] == 1
    assert result["completed"]["reset"] == 0


def test_launch_receipt_precedes_simulator_and_survives_missing_journal(tmp_path):
    """MON-12/CSE-4: failure before bridge startup is an expected, missing producer."""
    from aisle.harness.simulator_work import reserve_launch, summarize_launches

    binding = reserve_launch(tmp_path, run_id="run", launch=0, graph_sha256="a" * 64)
    assert binding["AISLE_SIM_WORK_LAUNCH"] == "0"
    assert binding["AISLE_SIM_WORK_RUN_ID"] == "run"
    assert json.loads((tmp_path / "launch-0.expected.json").read_text())["graph_sha256"] == "a" * 64
    result = summarize_launches(tmp_path, run_id="run", expected_graph_hashes=["a" * 64])
    assert not result["journals_complete"]
    assert not result["recorded_step_work_exact"]
    assert result["completed_env_sim_ns"] == 0
    assert result["launches"][0]["error"]
    with pytest.raises(FileExistsError):
        reserve_launch(tmp_path, run_id="run", launch=0, graph_sha256="a" * 64)


def test_relaunch_work_aggregates_by_bound_units(tmp_path):
    """MON-12/CSE-4: relaunches retain each completed step using that launch's units."""
    from aisle.harness.simulator_work import reserve_launch, summarize_launches, work_context

    hashes = ["a" * 64, "b" * 64]
    for index, digest in enumerate(hashes):
        binding = reserve_launch(tmp_path, run_id="run", launch=index, graph_sha256=digest)
        with work_context(binding, dt_ns=4 + index, n_envs=2) as work:
            work.call("step", lambda: None)
    result = summarize_launches(tmp_path, run_id="run", expected_graph_hashes=hashes)
    assert result["journals_complete"]
    assert result["recorded_step_work_exact"]
    assert result["completed_env_steps"] == 4
    assert result["completed_env_sim_ns"] == 18
    assert result["producer_coverage_complete"] is False


@pytest.mark.parametrize(
    "fault", ["missing_receipt", "wrong_graph", "boolean_launch", "extra_journal", "truncated"]
)
def test_launch_inventory_drift_never_certifies_recorded_work(tmp_path, fault):
    """MON-13: controller expectations detect missing, substituted and extra launch records."""
    from aisle.harness.simulator_work import reserve_launch, summarize_launches, work_context

    binding = reserve_launch(tmp_path, run_id="run", launch=0, graph_sha256="a" * 64)
    with work_context(binding, dt_ns=4, n_envs=1) as work:
        work.call("step", lambda: None)
    receipt = tmp_path / "launch-0.expected.json"
    if fault == "missing_receipt":
        receipt.unlink()
    elif fault == "wrong_graph":
        value = json.loads(receipt.read_text())
        value["graph_sha256"] = "b" * 64
        receipt.write_text(json.dumps(value))
    elif fault == "boolean_launch":
        value = json.loads(receipt.read_text())
        value["launch"] = False
        receipt.write_text(json.dumps(value))
    elif fault == "extra_journal":
        (tmp_path / "launch-1.jsonl").write_text("")
    else:
        journal = tmp_path / "launch-0.jsonl"
        journal.write_bytes(b"\n".join(journal.read_bytes().splitlines()[:-1]) + b"\n")
    result = summarize_launches(tmp_path, run_id="run", expected_graph_hashes=["a" * 64])
    assert not result["journals_complete"]
    assert not result["recorded_step_work_exact"]
    if fault == "truncated":
        assert result["completed_env_sim_ns"] == 4


def test_binding_can_be_hashed_before_durable_reservation(tmp_path):
    """MON-13: graph construction can bind the journal before the graph digest is known."""
    from aisle.harness.simulator_work import launch_binding, reserve_launch

    expected = launch_binding(tmp_path, run_id="run", launch=2)
    assert list(tmp_path.iterdir()) == []
    assert reserve_launch(tmp_path, run_id="run", launch=2, graph_sha256="a" * 64) == expected


def test_accounting_environment_is_never_inherited_from_ambient_state():
    """MON-13: only the controller's hashed graph may select a simulator journal."""
    from aisle.harness.rollout import scrub_bringup_env

    environment = {
        "AISLE_SIM_WORK_PATH": "/untrusted/journal",
        "AISLE_SIM_WORK_RUN_ID": "foreign",
        "AISLE_SIM_WORK_LAUNCH": "42",
        "PATH": "/runtime/bin",
    }
    assert scrub_bringup_env(environment) == {"PATH": "/runtime/bin"}
