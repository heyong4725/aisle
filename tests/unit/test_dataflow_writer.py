"""Build-time validation of write_bridge_dataflow's recorder-await plumbing
(issue #94, PR #159 review): misconfiguration must be rejected in the
pytest process, where the error is instant and readable — a recorder
waiting on a topic it can never see would otherwise burn the settle
helper's whole outer deadline. No dora, no sim (CON-12)."""

import yaml
from pytest import mark, raises

pytestmark = mark.unit


def _write(dataflow, tmp_path, **kwargs):
    rec = tmp_path / "records.jsonl"
    return dataflow.write(tmp_path, rec, **kwargs)


@mark.parametrize("bad", ["reset_done", "reset_done:", "reset_done:0", ":2", "reset_done:-1"])
def test_malformed_await_spec_is_rejected_at_build_time(dataflow, tmp_path, bad):
    with raises(ValueError, match="topic:count"):
        _write(dataflow, tmp_path, recorder_await=bad)


def test_await_topic_must_be_wired_to_the_recorder(dataflow, tmp_path):
    """An awaited topic absent from recorder_inputs could never be met —
    the exact opaque-hang misconfiguration the validation exists to stop."""
    with raises(ValueError, match="not wired"):
        _write(dataflow, tmp_path, recorder_await="episode_result:1")


def test_tail_or_sim_horizon_without_await_is_rejected(dataflow, tmp_path):
    """recorder.py only reads the tail/sim knobs on the awaited path;
    passing them alone would be a silent no-op."""
    with raises(ValueError, match="no-op"):
        _write(dataflow, tmp_path, recorder_await_tail_s=5.0)
    with raises(ValueError, match="no-op"):
        _write(dataflow, tmp_path, recorder_await_sim_ns=int(1e9))


def test_awaited_topic_is_queued_lossless(dataflow, tmp_path):
    """The awaited stream must not be latest-wins: dora's default delivery
    can coalesce two awaited rows under recorder starvation, leaving the
    count unreachable forever (PR #159 cross-model review)."""
    graph = _write(dataflow, tmp_path, recorder_await="reset_done:2")
    nodes = yaml.safe_load(graph.read_text())["nodes"]
    recorder = next(n for n in nodes if n["id"] == "recorder")
    wired = recorder["inputs"]["reset_done"]
    assert isinstance(wired, dict) and wired.get("queue_size", 0) >= 100, wired
    assert recorder["env"]["RECORDER_AWAIT"] == "reset_done:2"


def test_valid_await_passes_through_all_knobs(dataflow, tmp_path):
    graph = _write(
        dataflow,
        tmp_path,
        recorder_await="reset_done:2",
        recorder_await_tail_s=8.0,
        recorder_await_sim_ns=1_200_000_000,
    )
    nodes = yaml.safe_load(graph.read_text())["nodes"]
    env = next(n for n in nodes if n["id"] == "recorder")["env"]
    assert env["RECORDER_AWAIT_TAIL_S"] == "8.0"
    assert env["RECORDER_AWAIT_SIM_NS"] == "1200000000"
