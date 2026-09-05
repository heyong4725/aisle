#!/usr/bin/env python3
"""Clean-clone quickstart (BMK-7, BMK-9; SPEC 540, issue #357).

One non-interactive command that, from a fresh clone, installs the declared
environment, validates a small public graph, runs one fixed public task
instance in development_public mode, constructs a submission bundle,
validates it, and regenerates its report. Emits one CON-8 JSON record with
stage outcomes, versions, hashes, resources, elapsed time, and output
paths. A skipped stage, a local override, or a pre-existing run directory
makes the record `ok: false`; nothing is silently substituted.

    uv run python tools/quickstart.py --out runs/quickstart

The simulation stage needs `uv sync --extra sim` and the pinned dora CLI;
without them the stage fails and the record says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH = "graphs/expert_t0.yaml"
SEED = 0
VERSION_ID = "aisle-benchmark-v1-draft"


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def _sha(path: Path) -> str | None:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def stage(record: dict, name: str, fn) -> bool:
    started = time.monotonic()
    try:
        outcome = fn()
        ok = bool(outcome.get("ok"))
    except Exception as exc:  # noqa: BLE001 - every failure is a recorded stage outcome
        outcome, ok = {"ok": False, "error": repr(exc)}, False
    record["stages"].append(
        {"name": name, "ok": ok, "elapsed_s": round(time.monotonic() - started, 3), **outcome}
    )
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, default=Path("runs/quickstart"))
    parser.add_argument(
        "--skip-sync", action="store_true", help="records a local override (ok:false)"
    )
    parser.add_argument("--run-id", default="quickstart-t0-seed0")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    out = (root / args.out).resolve() if not args.out.is_absolute() else args.out
    started = time.monotonic()
    record: dict = {
        "ok": False,
        "schema_version": "aisle.benchmark.quickstart.v1",
        "benchmark_version": VERSION_ID,
        "mode": "development_public",
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "versions": {},
        "hashes": {"lock": _sha(root / "uv.lock"), "graph": _sha(root / GRAPH), "env_hash": None},
        "stages": [],
        "outputs": {},
        "local_overrides": [],
    }
    if (root / "runs" / args.run_id).exists():
        record["local_overrides"].append(f"pre-existing run directory runs/{args.run_id}")
    if args.skip_sync:
        record["local_overrides"].append("--skip-sync")

    def sync():
        if args.skip_sync:
            return {"ok": False, "error": "sync skipped by local override"}
        code, so, se = _run(["uv", "sync", "--extra", "sim"], root)
        return {"ok": code == 0, "stderr_tail": se[-400:]}

    def versions():
        code, so, _ = _run(
            ["uv", "run", "python", "-c", "import aisle,sys;print(sys.version.split()[0])"], root
        )
        record["versions"]["python"] = so.strip() if code == 0 else None
        code, so, _ = _run(["dora", "--version"], root)
        record["versions"]["dora_cli"] = (
            so.strip().splitlines()[0] if code == 0 and so.strip() else None
        )
        code, so, _ = _run(["uv", "run", "python", "tools/env_hash.py"], root)
        try:
            record["hashes"]["env_hash"] = json.loads(so)["env_hash"] if code == 0 else None
        except (json.JSONDecodeError, KeyError):
            record["hashes"]["env_hash"] = None
        return {
            "ok": record["versions"]["python"] is not None
            and record["versions"]["dora_cli"] is not None
        }

    def validate():
        code, so, se = _run(["uv", "run", "harness", "validate", GRAPH], root)
        try:
            report = json.loads(so)
        except json.JSONDecodeError:
            return {"ok": False, "stderr_tail": se[-400:]}
        return {"ok": bool(report.get("ok")), "errors": report.get("errors", [])[:3]}

    def rollout():
        cmd = [
            "uv",
            "run",
            "harness",
            "rollout",
            "--graph",
            GRAPH,
            "--tier",
            "T0",
            "--embodiment",
            "franka",
            "--perception",
            "L0",
            "--episodes",
            "1",
            "--seeds",
            str(SEED),
            "--no-idea-gate",
            "--env-baseline",
            "local",
            "--run-id",
            args.run_id,
        ]
        code, so, se = _run(cmd, root)
        try:
            result = json.loads(so)
        except json.JSONDecodeError:
            return {"ok": False, "stderr_tail": se[-600:]}
        record["outputs"]["run_dir"] = f"runs/{args.run_id}"
        episodes = result.get("episodes", [])
        return {
            "ok": bool(result.get("ok")) and len(episodes) == 1,
            "episodes": episodes,
            "durations": result.get("durations"),
        }

    def bundle():
        run_dir = root / "runs" / args.run_id
        manifest = (
            json.loads((run_dir / "manifest.json").read_text())
            if (run_dir / "manifest.json").exists()
            else {}
        )
        episodes = (
            [
                json.loads(line)
                for line in (run_dir / "episodes.jsonl").read_text().splitlines()
                if line.strip()
            ]
            if (run_dir / "episodes.jsonl").exists()
            else []
        )
        payload = {
            "schema_version": "aisle.benchmark.submission.v1",
            "submission_id": f"quickstart-{args.run_id}",
            "benchmark_version": VERSION_ID,
            "agent": {
                "provider": "none",
                "model_id": "expert_t0 fixture",
                "requested_parameters": {},
                "client_version": "repository",
                "access_date": "not_applicable",
                "nondeterminism": "none",
            },
            "contract_hashes": {
                "participant_contract": _sha(root / "docs/benchmark/v1/participant-contract.md")
                or "sha256:" + "0" * 64,
                "prompt": "sha256:" + "0" * 64,
                "tool_contract": _sha(root / "harness/budget.toml") or "sha256:" + "0" * 64,
            },
            "treatment": "typed",
            "artifacts": {
                "authored_hash": _sha(root / GRAPH),
                "executed_hash": manifest.get("graph_hash"),
            },
            "environment": {
                "lock_hash": record["hashes"]["lock"],
                "env_hash": manifest.get("env_hash"),
                "platform": manifest.get("platform"),
            },
            "sessions": [
                {
                    "session_id": args.run_id,
                    "attempt_id": "1",
                    "treatment": "typed",
                    "provenance": {
                        "run_id": manifest.get("run_id"),
                        "git_sha": manifest.get("git_sha"),
                        "mode": "development_public",
                    },
                    "budget": {"episodes": 1},
                    "outcome": {"episodes": episodes},
                    "exclusion": None,
                }
            ],
            "resources": {
                "tokens": 0,
                "cached_tokens": None,
                "wall_seconds": round(time.monotonic() - started, 1),
                "tool_calls": 0,
                "api_cost": None,
                "retries": 0,
                "parallel_agents": 1,
            },
            "evidence": {
                "commands": f"runs/{args.run_id}/traces",
                "receipts": f"runs/{args.run_id}/traces",
                "interventions": f"runs/{args.run_id}/traces",
                "outcomes": f"runs/{args.run_id}/episodes.jsonl",
            },
            "transcript": {
                "kind": "provider_limited_substitute",
                "path": "not_applicable: no agent",
            },
            "attestation": {
                "signed_by": "quickstart",
                "signature": "unsigned development_public",
                "integrity_controller": "none",
            },
            "declared_score": None,
        }
        out.mkdir(parents=True, exist_ok=True)
        path = out / "submission.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        record["outputs"]["submission"] = str(path)
        return {"ok": True, "sha256": _sha(path)}

    def validate_bundle():
        sys.path.insert(0, str(root / "src"))
        from aisle.harness.benchmark_submission import validate_submission

        payload = json.loads(Path(record["outputs"]["submission"]).read_text())
        problems = validate_submission(payload, root=root)
        return {"ok": not problems, "problems": problems[:5]}

    def report():
        payload = json.loads(Path(record["outputs"]["submission"]).read_text())
        episodes = payload["sessions"][0]["outcome"]["episodes"]
        row = {
            "schema_version": "aisle.benchmark.leaderboard.v1",
            "benchmark_version": VERSION_ID,
            "result_version": "development_public-unscored",
            "submission_id": payload["submission_id"],
            "agent": payload["agent"],
            "treatment": "typed",
            "experimental_unit": "agent_session",
            "sample": {
                "sessions_randomized": 1,
                "sessions_included": 1,
                "tasks": 1,
                "heldout_seeds_per_artifact": 0,
            },
            "success": {
                "sessions_succeeded": sum(1 for e in episodes if e.get("status") == "success"),
                "rate": None,
                "exact_interval": None,
            },
            "failure_classes": {str(e.get("failure")): 1 for e in episodes if e.get("failure")},
            "effect": {
                "contrast": None,
                "risk_difference": None,
                "newcombe_interval": None,
                "strata": {},
            },
            "exclusions": {"infrastructure": [], "treatment_integrity": [], "deviations": []},
            "integrity": {
                "treatment_integrity_status": "not_applicable",
                "instrument_audit_status": "not_applicable",
            },
            "safety": {
                "exposure_episodes": len(episodes),
                "interventions": None,
                "wrong_object_events": 0,
                "zero_event_bound": None,
            },
            "resources": {
                **payload["resources"],
                "cpu_seconds": None,
                "gpu_seconds": None,
                "peak_memory_bytes": None,
                "storage_bytes": None,
                "latency": None,
            },
            "claim_status": "unrun",
            "ranking": "none",
        }
        path = out / "report.json"
        path.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
        record["outputs"]["report"] = str(path)
        return {"ok": True, "sha256": _sha(path)}

    ok = True
    for name, fn in (
        ("sync", sync),
        ("versions", versions),
        ("validate", validate),
        ("rollout", rollout),
        ("bundle", bundle),
        ("validate_bundle", validate_bundle),
        ("report", report),
    ):
        ok = stage(record, name, fn) and ok
        if not ok and name in ("sync", "rollout"):
            record["stages"].append(
                {"name": "remaining", "ok": False, "skipped_because": f"{name} failed"}
            )
            break
    record["ok"] = ok and not record["local_overrides"]
    record["elapsed_s"] = round(time.monotonic() - started, 1)
    record["storage_bytes"] = (
        sum(p.stat().st_size for p in (root / "runs" / args.run_id).rglob("*") if p.is_file())
        if (root / "runs" / args.run_id).exists()
        else None
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "quickstart-record.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, sort_keys=True))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
