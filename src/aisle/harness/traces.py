"""Traces query (SPEC 070 HAR-6): aligned slices and per-topic summaries
over the Arrow traces a rollout run recorded."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa


def _load(run_dir: Path, topic: str) -> pa.Table:
    path = run_dir / "traces" / f"{topic}.arrow"
    if not path.exists():
        raise FileNotFoundError(f"no trace for topic {topic!r} under {run_dir}")
    # STREAM format, read defensively: a recorder killed mid-write leaves a
    # truncated tail batch — keep every complete batch
    batches = []
    with pa.ipc.open_stream(path) as reader:
        try:
            for batch in reader:
                batches.append(batch)
        except pa.ArrowInvalid:
            pass  # truncated tail
    if not batches:
        raise FileNotFoundError(f"trace for topic {topic!r} holds no complete batches")
    return pa.Table.from_batches(batches)


def query(
    run_dir: Path,
    topic: str,
    t0_ns: int | None = None,
    t1_ns: int | None = None,
    summarize: bool = False,
) -> dict:
    """HAR-6: a time-sliced view of one topic, as JSON-ready data or a
    per-topic summary (rate achieved, min/max, gaps)."""
    table = _load(run_dir, topic)
    times = np.asarray(table["sim_time_ns"], dtype=np.int64)
    mask = np.ones(len(times), dtype=bool)
    if t0_ns is not None:
        mask &= times >= t0_ns
    if t1_ns is not None:
        mask &= times <= t1_ns
    idx = np.flatnonzero(mask)
    times = times[idx]
    if not summarize:
        sliced = table.take(idx)
        return {
            "topic": topic,
            "n": len(idx),
            "sim_time_ns": times.tolist(),
            "seq": sliced["seq"].to_pylist(),
            "data": sliced["data"].to_pylist(),
        }
    if len(times) < 2:
        return {"topic": topic, "n": len(idx), "rate_hz": 0.0, "gaps": 0}
    spans = np.diff(np.sort(times)) / 1e9
    flat = np.concatenate([np.asarray(v, dtype=np.float64) for v in table["data"].to_pylist()])
    median_dt = float(np.median(spans))
    return {
        "topic": topic,
        "n": len(idx),
        "rate_hz": round(1.0 / median_dt, 2) if median_dt > 0 else 0.0,
        "min": float(flat.min()),
        "max": float(flat.max()),
        # a gap is a step over 3x the median spacing
        "gaps": int(np.sum(spans > 3 * median_dt)) if median_dt > 0 else 0,
    }
