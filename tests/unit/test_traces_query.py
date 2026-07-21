"""SPEC 070 traces query (HAR-6) over synthetic Arrow traces — no dora,
no sim (CON-12)."""

from pathlib import Path

import pyarrow as pa
import pytest

from aisle.harness.trace_recorder import TRACE_SCHEMA
from aisle.harness.traces import query

pytestmark = pytest.mark.unit


def write_trace(run_dir: Path, topic: str, rows):
    schema = TRACE_SCHEMA
    (run_dir / "traces").mkdir(parents=True, exist_ok=True)
    with pa.ipc.new_stream(run_dir / "traces" / f"{topic}.arrow", schema) as writer:
        for t, seq, data in rows:
            writer.write_batch(
                pa.record_batch(
                    [
                        pa.array([t], pa.int64()),
                        pa.array([0], pa.int32()),
                        pa.array([seq], pa.int64()),
                        pa.array([data], pa.list_(pa.float64())),
                        pa.array([None], pa.string()),
                    ],
                    schema=schema,
                )
            )


def test_query_slices_by_time(tmp_path):
    """HAR-6: --t0 (inclusive) / --t1 (exclusive, matching the episode
    windows) slice by sim time; rows keep seq and payload."""
    write_trace(tmp_path, "joint_state", [(int(i * 1e7), i + 1, [float(i)]) for i in range(10)])
    result = query(tmp_path, "joint_state", t0_ns=int(3e7), t1_ns=int(6e7))
    assert result["n"] == 3
    assert result["seq"] == [4, 5, 6]
    assert result["data"][0] == [3.0]


def test_summarize_reports_rate_extremes_and_gaps(tmp_path):
    """HAR-6: --summarize returns per-topic stats (rate achieved, min/max,
    gaps) instead of data."""
    rows = [(int(i * 1e7), i, [float(i)]) for i in range(20)]
    rows += [(int(40 * 1e7), 21, [99.0])]  # a 20-tick gap
    write_trace(tmp_path, "gripper_state", rows)
    stats = query(tmp_path, "gripper_state", summarize=True)
    assert stats["rate_hz"] == pytest.approx(100.0, rel=0.05)
    assert stats["min"] == 0.0 and stats["max"] == 99.0
    assert stats["gaps"] == 1
    assert "data" not in stats


def test_missing_topic_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="no trace"):
        query(tmp_path, "nope")


def test_summary_extrema_respect_the_slice(tmp_path):
    """PR #11 review: a filtered summary must report extrema over the
    SLICED rows only — whole-trace extrema misrepresent the window."""
    rows = [(int(i * 1e7), i, [float(i)]) for i in range(10)]
    rows.append((int(99 * 1e7), 99, [999.0]))  # outlier OUTSIDE the slice
    write_trace(tmp_path, "joint_state", rows)
    stats = query(tmp_path, "joint_state", t0_ns=0, t1_ns=int(9.5e7), summarize=True)
    assert stats["max"] == 9.0  # 999 excluded


def test_episode_selector_uses_reset_windows(tmp_path):
    """HAR-6 --episode: episode i spans reset_done i to reset_done i+1."""
    write_trace(tmp_path, "reset_done", [(0, 1, [1.0]), (int(5e8), 2, [1.0])])
    write_trace(tmp_path, "gripper_state", [(int(i * 1e8), i, [float(i)]) for i in range(10)])
    ep0 = query(tmp_path, "gripper_state", episode=0)
    ep1 = query(tmp_path, "gripper_state", episode=1)
    assert ep0["n"] == 5 and ep1["n"] == 5
    assert max(ep0["sim_time_ns"]) < int(5e8) <= min(ep1["sim_time_ns"])


def test_npz_format_writes_arrays(tmp_path):
    """HAR-6 --format npz: arrays land in the file; the report carries the
    path, not the data."""
    import numpy as np

    write_trace(tmp_path, "joint_state", [(int(i * 1e7), i, [float(i), 2.0]) for i in range(4)])
    out = tmp_path / "slice.npz"
    report = query(tmp_path, "joint_state", npz_path=out)
    assert report["npz"] == str(out) and "data" not in report
    loaded = np.load(out)
    assert loaded["data"].shape == (4, 2)
    assert loaded["sim_time_ns"].tolist() == [int(i * 1e7) for i in range(4)]


def test_node_selector_checks_producer(tmp_path):
    """HAR-6 --node: querying a topic under the wrong producing node is an
    error (checked against the run's instrumented graph)."""
    import yaml as yaml_module

    write_trace(tmp_path, "joint_state", [(0, 1, [1.0])])
    (tmp_path / "graph.yaml").write_text(
        yaml_module.safe_dump({"nodes": [{"id": "dora-genesis", "outputs": ["joint_state"]}]})
    )
    assert query(tmp_path, "joint_state", node="dora-genesis")["n"] == 1
    with pytest.raises(FileNotFoundError, match="produced by"):
        query(tmp_path, "joint_state", node="oracle-pose")
