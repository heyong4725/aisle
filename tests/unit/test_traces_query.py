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
                    ],
                    schema=schema,
                )
            )


def test_query_slices_by_time(tmp_path):
    """HAR-6: --t0/--t1 slice by sim time; rows keep seq and payload."""
    write_trace(tmp_path, "joint_state", [(int(i * 1e7), i + 1, [float(i)]) for i in range(10)])
    result = query(tmp_path, "joint_state", t0_ns=int(3e7), t1_ns=int(6e7))
    assert result["n"] == 4
    assert result["seq"] == [4, 5, 6, 7]
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
