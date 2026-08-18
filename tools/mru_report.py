"""MRU decomposition for fleet campaigns (ENPIRE follow-up 1,
owner-approved 2026-08-18): where did each agent-lane's session wall go?

ENPIRE's sharpest fleet diagnostic is Mean Robot Utilization — robots
idle while agents read logs or wait on the model. AISLE's analogue:
SIM utilization = (wall spent inside rollouts) / (session wall). The
recorded manifests predate the durations field, so rollout wall spans
are reconstructed from filesystem evidence: the span from a run dir's
earliest to latest file mtime, clipped to the session window. Reported
per lane and per fleet config beside MTU (tokens), giving the full
ENPIRE-Figure-6 comparison pair. CON-8: JSON to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def run_span(run_dir: Path) -> tuple[float, float] | None:
    """(start, end) epoch seconds from file evidence, None if empty."""
    mtimes = [p.stat().st_mtime for p in run_dir.rglob("*") if p.is_file()]
    return (min(mtimes), max(mtimes)) if mtimes else None


def lane_report(record: dict, worktree: Path) -> dict | None:
    session = record.get("session") or {}
    wall = session.get("wall_s")
    t0 = record.get("session_start_epoch")
    if not wall or not t0:
        return None
    t1 = t0 + wall
    spans = []
    for run_dir in sorted((worktree / "runs").glob("*")):
        if not run_dir.is_dir() or run_dir.name in ("ideas",):
            continue
        span = run_span(run_dir)
        if span is None:
            continue
        lo, hi = max(span[0], t0), min(span[1], t1)
        if hi > lo:
            spans.append(hi - lo)
    rollout_wall = sum(spans)
    return {
        "agent_index": record.get("agent_index"),
        "session_wall_s": wall,
        "rollout_wall_s": round(rollout_wall, 1),
        "rollouts_in_session": len(spans),
        "sim_utilization": round(rollout_wall / wall, 3),
        "think_wait_s": round(wall - rollout_wall, 1),
        "tokens": session.get("tokens"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True, help="runs/a5 dir")
    args = parser.parse_args()
    results = json.loads((args.campaign / "a5_results.json").read_text())
    configs = []
    for config in results.get("configs", []):
        n = config["fleet"]
        lanes = []
        for rec in config.get("agents", []):
            wt = args.campaign / f"fleet_{n}" / f"worktree_{rec.get('agent_index')}"
            lane = lane_report(rec, wt) if wt.exists() else None
            if lane:
                lanes.append(lane)
        if not lanes:
            continue
        util = [ln["sim_utilization"] for ln in lanes]
        configs.append(
            {
                "fleet": n,
                "lanes": lanes,
                "mru_analogue_mean_sim_utilization": round(sum(util) / len(util), 3),
                "mtu_mean_tokens": round(sum(ln["tokens"] or 0 for ln in lanes) / len(lanes)),
                "config_wall_s": config.get("config_wall_s"),
            }
        )
        print(
            f"[mru] fleet {n}: mean sim utilization "
            f"{configs[-1]['mru_analogue_mean_sim_utilization']}",
            file=sys.stderr,
        )
    print(json.dumps({"ok": True, "campaign": str(args.campaign), "configs": configs}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
