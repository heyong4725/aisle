"""Traces query (SPEC 070 HAR-6): aligned slices and per-topic summaries
over the Arrow traces a rollout run recorded. Episode windows derive from
the reset_done rows (episode i spans reset i to reset i+1); --node checks
the topic's producer against the run's instrumented graph."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import yaml


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


def episode_window(run_dir: Path, episode: int) -> tuple[int, int | None]:
    """Episode i spans reset_done i (inclusive) to reset_done i+1: sim-ns
    bounds derived from the recorded reset stream."""
    resets = sorted(_load(run_dir, "reset_done")["sim_time_ns"].to_pylist())
    if episode >= len(resets):
        raise FileNotFoundError(f"run has no episode {episode} ({len(resets)} resets recorded)")
    start = resets[episode]
    end = resets[episode + 1] if episode + 1 < len(resets) else None
    return start, end


def producer_of(run_dir: Path, topic: str) -> str | None:
    graph = run_dir / "graph.yaml"
    if not graph.exists():
        return None
    for node in yaml.safe_load(graph.read_text())["nodes"]:
        if topic in (node.get("outputs") or []):
            return node["id"]
    return None


def query(
    run_dir: Path,
    topic: str,
    t0_ns: int | None = None,
    t1_ns: int | None = None,
    summarize: bool = False,
    episode: int | None = None,
    node: str | None = None,
    npz_path: Path | None = None,
) -> dict:
    """HAR-6: a time/episode-sliced view of one topic — JSON-ready data, an
    npz file, or a per-topic summary (rate achieved, min/max, gaps) over
    the SLICED rows."""
    if node is not None:
        actual = producer_of(run_dir, topic)
        if actual is not None and actual != node:
            raise FileNotFoundError(f"topic {topic!r} is produced by {actual!r}, not {node!r}")
    if episode is not None:
        ep_start, ep_end = episode_window(run_dir, episode)
        t0_ns = max(t0_ns, ep_start) if t0_ns is not None else ep_start
        if ep_end is not None:
            t1_ns = min(t1_ns, ep_end) if t1_ns is not None else ep_end
    table = _load(run_dir, topic)
    times = np.asarray(table["sim_time_ns"], dtype=np.int64)
    mask = np.ones(len(times), dtype=bool)
    if t0_ns is not None:
        mask &= times >= t0_ns
    if t1_ns is not None:
        mask &= times < t1_ns
    idx = np.flatnonzero(mask)
    times = times[idx]
    sliced = table.take(idx)
    if summarize:
        if len(times) < 2:
            return {"topic": topic, "n": len(idx), "rate_hz": 0.0, "gaps": 0}
        spans = np.diff(np.sort(times)) / 1e9
        # extrema over the SLICED rows only (a filtered summary previously
        # reported whole-trace extrema — PR #11 review)
        rows = [v for v in sliced["data"].to_pylist() if v is not None]
        flat = (
            np.concatenate([np.asarray(v, dtype=np.float64) for v in rows])
            if rows
            else np.array([np.nan])
        )
        median_dt = float(np.median(spans))
        return {
            "topic": topic,
            "n": len(idx),
            "rate_hz": round(1.0 / median_dt, 2) if median_dt > 0 else 0.0,
            "min": float(np.nanmin(flat)),
            "max": float(np.nanmax(flat)),
            # a gap is a step over 3x the median spacing
            "gaps": int(np.sum(spans > 3 * median_dt)) if median_dt > 0 else 0,
        }
    if npz_path is not None:
        rows = sliced["data"].to_pylist()
        data = (
            np.asarray([r for r in rows if r is not None], dtype=np.float64)
            if rows and rows[0] is not None
            else np.empty((0,))
        )
        np.savez(
            npz_path,
            sim_time_ns=times,
            seq=np.asarray(sliced["seq"], dtype=np.int64),
            data=data,
        )
        return {"topic": topic, "n": len(idx), "npz": str(npz_path)}
    result = {
        "topic": topic,
        "n": len(idx),
        "sim_time_ns": times.tolist(),
        "seq": sliced["seq"].to_pylist(),
        "data": sliced["data"].to_pylist(),
    }
    if "text" in table.column_names:
        result["text"] = sliced["text"].to_pylist()
    return result
