"""MON-12/CSE-4: count physical advances independently of public scene calls."""

import hashlib
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_nested_build_step_and_veto_preserve_actual_advances(tmp_path):
    """MON-12/CSE-4: compilation advances count; vetoed public calls do not."""
    from aisle.harness.simulator_work import WorkJournal, summarize_work

    path = tmp_path / "work.jsonl"
    with WorkJournal(
        path,
        run_id="run",
        launch=0,
        dt_ns=4,
        n_envs=2,
        physics_source_sha256="a" * 64,
    ) as work:
        work.call("build", lambda: work.call("physics_step", lambda: None))
        work.call("step", lambda: work.call("physics_step", lambda: None))
        work.call("step", lambda: None)
    result = summarize_work(path, run_id="run", launch=0)
    assert result["journal_complete"]
    assert result["completed_env_steps"] == 4
    assert result["completed_env_sim_ns"] == 16
    assert result["completed"]["physics_step"] == 2
    assert result["completed"]["step"] == 2
    assert result["physics_source_sha256"] == "a" * 64


def test_failed_build_retains_completed_internal_advance(tmp_path):
    """MON-12/CSE-4: failure after compilation cannot erase physical work."""
    from aisle.harness.simulator_work import WorkJournal, summarize_work

    path = tmp_path / "work.jsonl"
    with pytest.raises(RuntimeError, match="build failed"):
        with WorkJournal(
            path,
            run_id="run",
            launch=0,
            dt_ns=4,
            n_envs=1,
            physics_source_sha256="a" * 64,
        ) as work:
            with work.operation("build"):
                work.call("physics_step", lambda: None)
                raise RuntimeError("build failed")
    result = summarize_work(path, run_id="run", launch=0)
    assert result["journal_complete"]
    assert result["completed_env_steps"] == 1
    assert result["failed"]["build"] == 1
    assert result["recorded_step_work_exact"]


def test_failed_internal_advance_remains_inexact(tmp_path):
    """MON-12/CSE-4: partial native work is not reported as exact zero."""
    from aisle.harness.simulator_work import WorkJournal, summarize_work

    path = tmp_path / "work.jsonl"
    with pytest.raises(RuntimeError, match="physics failed"):
        with WorkJournal(
            path,
            run_id="run",
            launch=0,
            dt_ns=4,
            n_envs=1,
            physics_source_sha256="a" * 64,
        ) as work:
            with work.operation("build"):
                with work.operation("physics_step"):
                    raise RuntimeError("physics failed")
    result = summarize_work(path, run_id="run", launch=0)
    assert result["journal_complete"]
    assert result["completed_env_steps"] == 0
    assert result["failed"]["physics_step"] == 1
    assert not result["recorded_step_work_exact"]


def test_observer_records_internal_calls_and_restores_method(tmp_path):
    """MON-12/MON-13: observation precedes construction and is scoped to the launch."""
    from aisle.harness.simulator_work import WorkJournal, observe_physics, summarize_work

    class Simulator:
        dt = 0.000000004
        _B = 2

        def step(self):
            return "advanced"

    original = Simulator.step
    path = tmp_path / "work.jsonl"
    with WorkJournal(
        path,
        run_id="run",
        launch=0,
        dt_ns=4,
        n_envs=2,
        physics_source_sha256=hashlib.sha256(
            Path(inspect.getfile(Simulator)).read_bytes()
        ).hexdigest(),
    ) as work:
        with observe_physics(Simulator, work, dt_ns=4, n_envs=2):
            assert work.call("build", lambda: Simulator().step()) == "advanced"
        assert Simulator.step is original
    result = summarize_work(path, run_id="run", launch=0)
    assert result["completed_env_steps"] == 2


def test_observer_refuses_unit_drift_before_physics(tmp_path):
    """MON-13/CSE-4: mismatched producer units cannot create mislabeled work."""
    from aisle.harness.simulator_work import WorkJournal, observe_physics

    calls = []

    class Simulator:
        dt = 0.01
        _B = 2

        def step(self):
            calls.append(True)

    original = Simulator.step
    with WorkJournal(
        tmp_path / "work.jsonl",
        run_id="run",
        launch=0,
        dt_ns=4,
        n_envs=2,
        physics_source_sha256=hashlib.sha256(
            Path(inspect.getfile(Simulator)).read_bytes()
        ).hexdigest(),
    ) as work:
        with pytest.raises(ValueError, match="units"):
            with observe_physics(Simulator, work, dt_ns=4, n_envs=2):
                work.call("build", lambda: Simulator().step())
    assert not calls
    assert Simulator.step is original


