"""H3 accumulation campaign orchestrator (ADR-h3-campaign-protocol,
accepted 2026-07-27; design doc §11.5, §8.4, hypothesis H3).

Two arms x S1→S2→S3, one worktree per arm at one pinned OID, fresh
session per scenario via the ADR-h2 machinery (tools/campaign.py). Arm W
(first, D6) is wiped back to the curated library before S2/S3; arm L
keeps its registered skills and read-only idea tree (D3). CON-8: JSON to
stdout, logs to stderr, exit 0 iff the campaign record was written.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from campaign import (  # noqa: E402
    agent_cmd_campaign,
    audit_frozen,
    campaign_metrics,
    campaign_prompt,
    campaign_treatment,
    resolve_commit,
    run_session,
    score_holdout,
    sweep_worktree,
    validate_seed_ranges,
)
from h1_protocol import DEFAULT_MODELS, make_worktree  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
ARMS = ("W", "L")  # D6: wiped arm first
# D2: per-arm sub-budgets (episodes are recorded targets; the frozen
# ledger enforces the global ceilings — ADR amendment)
SCENARIOS = (
    {"tier": "S1", "tokens": 1_000_000, "episodes": 80, "wall_h": 6.0},
    {"tier": "S2", "tokens": 750_000, "episodes": 60, "wall_h": 5.0},
    {"tier": "S3", "tokens": 750_000, "episodes": 60, "wall_h": 5.0},
)
# D5: identical in both arms — a nudge, not a treatment
NUDGE = "Distill what works into registered skills — they may pay off later."
DEV_SEEDS = "0..49"
HOLDOUT_SEEDS = "100..107"


def wipe_library(wt: Path, oid: str) -> dict:
    """Arm W between scenarios (ADR amendment): registry/manifests and
    skills/ byte-exact at the pinned OID, agent graphs and the idea tree
    removed; the ledger and run artifacts persist (global budget
    continuity)."""
    subprocess.run(["git", "checkout", oid, "--", "registry/manifests"], cwd=wt, check=True)
    removed = []
    clean = subprocess.run(
        ["git", "clean", "-fdx", "--", "registry/manifests", "skills", "graphs"],
        cwd=wt,
        capture_output=True,
        text=True,
        check=True,
    )
    removed += [line.removeprefix("Removing ") for line in clean.stdout.splitlines()]
    for idea_file in (wt / "runs" / "ideas").glob("*"):
        removed.append(str(idea_file.relative_to(wt)))
        idea_file.unlink()
    return {"removed": removed}


def skill_reuse(wt: Path, deliverable: Path, prior_skill_ids: set[str]) -> list[str]:
    """The transfer signal (protocol point 5): nodes of the scored graph
    whose evalcarded manifest was registered in an EARLIER scenario."""
    import yaml

    if not deliverable.exists():
        return []
    doc = yaml.safe_load(deliverable.read_text()) or {}
    node_ids = {n.get("id") for n in doc.get("nodes") or []}
    return sorted(node_ids & prior_skill_ids)


def registered_skill_ids(wt: Path) -> set[str]:
    """Agent-authored manifests with an evalcard, installed in the arm's
    registry (the library's current contents)."""
    import yaml

    ids = set()
    for path in (wt / "registry" / "manifests").glob("*.yaml"):
        m = yaml.safe_load(path.read_text()) or {}
        if m.get("origin") == "agent-authored" and m.get("eval"):
            ids.add(m.get("id"))
    return ids


def run_scenario(
    wt: Path, oid: str, arm: str, scenario: dict, out: Path, agent: str, model: str
) -> dict:
    tier = scenario["tier"]
    session_dir = out / f"arm_{arm}" / tier
    session_dir.mkdir(parents=True, exist_ok=True)
    prior_skills = registered_skill_ids(wt)
    prompt = campaign_prompt(tier, scenario["tokens"], scenario["wall_h"], DEV_SEEDS, note=NUDGE)
    t0 = time.time()
    session = run_session(
        agent,
        agent_cmd_campaign(agent, model, prompt),
        wt,
        session_dir,
        {
            "prior_tokens": 0,  # sub-budgets are per scenario (D2)
            "prior_wall_s": 0.0,
            "token_ceiling": scenario["tokens"],
            "wall_ceiling_s": scenario["wall_h"] * 3600.0,
        },
    )
    sweep_worktree(wt)
    drift = audit_frozen(wt, oid)
    holdout = score_holdout(wt, HOLDOUT_SEEDS, 0, tier)
    sweep_worktree(wt)
    metrics = campaign_metrics(wt, session_t0=t0)
    # scope the trajectory to THIS scenario's rollouts
    metrics["rollouts"] = [r for r in metrics["rollouts"] if r["mtime"] >= t0]
    reuse = skill_reuse(wt, wt / "graphs" / "agent_campaign.yaml", prior_skills)
    record = {
        "arm": arm,
        "tier": tier,
        "budgets": scenario,
        "session": session,
        "frozen_drift": drift,
        "holdout": {k: holdout.get(k) for k in ("ok", "error", "pass1", "pass8", "failures")},
        "first_success_wall_s": metrics["first_success_wall_s"],
        "wrong_object_total": metrics["wrong_object_total"],
        "rollouts": metrics["rollouts"],
        "prior_skills": sorted(prior_skills),
        "skills_after": sorted(registered_skill_ids(wt)),
        "skill_reuse_in_deliverable": reuse,
    }
    (session_dir / "scenario.json").write_text(json.dumps(record, indent=1))
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs" / "h3")
    parser.add_argument("--arms", default="W,L", help="subset, e.g. W (dry runs)")
    parser.add_argument("--scenarios", default="S1,S2,S3", help="subset, e.g. S1")
    parser.add_argument("--budget-scale", type=float, default=1.0, help="dry-run scaling")
    args = parser.parse_args()

    error = validate_seed_ranges(DEV_SEEDS, HOLDOUT_SEEDS)
    if error:
        print(json.dumps({"ok": False, "error": error}))
        return 1
    agent, model = "claude", DEFAULT_MODELS["claude"]  # D1
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    oid = resolve_commit(REPO_ROOT, args.commit)
    treatment = campaign_treatment(agent, model, oid, DEV_SEEDS, HOLDOUT_SEEDS)
    treatment["protocol"] = "ADR-h3-campaign-protocol"
    treatment["budget_scale"] = args.budget_scale
    arms = [a for a in ARMS if a in args.arms.split(",")]
    tiers = args.scenarios.split(",")
    records = []
    for arm in arms:
        wt = args.out / f"worktree_{arm}"
        if not wt.exists():
            print(f"[h3] arm {arm}: worktree at {oid[:8]}", file=sys.stderr)
            make_worktree(oid, wt)
        for scenario in SCENARIOS:
            if scenario["tier"] not in tiers:
                continue
            if arm == "W" and scenario["tier"] != "S1":
                wiped = wipe_library(wt, oid)
                print(f"[h3] arm W wiped {len(wiped['removed'])} path(s)", file=sys.stderr)
            scaled = {
                **scenario,
                "tokens": int(scenario["tokens"] * args.budget_scale),
                "wall_h": scenario["wall_h"] * args.budget_scale,
            }
            print(f"[h3] arm {arm} {scenario['tier']} starting", file=sys.stderr)
            records.append(run_scenario(wt, oid, arm, scaled, args.out, agent, model))
    ok = all(not r["frozen_drift"] for r in records)
    (args.out / "h3_results.json").write_text(
        json.dumps({"ok": ok, "treatment": treatment, "records": records}, indent=1)
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "record": str(args.out / "h3_results.json"),
                "scenarios_run": [f"{r['arm']}/{r['tier']}" for r in records],
                "holdout_pass1": {
                    f"{r['arm']}/{r['tier']}": r["holdout"].get("pass1") for r in records
                },
            }
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
