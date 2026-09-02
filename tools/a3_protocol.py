"""A3 ablation orchestrator (ADR-a3-protocol, ratified via PR #187):
params-only vs params+code authorship, two sequential T1 sessions at one
pinned OID. Arm F runs the standard research contract verbatim; arm P
runs the SAME contract with the params-only hard rule COMMITTED on the
arm's worktree before the session (the diff IS the treatment, its
sha256 recorded). Enforcement is audit-backed: the session's final
worktree diff against the pin must touch nothing under src/ or skills/,
else the cell records params_leak (excluded + rerun, never
direction-assumed). CON-8: JSON to stdout, logs to stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from campaign import (  # noqa: E402
    DEV_SEEDS,
    HOLDOUT_SEEDS,
    agent_cmd_campaign,
    campaign_metrics,
    campaign_prompt,
    campaign_treatment,
    isolated_session_env,
    probe_agent_auth,
    resolve_commit,
    run_session,
    score_holdout,
    scrub_session_credentials,
    seed_session_credentials,
    sweep_worktree,
)
from h1_protocol import DEFAULT_MODELS, make_worktree  # noqa: E402
from h3_campaign import host_dora_runtime  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
ARMS = ("F", "P")  # F first: a harness defect found on F biases AGAINST
# the novel arm's disadvantage claim (ADR-a3, mirroring ADR-h3 D6)
BUDGET = {"tokens": 400_000, "episodes": 40, "wall_h": 2.5}
CONTRACT = "harness/CLAUDE.research.md"
PARAMS_ONLY_RULE = """
## PARAMS-ONLY VARIANT (A3)

