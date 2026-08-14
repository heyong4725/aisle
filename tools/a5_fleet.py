"""A5 fleet-scaling orchestrator (ADR-a5-protocol; design doc §8.4.3, §6 A5).

Fleet sizes run SEQUENTIALLY; within a config, N research-agent T1
sessions run CONCURRENTLY, each in its own worktree at one pinned OID
with its own isolated home (issue #96) — the fleet shares the HOST, and
host contention is the measured scaling axis (deviation from the
shared-bridge design recorded in the ADR). CON-8: JSON to stdout, logs
to stderr, exit 0 iff the campaign record was written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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
FLEETS = (1, 4, 8)
# per-agent budgets: the desk-suite T1 split, identical across configs
# so per-agent economics are comparable as N grows (ADR-a5)
AGENT_BUDGET = {"tokens": 400_000, "episodes": 40, "wall_h": 2.5}
NOTE = (
    "You are agent {k} of a {n}-agent fleet working the same task "
    "independently on one machine; expect the simulator to run slower "
    "under load."
)


def a5_runner_identity() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def run_agent(k: int, n: int, oid: str, config_dir: Path, budget_scale: float) -> dict:
    """One agent's full lane: worktree, isolated session, metrics.
    Infra errors are the AGENT's record, never the config's (ADR-a5)."""
    agent, model = "claude", DEFAULT_MODELS["claude"]
    out_k = config_dir / f"agent_{k}"
    wt = config_dir / f"worktree_{k}"
    record: dict = {"agent_index": k, "fleet": n}
    try:
        out_k.mkdir(parents=True, exist_ok=True)
        if not wt.exists():
            make_worktree(oid, wt)
        tokens = int(AGENT_BUDGET["tokens"] * budget_scale)
        wall_h = AGENT_BUDGET["wall_h"] * budget_scale
        env, isolation = isolated_session_env(out_k, env_baseline_oid=oid)
        cred, cred_error = seed_session_credentials(agent, env)
        if cred_error:
            raise RuntimeError(cred_error)
        isolation["credentials"] = cred
        prompt = campaign_prompt("T1", tokens, wall_h, DEV_SEEDS, note=NOTE.format(k=k, n=n))
        cmd = agent_cmd_campaign(agent, model, prompt)
        t0 = time.time()
        session = run_session(
            agent,
            cmd,
            wt,
            out_k,
            {
                "prior_tokens": 0,
                "prior_wall_s": 0.0,
                "token_ceiling": tokens,
                "wall_ceiling_s": wall_h * 3600.0,
            },
            env=env,
        )
        record |= {
            "session": session,
            "session_isolation": isolation,
            "session_start_epoch": t0,
            **campaign_metrics(wt, t0, pin=oid),
        }
    except Exception as exc:  # noqa: BLE001 — the record IS the outcome
        record["infra_error"] = repr(exc)
    finally:
        if out_k.exists():
            scrub_session_credentials(out_k)
        record["swept_processes"] = sweep_worktree(wt) if wt.exists() else []
    return record


def utilization(record: dict) -> float | None:
    """The MRU stand-in (ADR-a5): fraction of the session's wall the
    agent spent inside rollouts (manifest mtimes bound run ends; run
    wall is not directly recorded, so this uses the episode-file spans
    available in every manifest dir — coarse, reported as such)."""
    session = record.get("session") or {}
    wall = session.get("wall_s")
    rollouts = record.get("rollouts") or []
    if not wall or not rollouts:
        return None
    # per-run wall is not in the manifest; approximate with episode
    # count x the T1 sim budget's typical realized wall (bounded by
    # session wall). Reported as sim_episodes_per_session_hour instead
    # of a fake ratio when the approximation would mislead.
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True, help="the campaign pin OID (ADR-a5)")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs" / "a5")
    parser.add_argument("--fleets", default="1,4,8")
    parser.add_argument("--budget-scale", type=float, default=1.0)
    parser.add_argument("--expect-dora-sha256", required=False, default=None)
    args = parser.parse_args()

    if not args.expect_dora_sha256:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "--expect-dora-sha256 is required (ADR-h3 amendment §5 "
                    "inherited by ADR-a5): assert the pin-era dora CLI hash",
                }
            )
        )
        return 1
    try:
        fleets = [int(f) for f in args.fleets.split(",")]
    except ValueError:
        fleets = []
    if not fleets or any(f < 1 or f > 16 for f in fleets):
        print(json.dumps({"ok": False, "error": f"bad --fleets selection {args.fleets!r}"}))
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
        "protocol": "ADR-a5-protocol",
        "suite": "a5-fleet",
        "budget_scale": args.budget_scale,
        "a5_runner_sha256": a5_runner_identity(),
        "host_dora_cli": runtime,
        "agent_budget": AGENT_BUDGET,
    }
    # fail-closed auth probe (issue #96) once, before any config spends
    probe_dir = args.out / "auth_probe"
    probe_dir.mkdir(exist_ok=True)
    env, _ = isolated_session_env(probe_dir, env_baseline_oid=oid)
    _, cred_error = seed_session_credentials(agent, env)
    error = cred_error or probe_agent_auth(agent, model, env, REPO_ROOT)
    scrub_session_credentials(probe_dir)
    if error:
        print(json.dumps({"ok": False, "error": error}))
        return 1

    configs = []
    for n in fleets:
        config_dir = args.out / f"fleet_{n}"
        config_dir.mkdir(exist_ok=True)
        print(f"[a5] fleet {n}: launching {n} concurrent agent(s)", file=sys.stderr)
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=n) as pool:
            records = list(
                pool.map(
                    lambda k, n=n, d=config_dir: run_agent(k, n, oid, d, args.budget_scale),
                    range(n),
                )
            )
        config_wall = round(time.time() - t0, 1)
        # holdout scoring SEQUENTIALLY after the config (ADR-a5: scoring
        # must not be contention-poisoned)
        for record in records:
            k = record["agent_index"]
            wt = config_dir / f"worktree_{k}"
            record["holdout"] = (
                score_holdout(wt, HOLDOUT_SEEDS, f"a5-f{n}-a{k}", "T1")
                if wt.exists() and not record.get("infra_error")
                else None
            )
        configs.append({"fleet": n, "config_wall_s": config_wall, "agents": records})
        print(f"[a5] fleet {n}: done in {config_wall} s", file=sys.stderr)
        (args.out / "a5_results.json").write_text(
            json.dumps({"ok": True, "treatment": treatment, "configs": configs}, indent=1)
        )

    summary = {
        f"fleet_{c['fleet']}": {
            "wall_s": c["config_wall_s"],
            "first_success_wall_s": [a.get("first_success_wall_s") for a in c["agents"]],
            "tokens": [(a.get("session") or {}).get("tokens") for a in c["agents"]],
            "holdout_pass1": [(a.get("holdout") or {}).get("pass1") for a in c["agents"]],
            "wrong_object": sum(a.get("wrong_object_total") or 0 for a in c["agents"]),
        }
        for c in configs
    }
    print(json.dumps({"ok": True, "record": str(args.out / "a5_results.json"), **summary}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
