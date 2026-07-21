"""Traces query (SPEC 070 HAR-6): aligned slices and per-topic summaries
over the Arrow traces a rollout run recorded. Episode windows derive from
the reset_done rows (episode i spans reset i to reset i+1); --node checks
the topic's producer against the run's instrumented graph."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import yaml


def resolve_endpoint(run_dir: Path, topic: str, node: str | None = None) -> Path:
    """Trace files are per-ENDPOINT (<producer>__<topic>.arrow) because two
    nodes may produce the same topic name (e.g. reset_done). A bare topic
    resolves when unique; ambiguity requires --node."""
    traces = run_dir / "traces"
    if node is not None:
        path = traces / f"{node}__{topic}.arrow"
        if not path.exists():
            raise FileNotFoundError(f"no trace for endpoint {node}/{topic} under {run_dir}")
        return path
    matches = sorted(traces.glob(f"*__{topic}.arrow")) + (
        [traces / f"{topic}.arrow"] if (traces / f"{topic}.arrow").exists() else []
    )
    if not matches:
        raise FileNotFoundError(f"no trace for topic {topic!r} under {run_dir}")
    if len(matches) > 1:
        producers = [p.name.split("__")[0] for p in matches]
        raise FileNotFoundError(
            f"topic {topic!r} has multiple producers {producers}; select one with --node"
        )
    return matches[0]


def _load(run_dir: Path, topic: str, node: str | None = None) -> pa.Table:
    path = resolve_endpoint(run_dir, topic, node)
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


def _completed_episodes(run_dir: Path) -> int | None:
    path = run_dir / "episodes.jsonl"
    if not path.exists():
        return None
    return sum(1 for line in path.read_text().splitlines() if line.strip())


def episode_window(run_dir: Path, episode: int) -> tuple[int, int | None]:
    """Episode i spans reset_done i (inclusive) to reset_done i+1. The
    index is validated against COMPLETED episodes (episodes.jsonl): an
    N-episode run records N resets plus the cleanup reset, so the raw
    reset count would admit a phantom cleanup-only 'episode' (PR #11
    review); negative indices are rejected outright. The bridge's own
    reset_done endpoint is authoritative when several exist."""
    completed = _completed_episodes(run_dir)
    try:
        resets = sorted(
            _load(run_dir, "reset_done", node="dora-genesis")["sim_time_ns"].to_pylist()
        )
    except FileNotFoundError:
        resets = sorted(_load(run_dir, "reset_done")["sim_time_ns"].to_pylist())
    limit = completed if completed is not None else max(0, len(resets) - 1)
    if not 0 <= episode < limit:
        raise FileNotFoundError(f"run has episodes 0..{limit - 1}, not {episode}")
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
    if episode is not None:
        ep_start, ep_end = episode_window(run_dir, episode)
        t0_ns = max(t0_ns, ep_start) if t0_ns is not None else ep_start
        if ep_end is not None:
            t1_ns = min(t1_ns, ep_end) if t1_ns is not None else ep_end
    table = _load(run_dir, topic, node)
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
        median_dt = float(np.median(spans))
        summary = {
            "topic": topic,
            "n": len(idx),
            "rate_hz": round(1.0 / median_dt, 2) if median_dt > 0 else 0.0,
            # a gap is a step over 3x the median spacing
            "gaps": int(np.sum(spans > 3 * median_dt)) if median_dt > 0 else 0,
        }
        # extrema over the SLICED numeric rows; text-only endpoints (e.g.
        # episode_result) OMIT min/max rather than emitting NaN (PR #11)
        rows = [v for v in sliced["data"].to_pylist() if v is not None]
        if rows:
            flat = np.concatenate([np.asarray(v, dtype=np.float64) for v in rows])
            summary["min"] = float(flat.min())
            summary["max"] = float(flat.max())
        return summary
    if npz_path is not None:
        arrays = {
            "sim_time_ns": times,
            "seq": np.asarray(sliced["seq"], dtype=np.int64),
        }
        rows = [v for v in sliced["data"].to_pylist() if v is not None]
        if rows:
            arrays["data"] = np.asarray(rows, dtype=np.float64)
        texts = [v for v in sliced["text"].to_pylist() if v is not None]
        if texts:
            # text payloads (episode_result etc.) are preserved, not dropped
            arrays["text"] = np.asarray(texts, dtype=np.str_)
        np.savez(npz_path, **arrays)
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
