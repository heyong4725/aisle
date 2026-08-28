"""local_baseline — the ADR-44 pre-registered measurements for the local
agent arm (issue #285 C3/C4).

C3: five H1-protocol composition attempts (the EXACT H1 prompt, sha-
imported; fresh pinned worktree per attempt; the agent may edit only
GRAPH_REL) scored by `harness validate` and, for any valid graph, the
H1 rollout (T1, 8 episodes, seeds 0..7). Pre-registered expectation:
BELOW H1's 15% launchable, plausibly 0/5.

C4: two additional sessions at the SAME sampler seed, prompt, and
model digest, temperature 0, fresh worktrees; compared on (a) the
assistant event streams and (b) the deliverable graph bytes.
Reporting-only either way (ADR-44).

CON-8: JSON records + manifest; logs stderr. UNATTESTED dev
measurement (ADR-24)."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from agent_adapters import ADAPTERS  # noqa: E402
from h1_protocol import GRAPH_REL, PROMPT_SHA, TASK_PROMPT, make_worktree  # noqa: E402

SESSION_WALL_S = 1800.0
N_ATTEMPTS = 5
REPLAY_SEED = 7


def model_digest(model: str) -> str:
    out = subprocess.run(["ollama", "list"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith(model):
            return line.split()[1]
    return "unknown"


def run_session(model: str, wt: Path, out_dir: Path, seed: int | None) -> dict:
    adapter = ADAPTERS["local"]
    cmd = adapter.cmd(model, TASK_PROMPT)
    if seed is not None:
        cmd += ["--seed", str(seed)]
    stream_path = out_dir / "session.jsonl"
    t0 = time.monotonic()
    with open(stream_path, "w") as stream, open(out_dir / "session.stderr", "w") as err:
        try:
            proc = subprocess.run(cmd, cwd=wt, stdout=stream, stderr=err, timeout=SESSION_WALL_S)
            stopped = "agent_done" if proc.returncode == 0 else f"rc={proc.returncode}"
        except subprocess.TimeoutExpired:
            stopped = "wall_ceiling"
    lines = stream_path.read_text().splitlines()
    graph = wt / GRAPH_REL
    record = {
        "stopped": stopped,
        "wall_s": round(time.monotonic() - t0, 1),
        "tokens_generated": adapter.parse_generated(lines),
        "graph_exists": graph.exists(),
        "graph_sha256": hashlib.sha256(graph.read_bytes()).hexdigest() if graph.exists() else None,
        "turns": sum(1 for x in lines if '"type": "assistant"' in x),
    }
    if graph.exists():
        shutil.copy(graph, out_dir / "deliverable.yaml")
        validate = subprocess.run(
            ["uv", "run", "harness", "validate", GRAPH_REL],
            cwd=wt,
            capture_output=True,
            text=True,
            timeout=300,
        )
        try:
            record["valid"] = bool(json.loads(validate.stdout)["ok"])
        except Exception:  # noqa: BLE001
            record["valid"] = False
        record["validate_tail"] = (validate.stdout or "")[-400:]
    else:
        record["valid"] = False
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3:30b")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs" / "local-arm-baseline")
    parser.add_argument("--replay-only", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    oid = subprocess.run(
        ["git", "rev-parse", "origin/main"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    manifest = {
        "adr": "ADR-44",
        "prompt_sha256": PROMPT_SHA,
        "model": args.model,
        "local_model_digest": model_digest(args.model),
        "local_backend": "ollama",
        "pin_oid": oid,
        "session_wall_s": SESSION_WALL_S,
        "expectation": "below H1 15% launchable; plausibly 0/5 (pre-registered)",
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")

    records: dict = {"attempts": [], "replay": []}
    if not args.replay_only:
        for i in range(N_ATTEMPTS):
            wt = args.out / f"wt_{i}"
            print(f"[baseline] attempt {i}", file=sys.stderr)
            make_worktree(oid, wt)
            out_dir = args.out / f"attempt_{i}"
            out_dir.mkdir(exist_ok=True)
            rec = run_session(args.model, wt, out_dir, seed=None)
            rec["attempt"] = i
            records["attempts"].append(rec)
            (args.out / "records.json").write_text(json.dumps(records, indent=1) + "\n")
            subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=REPO_ROOT)
    for r in ("r1", "r2"):
        wt = args.out / f"wt_{r}"
        print(f"[baseline] replay {r} seed={REPLAY_SEED}", file=sys.stderr)
        make_worktree(oid, wt)
        out_dir = args.out / f"replay_{r}"
        out_dir.mkdir(exist_ok=True)
        rec = run_session(args.model, wt, out_dir, seed=REPLAY_SEED)
        rec["replay"] = r
        records["replay"].append(rec)
        (args.out / "records.json").write_text(json.dumps(records, indent=1) + "\n")
        subprocess.run(["git", "worktree", "remove", "--force", str(wt)], cwd=REPO_ROOT)

    # C4 comparison: streams + deliverables
    if len(records["replay"]) == 2:
        s1 = (args.out / "replay_r1" / "session.jsonl").read_text()
        s2 = (args.out / "replay_r2" / "session.jsonl").read_text()
        d1 = args.out / "replay_r1" / "deliverable.yaml"
        d2 = args.out / "replay_r2" / "deliverable.yaml"
        records["c4"] = {
            "streams_identical": s1 == s2,
            "first_divergent_line": next(
                (
                    i
                    for i, (a, b) in enumerate(zip(s1.splitlines(), s2.splitlines(), strict=False))
                    if a != b
                ),
                None,
            ),
            "deliverables_identical": (
                d1.exists() and d2.exists() and d1.read_bytes() == d2.read_bytes()
            )
            if (d1.exists() or d2.exists())
            else None,
        }
        (args.out / "records.json").write_text(json.dumps(records, indent=1) + "\n")
    print(json.dumps({"ok": True, "records": str(args.out / "records.json")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
