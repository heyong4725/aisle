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
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from campaign import (  # noqa: E402
    DEV_SEEDS,
    HOLDOUT_SEEDS,
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


def scenario_slot(tier: str, attempt: int) -> str:
    """Attempt 1 keeps the plain tier name; reruns (PR #57 review:
    contaminated cells rerun under NEW ids) get their own dir and holdout
    tag suffix, never overwriting the flagged originals."""
    return tier if attempt == 1 else f"{tier}-r{attempt}"


def wipe_library(wt: Path, oid: str, keep_ref: str | None = None) -> dict:
    """Arm W between scenarios (ADR amendment): the working tree ends
    byte-exact at the pinned OID (detached HEAD) — including removal of
    files the agent COMMITTED on its branch — with agent graphs and the
    idea tree gone; the ledger and run artifacts persist (global budget
    continuity). The pre-wipe HEAD is pinned under keep_ref and recorded
    (PR #57 review: a detached-HEAD scenario's commits must stay durably
    reachable, not reflog-only), so agent history survives every wipe."""
    # detach the worktree AT the pin: `checkout <oid> -- .` only restores
    # paths present in the pin's tree, so files the agent COMMITTED on its
    # branch (tracked, absent from the pin) survived it AND `git clean` —
    # campaign-2 leak: s1-driver-v2 + research notes rode through both
    # arm-W wipes. Detaching makes the working tree exactly the pin's;
    # the agent's branches keep their commits for audit. Then remove ALL
    # untracked residue except runs/ (ledger + artifacts persist for
    # budget continuity).
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True, check=True
    ).stdout.strip()
    if keep_ref:
        subprocess.run(["git", "branch", "-f", keep_ref, "HEAD"], cwd=wt, check=True)
    subprocess.run(
        ["git", "checkout", "-f", "--detach", oid],
        cwd=wt,
        capture_output=True,
        text=True,
        check=True,
    )
    clean = subprocess.run(
        ["git", "clean", "-fdx", "-e", "runs"],
        cwd=wt,
        capture_output=True,
        text=True,
        check=True,
    )
    removed = [line.removeprefix("Removing ") for line in clean.stdout.splitlines()]
    ideas = wt / "runs" / "ideas"
    if ideas.exists():
        removed.append("runs/ideas/")
        shutil.rmtree(ideas)
    return {"removed": removed, "detached_from": head, "kept_ref": keep_ref}


def clear_nonlibrary_residue(wt: Path, oid: str, keep_ref: str | None = None) -> dict:
    """Arm L between scenarios (PR #60 review): arm L's persistence
    surface is the DEFINED library — registered skills (evalcarded
    manifest + skills/<id>/ code) + runs/ (idea tree, ledger) — not
    whatever working residue the previous session left. L/S1 left an
    unregistered skills/ dir and a working graph in worktree_L; carrying
    them into S2 would be untreated cross-scenario state. Mechanics:
    stash the registered library aside, detach byte-exact at the pin
    (removing stray untracked files, agent-COMMITTED files, and tracked
    modifications alike — same leak class as the arm-W wipe), clean
    everything but runs/, then restore the library. The pre-guard HEAD
    is pinned under keep_ref for audit, like the wipe."""
    import tempfile

    kept_skills = sorted(registered_skill_ids(wt))
    stash = Path(tempfile.mkdtemp(prefix="h3-library-"))
    for skill_id in kept_skills:
        manifest = wt / "registry" / "manifests" / f"{skill_id}.yaml"
        if manifest.exists():
            (stash / "manifests").mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest, stash / "manifests" / manifest.name)
        code_dir = wt / "skills" / skill_id
        if code_dir.exists():
            shutil.copytree(code_dir, stash / "skills" / skill_id)
    # the idea tree persists for arm L (D3: read-only lab notebook) —
    # wipe_library removes runs/ideas, so it rides the stash too
    ideas = wt / "runs" / "ideas"
    if ideas.exists():
        shutil.copytree(ideas, stash / "ideas")
    report = wipe_library(wt, oid, keep_ref=keep_ref)
    if (stash / "ideas").exists():
        shutil.copytree(stash / "ideas", wt / "runs" / "ideas")
    for skill_id in kept_skills:
        staged = stash / "manifests" / f"{skill_id}.yaml"
        if staged.exists():
            (wt / "registry" / "manifests").mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged, wt / "registry" / "manifests" / f"{skill_id}.yaml")
        staged_dir = stash / "skills" / skill_id
        if staged_dir.exists():
            shutil.copytree(staged_dir, wt / "skills" / skill_id)
    shutil.rmtree(stash)
    return {**report, "kept_skills": kept_skills}


def skill_reuse(deliverable: Path, prior_skill_ids: set[str]) -> list[str]:
    """The transfer signal (protocol point 5): nodes of the scored graph
    whose evalcarded manifest was registered in an EARLIER scenario.
    Agent-authored input: malformed YAML is no reuse, never a crash
    (PR #48 review)."""
    if not deliverable.exists():
        return []
    try:
        doc = yaml.safe_load(deliverable.read_text())
    except yaml.YAMLError:
        return []
    if not isinstance(doc, dict):
        return []
    node_ids = {n.get("id") for n in doc.get("nodes") or [] if isinstance(n, dict)}
    return sorted(node_ids & prior_skill_ids)