You MUST NOT author or edit node code or skills — no new or modified
files under `src/` or `skills/`, no new Python anywhere, and
`harness skill register` is off-limits. Your action space is registry
search, dataflow YAML composition and wiring, node `env` parameters in
the graph, and rollout configuration.
"""
# the audit surface: any post-pin change here on arm P is a leak
AUDIT_PREFIXES = ("src/", "skills/")


def a3_runner_identity() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def commit_contract_variant(wt: Path) -> str:
    """Arm P: append the params-only rule to the contract and COMMIT it
    (the contract is versioned by design; the diff is the treatment).
    Returns the sha256 of the appended rule text."""
    contract = wt / CONTRACT
    contract.write_text(contract.read_text() + PARAMS_ONLY_RULE)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "aisle-a3-protocol",
        "GIT_AUTHOR_EMAIL": "a3-protocol@aisle.invalid",
        "GIT_COMMITTER_NAME": "aisle-a3-protocol",
        "GIT_COMMITTER_EMAIL": "a3-protocol@aisle.invalid",
    }
    subprocess.run(["git", "add", CONTRACT], cwd=wt, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "a3: params-only contract variant (ADR-a3)"],
        cwd=wt,
        env=env,
        check=True,
    )
    return hashlib.sha256(PARAMS_ONLY_RULE.encode()).hexdigest()


def audit_params_surface(wt: Path, oid: str) -> list[str]:
    """Every path under src/ or skills/ that differs from the pin —
    tracked changes AND untracked files (a new file is not a diff)."""
    changed = subprocess.run(
        ["git", "diff", "--name-only", oid],
        cwd=wt,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=wt,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return sorted(p for p in {*changed, *untracked} if p.startswith(AUDIT_PREFIXES))


def run_arm(arm: str, oid: str, out: Path, budget_scale: float) -> dict:
    agent, model = "claude", DEFAULT_MODELS["claude"]
    arm_out = out / f"arm_{arm}"
    wt = out / f"worktree_{arm}"
    record: dict = {"arm": arm}
    try:
        arm_out.mkdir(parents=True, exist_ok=True)
        if not wt.exists():
            make_worktree(oid, wt)
        if arm == "P":
            record["contract_rule_sha256"] = commit_contract_variant(wt)
        tokens = int(BUDGET["tokens"] * budget_scale)
        wall_h = BUDGET["wall_h"] * budget_scale
        env, isolation = isolated_session_env(arm_out, env_baseline_oid=oid)
        cred, cred_error = seed_session_credentials(agent, env)
        if cred_error:
            raise RuntimeError(cred_error)
        isolation["credentials"] = cred
        prompt = campaign_prompt("T1", tokens, wall_h, DEV_SEEDS)
        t0 = time.time()
        session = run_session(
            agent,
            agent_cmd_campaign(agent, model, prompt),
            wt,
            arm_out,
            {
                "prior_tokens": 0,
                "prior_wall_s": 0.0,
                "token_ceiling": tokens,
                "wall_ceiling_s": wall_h * 3600.0,
            },
            env=env,
            environment_record=isolation["ambient_baseline"],
        )
        record |= {
            "session": session,
            "session_isolation": isolation,
            "session_start_epoch": t0,
            **campaign_metrics(wt, t0, pin=oid),
        }
        record["params_surface_diff"] = audit_params_surface(wt, oid)
        if arm == "P" and record["params_surface_diff"]:
            record["params_leak"] = True
        record["holdout"] = score_holdout(wt, HOLDOUT_SEEDS, f"a3-{arm}", "T1")
    except Exception as exc:  # noqa: BLE001 — the record IS the outcome
        record["infra_error"] = repr(exc)
    finally:
        if arm_out.exists():
            scrub_session_credentials(arm_out)
        record["swept_processes"] = sweep_worktree(wt) if wt.exists() else []
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="the campaign pin OID (ADR-a3)")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs" / "a3")
    parser.add_argument("--arms", default="F,P")
    parser.add_argument("--budget-scale", type=float, default=1.0)
    parser.add_argument("--expect-dora-sha256", default=None)
    args = parser.parse_args()

    if not args.expect_dora_sha256:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "--expect-dora-sha256 is required (ADR-h3 amendment §5 "
                    "inherited by ADR-a3): assert the pin-era dora CLI hash",
                }
            )
        )
        return 1
    arms = [a for a in ARMS if a in args.arms.split(",")]
    if not arms or set(args.arms.split(",")) - set(ARMS):
        print(json.dumps({"ok": False, "error": f"bad --arms selection {args.arms!r}"}))
        return 1
    runtime = host_dora_runtime()
    if runtime.get("sha256") != args.expect_dora_sha256:
        print(
            json.dumps(
                {"ok": False, "error": "dora CLI is not the pin-era binary", "found": runtime}
            )
        )
        return 1
    oid = resolve_commit(REPO_ROOT, args.commit)
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    agent, model = "claude", DEFAULT_MODELS["claude"]
    treatment = campaign_treatment(agent, model, oid, DEV_SEEDS, HOLDOUT_SEEDS)
    treatment |= {
        "protocol": "ADR-a3-protocol",
        "suite": "a3",
        "budget_scale": args.budget_scale,
        "a3_runner_sha256": a3_runner_identity(),
        "host_dora_cli": runtime,
        "arm_budget": BUDGET,
        "params_rule_sha256": hashlib.sha256(PARAMS_ONLY_RULE.encode()).hexdigest(),
    }
    probe_dir = args.out / "auth_probe"
    probe_dir.mkdir(exist_ok=True)
    env, _ = isolated_session_env(probe_dir, env_baseline_oid=oid)
    _, cred_error = seed_session_credentials(agent, env)
    error = cred_error or probe_agent_auth(agent, model, env, REPO_ROOT)
    scrub_session_credentials(probe_dir)
    if error:
        print(json.dumps({"ok": False, "error": error}))
        return 1

    records = []
    for arm in arms:  # sequential, F first (ADR-a3)
        print(f"[a3] arm {arm} starting", file=sys.stderr)
        records.append(run_arm(arm, oid, args.out, args.budget_scale))
        (args.out / "a3_results.json").write_text(
            json.dumps({"ok": True, "treatment": treatment, "records": records}, indent=1)
        )
        print(f"[a3] arm {arm} done", file=sys.stderr)

    summary = {
        f"arm_{r['arm']}": {
            "first_success_wall_s": r.get("first_success_wall_s"),
            "tokens": (r.get("session") or {}).get("tokens"),
            "holdout_pass1": (r.get("holdout") or {}).get("pass1"),
            "params_leak": r.get("params_leak", False),
            "infra_error": r.get("infra_error"),
        }
        for r in records
    }
    print(json.dumps({"ok": True, "record": str(args.out / "a3_results.json"), **summary}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