@pytest.mark.parametrize("fault", ["parent", "close_outer_first", "missing_child_end"])
def test_nested_journal_corruption_cannot_certify_work(tmp_path, fault):
    """MON-13/CSE-4: malformed nesting cannot certify complete physical work."""
    import json

    from aisle.harness.simulator_work import WorkJournal, summarize_work

    path = tmp_path / "work.jsonl"
    with WorkJournal(
        path,
        run_id="run",
        launch=0,
        dt_ns=4,
        n_envs=1,
        physics_source_sha256="a" * 64,
    ) as work:
        work.call("build", lambda: work.call("physics_step", lambda: None))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if fault == "parent":
        rows[2]["parent"] = True
    elif fault == "close_outer_first":
        rows[3], rows[4] = rows[4], rows[3]
    else:
        del rows[3]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = summarize_work(path, run_id="run", launch=0)
    assert not report["journal_complete"]
    assert not report["recorded_step_work_exact"]


def test_observer_restores_method_even_when_observation_is_changed(tmp_path):
    """MON-13: detect observation drift and release owned instrumentation on failure."""
    from aisle.harness.simulator_work import WorkJournal, observe_physics

    class Simulator:
        dt = 0.000000004
        _B = 1

        def step(self):
            return "original"

    original = Simulator.step
    source = hashlib.sha256(Path(inspect.getfile(Simulator)).read_bytes()).hexdigest()
    with WorkJournal(
        tmp_path / "work.jsonl",
        run_id="run",
        launch=0,
        dt_ns=4,
        n_envs=1,
        physics_source_sha256=source,
    ) as work:
        with pytest.raises(ValueError, match="changed"):
            with observe_physics(Simulator, work, dt_ns=4, n_envs=1):
                Simulator.step = lambda self: "replaced"
        assert Simulator.step is original
        # A failed observer must release its exclusivity as well as its patch.
        with observe_physics(Simulator, work, dt_ns=4, n_envs=1):
            assert Simulator().step() == "original"


def test_overlapping_observer_does_not_remove_active_instrumentation(tmp_path):
    """MON-13: a second observer cannot replace or disable the current observer."""
    from aisle.harness.simulator_work import WorkJournal, observe_physics, summarize_work

    class Simulator:
        dt = 0.000000004
        _B = 1

        def step(self):
            return "advanced"

    source = hashlib.sha256(Path(inspect.getfile(Simulator)).read_bytes()).hexdigest()
    path = tmp_path / "work.jsonl"
    with WorkJournal(
        path,
        run_id="run",
        launch=0,
        dt_ns=4,
        n_envs=1,
        physics_source_sha256=source,
    ) as work:
        with observe_physics(Simulator, work, dt_ns=4, n_envs=1):
            with pytest.raises(ValueError, match="already active"):
                with observe_physics(Simulator, work, dt_ns=4, n_envs=1):
                    pytest.fail("overlapping observer entered")
            assert Simulator().step() == "advanced"
    assert summarize_work(path, run_id="run", launch=0)["completed_env_steps"] == 1


def test_foreign_thread_cannot_advance_under_another_thread_observer(tmp_path):
    """MON-13/CSE-4: refuse concurrent physics before it can evade serial recording."""
    from concurrent.futures import ThreadPoolExecutor

    from aisle.harness.simulator_work import WorkJournal, observe_physics

    calls = []

    class Simulator:
        dt = 0.000000004
        _B = 1

        def step(self):
            calls.append(True)

    source = hashlib.sha256(Path(inspect.getfile(Simulator)).read_bytes()).hexdigest()
    with WorkJournal(
        tmp_path / "work.jsonl",
        run_id="run",
        launch=0,
        dt_ns=4,
        n_envs=1,
        physics_source_sha256=source,
    ) as work:
        with observe_physics(Simulator, work, dt_ns=4, n_envs=1):
            with ThreadPoolExecutor(max_workers=1) as executor:
                with pytest.raises(ValueError, match="thread"):
                    executor.submit(Simulator().step).result(timeout=5)
    assert not calls


def test_source_mismatch_refuses_observation_without_patching(tmp_path):
    """MON-13: source identity drift is rejected before installing instrumentation."""
    from aisle.harness.simulator_work import WorkJournal, observe_physics

    class Simulator:
        def step(self):
            pytest.fail("physics must not execute")

    original = Simulator.step
    with WorkJournal(
        tmp_path / "work.jsonl",
        run_id="run",
        launch=0,
        dt_ns=4,
        n_envs=1,
        physics_source_sha256="a" * 64,
    ) as work:
        with pytest.raises(ValueError, match="source differs"):
            with observe_physics(Simulator, work, dt_ns=4, n_envs=1):
                pytest.fail("mismatched observer entered")
    assert Simulator.step is original