def registered_skill_ids(wt: Path) -> set[str]:
    """Agent-authored manifests with an evalcard, installed in the arm's
    registry (the library's current contents)."""
    ids = set()
    for path in (wt / "registry" / "manifests").glob("*.yaml"):
        try:
            m = yaml.safe_load(path.read_text())
        except yaml.YAMLError:
            continue  # agent-authored garbage is not a skill, not a crash
        if isinstance(m, dict) and m.get("origin") == "agent-authored" and m.get("eval"):
            ids.add(m.get("id"))
    return ids


def run_scenario(
    wt: Path,
    oid: str,
    arm: str,
    scenario: dict,
    out: Path,
    agent: str,
    model: str,
    attempt: int = 1,
) -> dict:
    tier = scenario["tier"]
    slot = scenario_slot(tier, attempt)
    session_dir = out / f"arm_{arm}" / slot
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
    holdout = score_holdout(wt, HOLDOUT_SEEDS, f"{arm}-{slot}", tier)
    sweep_worktree(wt)
    # since=t0 scopes EVERY aggregate to this scenario (PR #48 review:
    # unscoped first_success/wrong_object contaminated the H3 headline)
    metrics = campaign_metrics(wt, session_t0=t0, since=t0)
    reuse = skill_reuse(wt / "graphs" / "agent_campaign.yaml", prior_skills)
    worktree_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True
    ).stdout.strip()
    record = {
        "arm": arm,
        "tier": tier,
        "attempt": attempt,
        # the scenario's final worktree state, durably recorded (PR #57
        # review: scenario HEADs need recorded hashes, wipe pins the ref)
        "worktree_head": worktree_head,
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
    parser.add_argument(
        "--attempt",
        type=int,
        default=1,
        help="rerun counter: >1 writes S<t>-rN scenario dirs and holdout "
        "run ids, leaving flagged originals in place (PR #57 review)",
    )
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
    treatment["nudge_sha256"] = hashlib.sha256(NUDGE.encode()).hexdigest()
    treatment["budget_scale"] = args.budget_scale
    arms = [a for a in ARMS if a in args.arms.split(",")]
    tiers = [s["tier"] for s in SCENARIOS if s["tier"] in args.scenarios.split(",")]
    unknown = (set(args.arms.split(",")) - set(ARMS)) | (
        set(args.scenarios.split(",")) - {s["tier"] for s in SCENARIOS}
    )
    if unknown or not arms or not tiers:
        # a typo must refuse, not exit 0 with an empty "campaign" (PR #48)
        print(json.dumps({"ok": False, "error": f"bad selection: {sorted(unknown)}"}))
        return 1
    # a RERUN must never clobber the campaign's primary record
    # (self-review of PR #57: --attempt 2 would have overwritten
    # h3_results.json with the 2-record rerun output)
    results_path = args.out / f"h3_results{'' if args.attempt == 1 else f'-r{args.attempt}'}.json"
    if results_path.exists():
        # PR #60 review (resume path): a partial-arm invocation must not
        # CLOBBER the prior legs' aggregate — back it up first (numbered,
        # so successive resumes stack instead of overwriting each other)
        n = 1
        while (backup := args.out / f"h3_results-prev{n}.json").exists():
            n += 1
        shutil.copy2(results_path, backup)
        print(f"[h3] prior aggregate backed up to {backup.name}", file=sys.stderr)
    records = []
    wipes = []
    for arm in arms:
        wt = args.out / f"worktree_{arm}"
        if not wt.exists():
            print(f"[h3] arm {arm}: worktree at {oid[:8]}", file=sys.stderr)
            make_worktree(oid, wt)
        for index, scenario in enumerate(SCENARIOS):
            if scenario["tier"] not in tiers:
                continue
            if index > 0 or args.attempt > 1:
                slot = scenario_slot(scenario["tier"], args.attempt)
                if arm == "W":
                    wiped = wipe_library(wt, oid, keep_ref=f"h3/keep-{arm}-pre-{slot}")
                    print(f"[h3] arm W wiped {len(wiped['removed'])} path(s)", file=sys.stderr)
                else:
                    # PR #60 review: arm L carries ONLY its defined library
                    # forward — stray working residue is untreated state
                    wiped = clear_nonlibrary_residue(wt, oid, keep_ref=f"h3/keep-{arm}-pre-{slot}")
                    print(
                        f"[h3] arm L residue cleared ({len(wiped['removed'])} path(s), "
                        f"library kept: {wiped['kept_skills'] or 'none'})",
                        file=sys.stderr,
                    )
                wipes.append({"arm": arm, "before": slot, **wiped})
            scaled = {
                **scenario,
                "tokens": int(scenario["tokens"] * args.budget_scale),
                "wall_h": scenario["wall_h"] * args.budget_scale,
            }
            print(f"[h3] arm {arm} {scenario['tier']} starting", file=sys.stderr)
            try:
                records.append(
                    run_scenario(wt, oid, arm, scaled, args.out, agent, model, args.attempt)
                )
            except Exception as exc:  # noqa: BLE001 — protocol point 8
                # infra abort: keep prior records, attribute, stop the run
                records.append({"arm": arm, "tier": scenario["tier"], "infra_error": repr(exc)})
                results_path.write_text(
                    json.dumps(
                        {"ok": False, "treatment": treatment, "records": records, "wipes": wipes},
                        indent=1,
                    )
                )
                print(json.dumps({"ok": False, "error": f"infra abort: {exc!r}"}))
                return 1
    ok = all(not r.get("frozen_drift") for r in records)
    results_path.write_text(
        json.dumps({"ok": ok, "treatment": treatment, "records": records, "wipes": wipes}, indent=1)
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "record": str(results_path),
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
