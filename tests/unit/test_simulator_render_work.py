"""MON-12/MON-13: render work preserves step units and historical evidence."""

import json

import pytest

from aisle.harness.simulator_work import WorkJournal, summarize_work

pytestmark = pytest.mark.unit


def test_render_has_distinct_count_and_wall_time(tmp_path):
    """MON-12: camera calls contribute render work without adding simulated steps."""
    path = tmp_path / "work.jsonl"
    ticks = iter([10, 20, 30, 60])
    with WorkJournal(
        path, run_id="run", launch=0, dt_ns=4, n_envs=2, clock=lambda: next(ticks)
    ) as work:
        work.call("step", lambda: None)
        work.call("render", lambda: None)
    result = summarize_work(path, run_id="run", launch=0)
    assert result["journal_complete"]
    assert result["completed"]["render"] == 1
    assert result["completed_env_steps"] == 2
    assert result["completed_env_sim_ns"] == 8
    assert result["operation_wall_ns"]["step"] == 10
    assert result["operation_wall_ns"]["render"] == 30


def test_render_failure_retains_attempt_and_zero_completions(tmp_path):
    """MON-12: camera failure retains partial work without inventing a completed render."""
    path = tmp_path / "work.jsonl"

    def fail():
        raise RuntimeError("camera failed")

    with pytest.raises(RuntimeError, match="camera failed"):
        with WorkJournal(path, run_id="run", launch=0, dt_ns=4, n_envs=1) as work:
            work.call("render", fail)
    result = summarize_work(path, run_id="run", launch=0)
    assert result["journal_complete"]
    assert result["attempted"]["render"] == result["failed"]["render"] == 1
    assert result["completed"]["render"] == result["completed_env_steps"] == 0


def test_legacy_flat_journal_remains_replayable(tmp_path):
    """MON-12/MON-13: extending accounting must retain historical v1 evidence meaning."""
    path = tmp_path / "work.jsonl"
    rows = [
        {
            "event": "launch",
            "schema_version": "aisle.simulator-work-journal.v1",
            "run_id": "run",
            "launch": 0,
            "dt_ns": 4,
            "n_envs": 2,
        },
        {"event": "started", "sequence": 1, "kind": "step"},
        {"event": "completed", "sequence": 1, "kind": "step", "wall_ns": 12},
        {"event": "terminal", "operations": 1, "outcome": "returned"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = summarize_work(path, run_id="run", launch=0)
    assert result["journal_complete"]
    assert result["completed_env_steps"] == 2
    assert result["operation_wall_ns"]["step"] == 12


@pytest.mark.parametrize("fault", ["truncated", "legacy_version", "reordered"])
def test_invalid_render_evidence_cannot_certify_work(tmp_path, fault):
    """MON-13: interrupted, reordered or version-mislabelled render work fails replay."""
    path = tmp_path / "work.jsonl"
    with WorkJournal(path, run_id="run", launch=0, dt_ns=4, n_envs=1) as work:
        work.call("render", lambda: None)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if fault == "truncated":
        rows = rows[:2]
    elif fault == "legacy_version":
        rows[0]["schema_version"] = "aisle.simulator-work-journal.v1"
    else:
        rows[1], rows[2] = rows[2], rows[1]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = summarize_work(path, run_id="run", launch=0)
    assert not result["journal_complete"]
    assert not result["recorded_step_work_exact"]


def test_step_nesting_refused_before_physics_call(tmp_path):
    """MON-12: nested steps cannot multiply environment-step accounting."""
    path = tmp_path / "work.jsonl"
    called = []
    with WorkJournal(path, run_id="run", launch=0, dt_ns=4, n_envs=1) as work:
        with work.operation("step"):
            with pytest.raises(ValueError, match="overlapping"):
                work.call("step", lambda: called.append(True))
    assert called == []
    assert summarize_work(path, run_id="run", launch=0)["completed_env_steps"] == 1


def test_camera_outputs_share_one_render_and_reset_does_not_render(tmp_path):
    """MON-12/TC-9: one overhead pass supplies RGB/depth/seg; reset topics do not render."""
    import numpy as np

    from aisle.nodes.dora_genesis import render_frames, reset_publish_topics

    calls = []

    class Camera:
        def __init__(self, name):
            self.name = name

        def render(self, **kwargs):
            calls.append((self.name, kwargs))
            return (np.zeros((1, 1, 3)), np.ones((1, 1)), np.ones((1, 1)))

    cameras = {name: Camera(name) for name in ("overhead", "wrist")}
    path = tmp_path / "work.jsonl"
    with WorkJournal(path, run_id="run", launch=0, dt_ns=4, n_envs=1) as work:
        assert render_frames(cameras, [], work) == {}
        with work.operation("reset"):
            topics = reset_publish_topics({"oracle_state": 30, "poses": 15})
            assert render_frames(cameras, topics, work) == {}
        frames = render_frames(
            cameras, ["rgb_overhead", "depth_overhead", "seg_overhead", "rgb_wrist"], work
        )
    assert calls == [
        ("overhead", {"rgb": True, "depth": True, "segmentation": True}),
        ("wrist", {}),
    ]
    assert frames["rgb_overhead"].dtype == frames["rgb_wrist"].dtype == np.uint8
    assert frames["depth_overhead"].dtype == np.float32
    assert frames["seg_overhead"].dtype == np.int32
    result = summarize_work(path, run_id="run", launch=0)
    assert result["journal_complete"]
    assert result["completed"]["render"] == 2
    assert result["completed_env_steps"] == 0
