"""Idea tree (SPEC 070 HAR-7/8): append-only JSONL per branch under
runs/ideas/<branch>.jsonl. An idea is OPEN if logged and not closed; the
rollout gate (HAR-2) refuses to launch without one. Timestamps and git
SHAs are injected (CON-5)."""

from __future__ import annotations

import json
from pathlib import Path


def _ideas_file(root: Path, branch: str) -> Path:
    safe_branch = branch.replace("/", "__")
    return root / "runs" / "ideas" / f"{safe_branch}.jsonl"


def _read(root: Path, branch: str) -> list[dict]:
    path = _ideas_file(root, branch)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def log_idea(
    root: Path,
    branch: str,
    idea: str,
    ts: str,
    git_sha: str,
    parent: str | None = None,
    expect: str | None = None,
) -> dict:
    """HAR-7: append an idea entry with a branch-monotonic id (I1, I2...)."""
    entries = _read(root, branch)
    entry = {
        "id": f"I{len(entries) + 1}",
        "ts": ts,
        "git_sha": git_sha,
        "idea": idea,
        "parent": parent,
        "expect": expect,
        "status": "open",
    }
    path = _ideas_file(root, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def close_idea(root: Path, branch: str, idea_id: str, observed: str, verdict: str, ts: str) -> dict:
    """HAR-7: append a close record. Verdict is one of up|down|flat."""
    if verdict not in ("up", "down", "flat"):
        raise ValueError(f"verdict must be up|down|flat, got {verdict!r}")
    if not any(e["id"] == idea_id for e in open_ideas(root, branch)):
        raise ValueError(f"no open idea {idea_id!r} on branch {branch!r}")
    entry = {
        "id": idea_id,
        "ts": ts,
        "observed": observed,
        "verdict": verdict,
        "status": "closed",
    }
    with open(_ideas_file(root, branch), "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def open_ideas(root: Path, branch: str) -> list[dict]:
    """HAR-8: logged and not (yet) closed, in log order."""
    entries = _read(root, branch)
    closed = {e["id"] for e in entries if e.get("status") == "closed"}
    return [e for e in entries if e.get("status") == "open" and e["id"] not in closed]
