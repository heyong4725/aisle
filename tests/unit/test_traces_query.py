"""SPEC 070 traces query (HAR-6) over synthetic Arrow traces — no dora,
no sim (CON-12)."""

from pathlib import Path

import pyarrow as pa
import pytest

from aisle.harness.trace_recorder import TRACE_SCHEMA
from aisle.harness.traces import query

pytestmark = pytest.mark.unit


def write_trace(run_dir: Path, endpoint: str, rows):
    """rows: (sim_time_ns, seq, data) or (sim_time_ns, seq, data, text)."""
    schema = TRACE_SCHEMA
    (run_dir / "traces").mkdir(parents=True, exist_ok=True)
    with pa.ipc.new_stream(run_dir / "traces" / f"{endpoint}.arrow", schema) as writer:
        for row in rows:
            t, seq, data = row[0], row[1], row[2]
            text = row[3] if len(row) > 3 else None
            writer.write_batch(
                pa.record_batch(
                    [
                        pa.array([t], pa.int64()),
                        pa.array([0], pa.int32()),
                        pa.array([seq], pa.int64()),
                        pa.array([data], pa.list_(pa.float64())),
                        pa.array([text], pa.string()),
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


def _episodes_file(run_dir: Path, n: int) -> None:
    lines = "".join('{"status": "fail"}' + "\n" for _ in range(n))
    (run_dir / "episodes.jsonl").write_text(lines)


def test_episode_selector_uses_reset_windows(tmp_path):
    """HAR-6 --episode: episode i spans reset_done i to reset_done i+1,
    using the REAL reset sequence (N episode resets + the cleanup reset)
    with the index validated against COMPLETED episodes — the phantom
    cleanup-only window and negative indices are rejected (PR #11)."""
    write_trace(
        tmp_path,
        "dora-genesis__reset_done",
        [(0, 1, [1.0]), (int(5e8), 2, [1.0]), (int(10e8), 3, [1.0])],
    )
    _episodes_file(tmp_path, 2)
    write_trace(tmp_path, "gripper_state", [(int(i * 1e8), i, [float(i)]) for i in range(10)])
    ep0 = query(tmp_path, "gripper_state", episode=0)
    ep1 = query(tmp_path, "gripper_state", episode=1)
    assert ep0["n"] == 5 and ep1["n"] == 5
    assert max(ep0["sim_time_ns"]) < int(5e8) <= min(ep1["sim_time_ns"])
    with pytest.raises(FileNotFoundError, match=r"episodes 0\.\.1"):
        query(tmp_path, "gripper_state", episode=2)  # phantom cleanup window
    with pytest.raises(FileNotFoundError, match=r"episodes 0\.\.1"):
        query(tmp_path, "gripper_state", episode=-1)


def test_ambiguous_topic_requires_node_selector(tmp_path):
    """PR #11: two producers of one topic name stay distinct endpoints; a
    bare-topic query on an ambiguous name lists the producers, and --node
    selects one."""
    write_trace(tmp_path, "dora-genesis__reset_done", [(0, 1, [1.0])])
    write_trace(tmp_path, "reset__reset_done", [(0, 1, [1.0]), (int(1e8), 2, [1.0])])
    with pytest.raises(FileNotFoundError, match="multiple producers"):
        query(tmp_path, "reset_done")
    assert query(tmp_path, "reset_done", node="reset")["n"] == 2


def test_text_endpoint_summary_and_npz(tmp_path):
    """PR #11: text-only endpoints (episode_result) summarize without NaN
    extrema and keep their payloads in NPZ exports."""
    import numpy as np

    rows = [(int(i * 1e9), i, None, '{"status": "fail"}') for i in range(3)]
    write_trace(tmp_path, "verifier-oracle__episode_result", rows)
    stats = query(tmp_path, "episode_result", summarize=True)
    assert stats["n"] == 3 and "min" not in stats and "max" not in stats
    out = tmp_path / "results.npz"
    report = query(tmp_path, "episode_result", npz_path=out)
    assert report["npz"] == str(out)
    loaded = np.load(out)
    assert list(loaded["text"]) == ['{"status": "fail"}'] * 3
    assert "data" not in loaded


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


def test_node_selector_resolves_endpoint_files(tmp_path):
    """HAR-6 --node: selects the producer-qualified endpoint file; a wrong
    node is an error."""
    write_trace(tmp_path, "dora-genesis__joint_state", [(0, 1, [1.0])])
    assert query(tmp_path, "joint_state", node="dora-genesis")["n"] == 1
    with pytest.raises(FileNotFoundError, match="no trace for endpoint"):
        query(tmp_path, "joint_state", node="oracle-pose")
