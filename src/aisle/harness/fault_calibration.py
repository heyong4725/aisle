"""Severity calibration for fault-bank instances (FLT-9, FLT-10, FLT-12;
issue #348).

Each candidate instance and severity rung is materialized into an
evaluator-owned git worktree at the declared clean commit, run through the
same graph and paired seeds as a retained clean baseline, and scored by the
frozen rule: the seed pair is the unit; the paired clean-minus-fault
episode-success difference with an exact interval decides whether the rung
degrades the baseline by at least the minimum meaningful degradation while
leaving valid evidence. Every attempted rung and the sham are retained;
campaign purpose is `excluded_pilot` and nothing here may enter a
confirmatory estimate.

`score_rung` and `calibration_report` are pure (CON-12); `run_rung` is the
subprocess adapter around `harness rollout`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from aisle.harness.benchmark_statistics import clopper_pearson_interval
from aisle.harness.fault_injector import content_hash, materialize

REPORT_SCHEMA = "aisle.fault-bank.calibration.v1"
DEFAULT_RULE = {
    "unit": "seed_pair",
    "outcome": "episode_success",
    "direction": "fault lower than clean",
    "minimum_meaningful_degradation": 0.25,
    "confidence_level": 0.95,
    "minimum_discordant_pairs": 3,
    "selection": "paired difference point estimate >= minimum, at least the minimum number of"
    " discordant pairs, and the exact 95% lower bound on the clean-only share of discordant"
    " pairs above 0.5 (paired binomial); sham must not degrade by the same rule",
    "saturation": "a clean baseline with zero successes cannot calibrate anything",
}


def _successes(episodes: list[dict]) -> dict[int, bool]:
    return {int(e["seed"]): e.get("status") == "success" for e in episodes}


def score_rung(
    clean_episodes: list[dict],
    fault_episodes: list[dict],
    *,
    rule: dict | None = None,
    sham: bool = False,
) -> dict[str, Any]:
    """FLT-9 paired scoring on the seed-pair unit."""
    rule = {**DEFAULT_RULE, **(rule or {})}
    clean, fault = _successes(clean_episodes), _successes(fault_episodes)
    seeds = sorted(set(clean) & set(fault))
    missing = sorted((set(clean) | set(fault)) - set(seeds))
    n = len(seeds)
    if n == 0:
        return {
            "decision": "invalid",
            "reason": "no paired seeds",
            "pairs": 0,
            "missing_seeds": missing,
        }
    clean_k = sum(1 for s in seeds if clean[s])
    fault_k = sum(1 for s in seeds if fault[s])
    hazards = [e for e in fault_episodes if e.get("failure") == "wrong_object"]
    if hazards:  # FLT-11: a fault may never create a wrong-object action
        return {
            "decision": "rejected",
            "reason": "semantic hazard: the fault produced a wrong-object episode",
            "pairs": n,
            "wrong_object_episodes": len(hazards),
        }
    if clean_k == 0:
        return {
            "decision": "rejected",
            "reason": "saturated: clean baseline has zero successes",
            "pairs": n,
        }
    clean_ci = clopper_pearson_interval(clean_k, n, confidence_level=rule["confidence_level"])
    fault_ci = clopper_pearson_interval(fault_k, n, confidence_level=rule["confidence_level"])
    difference = (clean_k - fault_k) / n
    discordant = {
        "clean_only": sum(1 for s in seeds if clean[s] and not fault[s]),
        "fault_only": sum(1 for s in seeds if fault[s] and not clean[s]),
    }
    n_discordant = discordant["clean_only"] + discordant["fault_only"]
    paired = (
        clopper_pearson_interval(
            discordant["clean_only"], n_discordant, confidence_level=rule["confidence_level"]
        )
        if n_discordant
        else None
    )
    paired_evidence = (
        n_discordant >= rule["minimum_discordant_pairs"]
        and paired is not None
        and paired["lower"] > 0.5
    )
    if sham:
        degraded = difference > 0 and paired_evidence
        decision = "rejected" if degraded else "parity"
        reason = "sham degraded the baseline" if degraded else "sham indistinguishable from clean"
    else:
        effective = difference >= rule["minimum_meaningful_degradation"] and paired_evidence
        decision = "selected" if effective else "rejected"
        reason = (
            "meets the frozen degradation rule"
            if effective
            else "degradation below the minimum or interval overlap"
        )
    return {
        "decision": decision,
        "reason": reason,
        "pairs": n,
        "missing_seeds": missing,
        "clean_successes": clean_k,
        "fault_successes": fault_k,
        "clean_interval": clean_ci,
        "fault_interval": fault_ci,
        "paired_difference": difference,
        "discordant_pairs": discordant,
        "discordant_clean_only_interval": paired,
        "fault_failure_classes": _classes(fault_episodes),
        "rule": rule,
    }


def _classes(episodes: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in episodes:
        key = e.get("failure") or e.get("status") or "no_result"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def calibration_report(
    bank_id: str,
    clean_run_id: str,
    rungs: list[dict],
    *,
    campaign_id: str,
) -> dict[str, Any]:
    """FLT-10: every attempted rung and sham retained with purpose
    excluded_pilot; a selection per instance without exposing bytes."""
    by_instance: dict[str, list[dict]] = {}
    for rung in rungs:
        by_instance.setdefault(rung["opaque_id"], []).append(rung)
    selection = {}
    for opaque_id, rows in by_instance.items():
        chosen = [r for r in rows if r["score"]["decision"] == "selected"]
        if any(r["sham"] for r in rows):
            selection[opaque_id] = {
                "disposition": "control"
                if all(r["score"]["decision"] == "parity" for r in rows)
                else "blocked",
                "rungs": [r["severity_index"] for r in rows],
            }
        elif chosen:
            selection[opaque_id] = {
                "disposition": "selected",
                "severity_index": min(r["severity_index"] for r in chosen),
                "rule": "least severe rung that meets the frozen degradation rule",
                "rungs": [r["severity_index"] for r in rows],
            }
        else:
            selection[opaque_id] = {
                "disposition": "rejected",
                "reasons": sorted({r["score"]["reason"] for r in rows}),
                "rungs": [r["severity_index"] for r in rows],
            }
    report = {
        "schema_version": REPORT_SCHEMA,
        "campaign_id": campaign_id,
        "campaign_purpose": "excluded_pilot",
        "bank_id": bank_id,
        "clean_run_id": clean_run_id,
        "evidence_kind": "simulation",
        "rungs": rungs,
        "selection": selection,
        "wording": (
            "calibration outcomes select or reject severities before seal and never enter "
            "a confirmatory estimate"
        ),
    }
    report["report_hash"] = content_hash({k: v for k, v in report.items() if k != "report_hash"})
    return report


def run_rung(
    *,
    root: Path,
    worktree: Path,
    clean_commit: str,
    instance: dict,
    severity_index: int,
    clean_hash: str,
    graph_rel: str,
    seeds: str,
    tier: str,
    embodiment: str,
    perception: str,
    run_id: str,
    env: dict | None = None,
) -> dict[str, Any]:
    """Materialize into a fresh git worktree at the clean commit and run the
    rollout there with --root so validation resolves the staged sources.
    Returns the receipt, the rollout result, and the episode rows."""
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(worktree), clean_commit],
        cwd=root,
        check=True,
    )
    try:
        receipt = materialize(
            root, worktree, instance, clean_hash=clean_hash, severity_index=severity_index
        )
        cmd = [
            "uv",
            "run",
            "harness",
            "rollout",
            "--root",
            str(worktree),
            "--graph",
            str(worktree / graph_rel),
            "--tier",
            tier,
            "--embodiment",
            embodiment,
            "--perception",
            perception,
            "--episodes",
            str(len(_expand(seeds))),
            "--seeds",
            seeds,
            "--no-idea-gate",
            "--env-baseline",
            "local",
            "--run-id",
            run_id,
        ]
        proc = subprocess.run(
            cmd, cwd=root, capture_output=True, text=True, env={**os.environ, **(env or {})}
        )
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            result = {
                "ok": False,
                "error": "rollout emitted no JSON",
                "stderr_tail": proc.stderr[-2000:],
            }
        episodes_path = worktree / "runs" / run_id / "episodes.jsonl"
        episodes = (
            [json.loads(line) for line in episodes_path.read_text().splitlines() if line.strip()]
            if episodes_path.exists()
            else []
        )
        return {
            "receipt": receipt,
            "result": result,
            "episodes": episodes,
            "run_dir": str(worktree / "runs" / run_id),
        }
    finally:
        pass  # the caller retains the worktree for raw-evidence copy, then removes it


def _expand(seeds: str) -> list[int]:
    if ".." in seeds:
        a, b = seeds.split("..")
        return list(range(int(a), int(b) + 1))
    return [int(s) for s in seeds.split(",") if s.strip()]


def calibrate_bank(args, bank: dict) -> dict[str, Any]:
    """CLI orchestration: every instance and rung, sham included, against
    the retained clean run; raw run dirs copied to the private raw store."""
    import shutil

    root = Path(args.root).resolve()
    staging = Path(args.staging).resolve()
    if staging == root or root in staging.parents:
        return {"ok": False, "error": "staging must be outside the worktree"}
    clean_run = Path(args.clean_run)
    clean_episodes = [
        json.loads(line)
        for line in (clean_run / "episodes.jsonl").read_text().splitlines()
        if line.strip()
    ]
    only = set(args.only.split(",")) if args.only else None
    env = {
        "VIRTUAL_ENV": str(root / ".venv"),
        "UV_PROJECT_ENVIRONMENT": str(root / ".venv"),
    }
    for key in ("CONDA_PREFIX", "CONDA_DEFAULT_ENV"):
        os.environ.pop(key, None)
    rungs = []
    for instance in bank["instances"]:
        if only and instance["opaque_id"] not in only:
            continue
        op = instance["operator"]
        ladder = (
            len(op["replace"])
            if isinstance(op, dict) and isinstance(op.get("replace"), list)
            else 1
        )
        for severity_index in range(ladder):
            run_id = f"{args.campaign_id}-{instance['opaque_id']}-s{severity_index}"
            worktree = staging / run_id
            shutil.rmtree(worktree, ignore_errors=True)
            subprocess.run(["git", "worktree", "prune"], cwd=root, check=False)
            outcome = run_rung(
                root=root,
                worktree=worktree,
                clean_commit=args.clean_commit,
                instance=instance,
                severity_index=severity_index,
                clean_hash=bank["clean_baseline_hash"],
                graph_rel=args.graph,
                seeds=args.seeds,
                tier=args.tier,
                embodiment=args.embodiment,
                perception=args.perception,
                run_id=run_id,
                env=env,
            )
            raw_dest = Path(args.raw_store) / run_id
            if Path(outcome["run_dir"]).exists():
                shutil.rmtree(raw_dest, ignore_errors=True)
                shutil.copytree(outcome["run_dir"], raw_dest)
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)], cwd=root, check=False
            )
            score = score_rung(
                clean_episodes, outcome["episodes"], sham=instance["family"] == "sham"
            )
            rungs.append(
                {
                    "opaque_id": instance["opaque_id"],
                    "family": instance["family"],
                    "severity_index": severity_index,
                    "sham": instance["family"] == "sham",
                    "receipt": outcome["receipt"],
                    "rollout_ok": bool(outcome["result"].get("ok")),
                    "rollout_error": outcome["result"].get("error"),
                    "episodes": outcome["episodes"],
                    "raw_store": str(raw_dest),
                    "score": score,
                }
            )
    report = calibration_report(
        bank["bank_id"], clean_run.name, rungs, campaign_id=args.campaign_id
    )
    report["ok"] = True
    return report
