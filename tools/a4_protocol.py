"""A4 agent-comparison orchestrator (ADR-a4-protocol, owner-directed):
one fresh isolated T1 research session per agent CLI (claude, codex) at
one pinned OID, identical budgets/contract/prompt, sequential, held-out
scoring per arm. CON-8: JSON to stdout, logs to stderr."""

from __future__ import annotations

import argparse
import hashlib
import json
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
AGENTS = ("claude", "codex")  # claude first (ADR-a4 direction-of-bias)
BUDGET = {"tokens": 400_000, "episodes": 40, "wall_h": 2.5}


def a4_runner_identity() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def run_arm(agent: str, oid: str, out: Path, budget_scale: float) -> dict:
    model = DEFAULT_MODELS[agent]
    arm_out = out / f"arm_{agent}"
    wt = out / f"worktree_{agent}"
    record: dict = {"agent": agent, "model": model}
    try:
        arm_out.mkdir(parents=True, exist_ok=True)
        if not wt.exists():
            make_worktree(oid, wt)
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
        record["holdout"] = score_holdout(wt, HOLDOUT_SEEDS, f"a4-{agent}", "T1")
    except Exception as exc:  # noqa: BLE001 — the record IS the outcome
        record["infra_error"] = repr(exc)
    finally:
        if arm_out.exists():
            scrub_session_credentials(arm_out)
        record["swept_processes"] = sweep_worktree(wt) if wt.exists() else []
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="the campaign pin OID (ADR-a4)")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs" / "a4")
    parser.add_argument("--agents", default="claude,codex")
    parser.add_argument("--budget-scale", type=float, default=1.0)
    parser.add_argument("--expect-dora-sha256", default=None)
    args = parser.parse_args()

    if not args.expect_dora_sha256:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "--expect-dora-sha256 is required (ADR-h3 amendment §5 "
                    "inherited by ADR-a4): assert the pin-era dora CLI hash",
                }
            )
        )
        return 1
    agents = [a for a in AGENTS if a in args.agents.split(",")]
    if not agents or set(args.agents.split(",")) - set(AGENTS):
        print(json.dumps({"ok": False, "error": f"bad --agents selection {args.agents!r}"}))
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
    treatment = {
        "protocol": "ADR-a4-protocol",
        "suite": "a4",
        "commit": oid,
        "dev_seeds": DEV_SEEDS,
        "holdout_seeds": HOLDOUT_SEEDS,
        "budget_scale": args.budget_scale,
        "a4_runner_sha256": a4_runner_identity(),
        "host_dora_cli": runtime,
        "arm_budget": BUDGET,
        "arms": {a: DEFAULT_MODELS[a] for a in agents},
        "contract_sha256": campaign_treatment(
            "claude", DEFAULT_MODELS["claude"], oid, DEV_SEEDS, HOLDOUT_SEEDS
        ).get("contract_sha256"),
    }
    # fail-closed auth probes for EVERY selected agent before any spend
    for agent in agents:
        probe_dir = args.out / f"auth_probe_{agent}"
        probe_dir.mkdir(exist_ok=True)
        env, _ = isolated_session_env(probe_dir, env_baseline_oid=oid)
        _, cred_error = seed_session_credentials(agent, env)
        error = cred_error or probe_agent_auth(agent, DEFAULT_MODELS[agent], env, REPO_ROOT)
        scrub_session_credentials(probe_dir)
        if error:
            print(json.dumps({"ok": False, "error": f"{agent}: {error}"}))
            return 1

    records = []
    for agent in agents:  # sequential, claude first (ADR-a4)
        print(f"[a4] arm {agent} starting", file=sys.stderr)
        records.append(run_arm(agent, oid, args.out, args.budget_scale))
        (args.out / "a4_results.json").write_text(
            json.dumps({"ok": True, "treatment": treatment, "records": records}, indent=1)
        )
        print(f"[a4] arm {agent} done", file=sys.stderr)

    summary = {
        f"arm_{r['agent']}": {
            "first_success_wall_s": r.get("first_success_wall_s"),
            "tokens": (r.get("session") or {}).get("tokens"),
            "holdout_pass1": (r.get("holdout") or {}).get("pass1"),
            "infra_error": r.get("infra_error"),
        }
        for r in records
    }
    print(json.dumps({"ok": True, "record": str(args.out / "a4_results.json"), **summary}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
