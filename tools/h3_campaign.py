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
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from campaign import (  # noqa: E402
    DEV_SEEDS,
    HOLDOUT_SEEDS,
    agent_cmd_campaign,
    attach_historical_baseline_compat,
    audit_frozen,
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
# Desk-suite instantiation (design doc §8.4.2: the ASPIRE ablation is
# T1→T2→T3→T4; ADR-h3 desk amendment). Same D2 logic: per-arm totals
# (2.5M / 200 / 16 h) mirror the retail split so both arms fit the frozen
# harness/budget.toml ceilings with the same re-run headroom. T2 carries
# the largest share — its expert baseline is 0.08 (analysis/t2), so
# time-to-success there is the curve's expected inflection.
DESK_SCENARIOS = (
    {"tier": "T1", "tokens": 400_000, "episodes": 40, "wall_h": 2.5},
    {"tier": "T2", "tokens": 800_000, "episodes": 70, "wall_h": 6.0},
    {"tier": "T3", "tokens": 700_000, "episodes": 50, "wall_h": 4.5},
    {"tier": "T4", "tokens": 600_000, "episodes": 40, "wall_h": 3.0},
)
SUITES = {"retail": SCENARIOS, "desk": DESK_SCENARIOS}
# D5: identical in both arms — a nudge, not a treatment
NUDGE = "Distill what works into registered skills — they may pay off later."
# agent-controlled manifest ids are used as PATH COMPONENTS by the
# arm-L guard — only ids matching this survive (PR #61 review)
SKILL_ID_SAFE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def h3_runner_identity() -> str:
    """sha256 of THIS orchestrator: campaign.py's runner_sha256 does not
    cover h3_campaign.py, so treatment-policy changes here (e.g. the
    arm-L residue guard) must be recorded separately (PR #61 review)."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def backup_existing_results(results_path: Path) -> Path | None:
    """PR #60/#61 reviews: a partial-arm invocation must not CLOBBER the
    prior legs' aggregate — copy an existing results file to a numbered
    -prevN sibling (successive resumes stack)."""
    if not results_path.exists():
        return None
    n = 1
    while (backup := results_path.parent / f"h3_results-prev{n}.json").exists():
        n += 1
    shutil.copy2(results_path, backup)
    return backup


def rotate_occupied_slot(session_dir: Path) -> Path | None:
    """PR #61 review: reusing an occupied scenario dir corrupts telemetry
    (token_samples.jsonl APPENDS -> a fresh session lands after the
    aborted prefix and poisons tokens-to-first-success; session.jsonl
    opens 'w' -> the aborted transcript is destroyed). A non-empty slot
    is rotated aside, preserving the prior attempt's artifacts."""
    if not session_dir.exists() or not any(session_dir.iterdir()):
        return None
    n = 1
    while (dest := session_dir.parent / f"{session_dir.name}-superseded{n}").exists():
        n += 1
    session_dir.rename(dest)
    return dest


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
        # the audit ref must preserve UNTRACKED residue too (PR #61
        # review): build a snapshot commit (parent = pre-wipe HEAD) from a
        # throwaway index with the full working state, so every removed
        # file is recoverable via `git show <keep_ref>:<path>`. runs/ is
        # gitignored and stays out of the snapshot.
        with tempfile.TemporaryDirectory() as td:
            # PR #82 review: GIT_AUTHOR_*/GIT_COMMITTER_* OUTRANK `-c`
            # config, so ambient values (or empty ones — commit-tree
            # still exits 128 on an empty ident) must never reach the
            # machinery commit. All four identity variables are pinned
            # in the subprocess env; the snapshot's identity is the
            # campaign's, not whoever exported variables in this shell.
            env = {
                **os.environ,
                "GIT_INDEX_FILE": str(Path(td) / "index"),
                "GIT_AUTHOR_NAME": "aisle-h3-campaign",
                "GIT_AUTHOR_EMAIL": "h3-campaign@aisle.invalid",
                "GIT_COMMITTER_NAME": "aisle-h3-campaign",
                "GIT_COMMITTER_EMAIL": "h3-campaign@aisle.invalid",
            }
            subprocess.run(["git", "add", "-A"], cwd=wt, env=env, check=True)
            tree = subprocess.run(
                ["git", "write-tree"], cwd=wt, env=env, capture_output=True, text=True, check=True
            ).stdout.strip()
            # commit-tree needs a committer identity, and this snapshot is
            # campaign machinery rather than anyone's authorship. Pin one
            # instead of inheriting the operator's: a host with no global git
            # config (a CI runner, a fresh clone) has no identity to inherit
            # and `commit-tree` exits 128 — losing the residue evidence the
            # keep-ref exists to preserve.
            snap = subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=aisle-h3-campaign",
                    "-c",
                    "user.email=h3-campaign@aisle.invalid",
                    "commit-tree",
                    tree,
                    "-p",
                    head,
                    "-m",
                    f"pre-wipe snapshot ({keep_ref})",
                ],
                cwd=wt,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        subprocess.run(["git", "branch", "-f", keep_ref, snap], cwd=wt, check=True)
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


def clear_nonlibrary_residue(
    wt: Path, oid: str, keep_ref: str | None = None, keep_skills: list[str] | None = None
) -> dict:
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
    registered = registered_skill_ids(wt)
    if keep_skills is not None:
        # rerun allowlist (PR #61 review): a skill registered DURING a
        # failed attempt of this tier must not ride into its rerun — the
        # caller passes the tier's original prior_skills
        registered &= set(keep_skills)
    kept_skills, skipped_ids = [], []
    for skill_id in sorted(registered, key=str):
        # ids are AGENT-CONTROLLED and used as path components: refuse
        # traversal-shaped ids outright (PR #61 review)
        if isinstance(skill_id, str) and SKILL_ID_SAFE.fullmatch(skill_id):
            kept_skills.append(skill_id)
        else:
            skipped_ids.append(skill_id)
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
    return {**report, "kept_skills": kept_skills, "skipped_ids": skipped_ids}


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


def host_dora_runtime() -> dict:
    """CONTENT identity of the host dora CLI (PR #90 review round 4).
    The CLI/daemon is part of the treatment — a committed frozen hash
    cannot see an external executable change, and `dora --version`
    cannot either: it prints only CARGO_PKG_VERSION (7eb4a5f8b and
    cd597e705, the exact mismatch that invalidated S3-r3, BOTH report
    1.0.0-rc.4) and never inspects the pinned python API. The binary's
    sha256 is the trustworthy build artifact — any source or toolchain
    change moves it; the semver line is recorded for humans only."""
    path = shutil.which("dora")
    if path is None:
        return {"path": None, "sha256": None, "version": None, "error": "dora CLI not on PATH"}
    resolved = str(Path(path).resolve())
    try:
        digest = hashlib.sha256(Path(resolved).read_bytes()).hexdigest()
    except OSError as exc:
        return {"path": resolved, "sha256": None, "version": None, "error": str(exc)}
    try:
        proc = subprocess.run([resolved, "--version"], capture_output=True, text=True, timeout=30)
        version = proc.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        version = None
    return {"path": resolved, "sha256": digest, "version": version}


def runtime_drift_check(launch: dict, current: dict) -> dict | None:
    """Non-None iff the scenario's preflight CLI identity fails to prove
    it is the launch-captured binary. Fail closed: an UNRESOLVED identity
    is drift, and an equal semver is never sameness (round-4 review —
    two source revisions shared 1.0.0-rc.4)."""
    if not launch.get("sha256") or not current.get("sha256"):
        return {"launch": launch, "found": current, "reason": "unresolved CLI identity"}
    if current["sha256"] != launch["sha256"]:
        return {"launch": launch, "found": current, "reason": "CLI binary changed mid-campaign"}
    return None


def run_scenario(
    wt: Path,
    oid: str,
    arm: str,
    scenario: dict,
    out: Path,
    agent: str,
    model: str,
    attempt: int = 1,
    launch_runtime: dict | None = None,
) -> dict:
    # BRACKETING runtime identity (rounds 5-6: preflight alone leaves
    # the multi-hour session and holdout windows unguarded, and a
    # DETECTED preflight mismatch must refuse BEFORE the budget is
    # spent, not record-and-run) — captured here and re-captured after
    # the session (before holdout scoring) and after holdout; every
    # capture must match the launch binary
    runtime = host_dora_runtime()
    rt_baseline = launch_runtime or runtime
    rechecks: dict[str, dict] = {}
    preflight_drift = runtime_drift_check(rt_baseline, runtime)
    if preflight_drift is not None:
        # infra abort (protocol point 8): main() records the runner
        # error, marks the campaign non-OK, and stops — the scenario
        # re-runs after the operator restores the pinned runtime
        raise RuntimeError(f"runtime drift at scenario preflight: {preflight_drift}")
    rt_drift: dict | None = None
    tier = scenario["tier"]
    slot = scenario_slot(tier, attempt)
    session_dir = out / f"arm_{arm}" / slot
    rotated = rotate_occupied_slot(session_dir)
    if rotated is not None:
        print(f"[h3] occupied slot rotated to {rotated.name}", file=sys.stderr)
    session_dir.mkdir(parents=True, exist_ok=True)
    prior_skills = registered_skill_ids(wt)
    prompt = campaign_prompt(tier, scenario["tokens"], scenario["wall_h"], DEV_SEEDS, note=NUDGE)
    # issue #96: the session runs under an isolated home — operator
    # memory/config is not a treatment channel; recorded per scenario
    session_env, session_isolation = isolated_session_env(session_dir, env_baseline_oid=oid)
    session_isolation["baseline_compat"] = attach_historical_baseline_compat(
        wt, session_dir, oid, session_env
    )
    seed_rec, seed_error = seed_session_credentials(agent, session_env)
    if seed_error:
        # infra abort (protocol point 8), like the runtime preflight: a
        # missing campaign login must never fall back to the operator's
        raise RuntimeError(seed_error)
    session_isolation.update(seed_rec)
    t0 = time.time()
    try:
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
            env=session_env,
        )
    finally:
        # PR #100 review P1: the seeded token must not outlive the
        # session (holdout scoring below never needs agent credentials)
        session_isolation["credentials_scrubbed"] = scrub_session_credentials(
            Path(session_env["HOME"])
        )
    sweep_worktree(wt)
    rechecks["post_session"] = host_dora_runtime()
    rt_drift = rt_drift or runtime_drift_check(rt_baseline, rechecks["post_session"])
    drift = audit_frozen(wt, oid)
    holdout = score_holdout(wt, HOLDOUT_SEEDS, f"{arm}-{slot}", tier)
    sweep_worktree(wt)
    rechecks["post_holdout"] = host_dora_runtime()
    rt_drift = rt_drift or runtime_drift_check(rt_baseline, rechecks["post_holdout"])
    # since=t0 scopes EVERY aggregate to this scenario (PR #48 review:
    # unscoped first_success/wrong_object contaminated the H3 headline)
    metrics = campaign_metrics(wt, session_t0=t0, since=t0, pin=oid)
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
        "host_dora_cli": runtime,
        "host_dora_cli_rechecks": rechecks,
        "session_isolation": session_isolation,
    }
    if rt_drift is not None:
        # fail closed: the analyzer excludes any cell carrying a truthy
        # runtime_drift, and main() turns it into a non-OK campaign
        record["runtime_drift"] = rt_drift
    (session_dir / "scenario.json").write_text(json.dumps(record, indent=1))
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "runs" / "h3")
    parser.add_argument("--arms", default="W,L", help="subset, e.g. W (dry runs)")
    parser.add_argument(
        "--suite",
        default="retail",
        choices=sorted(SUITES),
        help="scenario suite: retail (S1→S3, the original ADR-h3 campaign) "
        "or desk (T1→T4, design doc §8.4.2 — the ASPIRE tier ladder)",
    )
    parser.add_argument(
        "--scenarios",
        default=None,
        help="subset, e.g. S1 or T1,T2; default = the whole selected suite",
    )
    parser.add_argument("--budget-scale", type=float, default=1.0, help="dry-run scaling")
    parser.add_argument(
        "--attempt",
        type=int,
        default=1,
        help="rerun counter: >1 writes S<t>-rN scenario dirs and holdout "
        "run ids, leaving flagged originals in place (PR #57 review)",
    )
    parser.add_argument(
        "--expect-dora-sha256",
        default=None,
        help="sha256 of the pin-era dora CLI binary — the operator's "
        "assertion that the host CLI matches the campaign pin (cargo-"
        "install at the pin rev, then `shasum -a 256 $(which dora)`). "
        "REQUIRED (enforced as a CON-8 JSON refusal, not an argparse "
        "error — round 6): an optional expectation let the S3-r3 "
        "mismatch class self-certify clean; a different or unresolved "
        "host CLI refuses to launch (ADR-h3 amendment §5)",
    )
    args = parser.parse_args()

    error = validate_seed_ranges(DEV_SEEDS, HOLDOUT_SEEDS)
    if error:
        print(json.dumps({"ok": False, "error": error}))
        return 1
    if not args.expect_dora_sha256:
        # CON-8: refusals are JSON on stdout, exit nonzero — argparse
        # `required` wrote usage to stderr with empty stdout (round 6)
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "--expect-dora-sha256 is required: the operator must assert "
                    "the pin-era dora CLI hash (ADR-h3 amendment §5)",
                }
            )
        )
        return 1
    suite = SUITES[args.suite]
    selected = args.scenarios or ",".join(s["tier"] for s in suite)
    arms = [a for a in ARMS if a in args.arms.split(",")]
    tiers = [s["tier"] for s in suite if s["tier"] in selected.split(",")]
    unknown = (set(args.arms.split(",")) - set(ARMS)) | (
        set(selected.split(",")) - {s["tier"] for s in suite}
    )
    if unknown or not arms or not tiers:
        # a typo must refuse, not exit 0 with an empty "campaign" (PR #48)
        print(json.dumps({"ok": False, "error": f"bad selection: {sorted(unknown)}"}))
        return 1
    agent, model = "claude", DEFAULT_MODELS["claude"]  # D1
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    oid = resolve_commit(REPO_ROOT, args.commit)
    treatment = campaign_treatment(agent, model, oid, DEV_SEEDS, HOLDOUT_SEEDS)
    treatment["protocol"] = "ADR-h3-campaign-protocol"
    treatment["suite"] = args.suite
    treatment["nudge_sha256"] = hashlib.sha256(NUDGE.encode()).hexdigest()
    treatment["budget_scale"] = args.budget_scale
    # PR #61 review: campaign.py's runner_sha256 does not cover THIS
    # orchestrator — record its identity so treatment-policy changes
    # (wipe/guard semantics) are visible in every campaign record
    treatment["h3_runner_sha256"] = h3_runner_identity()
    # runtime identity preflight (PR #90 round 4): the treatment includes
    # the host dora CLI as a CONTENT identity — an unresolved binary, or
    # one that differs from the operator-supplied pin-era hash, refuses
    # to launch rather than running a doomed treatment
    launch_runtime = host_dora_runtime()
    if not launch_runtime.get("sha256"):
        print(json.dumps({"ok": False, "error": f"unresolved dora CLI identity: {launch_runtime}"}))
        return 1
    if launch_runtime["sha256"] != args.expect_dora_sha256:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "dora CLI is not the pin-era binary",
                    "expected_sha256": args.expect_dora_sha256,
                    "found": launch_runtime,
                }
            )
        )
        return 1
    treatment["host_dora_cli"] = launch_runtime
    # issue #96 fail-closed: verify the agent can authenticate under the
    # ISOLATED home before any scenario spends budget — credential
    # stores keyed off the operator HOME break under isolation, and a
    # silent fallback to the operator home would reopen the memory
    # channel the isolation exists to close
    probe_env, _ = isolated_session_env(args.out / "auth_probe")
    _, seed_error = seed_session_credentials(agent, probe_env)
    if seed_error:
        print(json.dumps({"ok": False, "error": seed_error}))
        return 1
    try:
        probe_error = probe_agent_auth(agent, model, probe_env, args.out)
    finally:
        # tokens never persist, even past an unexpected probe exception
        scrub_session_credentials(Path(probe_env["HOME"]))
    if probe_error:
        print(json.dumps({"ok": False, "error": probe_error}))
        return 1
    treatment["session_isolation"] = {"home_isolated": True, "auth_probe": "passed"}
    # a RERUN must never clobber the campaign's primary record
    # (self-review of PR #57: --attempt 2 would have overwritten
    # h3_results.json with the 2-record rerun output)
    results_path = args.out / f"h3_results{'' if args.attempt == 1 else f'-r{args.attempt}'}.json"
    backup = backup_existing_results(results_path)
    if backup is not None:
        print(f"[h3] prior aggregate backed up to {backup.name}", file=sys.stderr)
    records = []
    wipes = []
    for arm in arms:
        wt = args.out / f"worktree_{arm}"
        if not wt.exists():
            print(f"[h3] arm {arm}: worktree at {oid[:8]}", file=sys.stderr)
            make_worktree(oid, wt)
        for index, scenario in enumerate(suite):
            if scenario["tier"] not in tiers:
                continue
            if index > 0 or args.attempt > 1:
                slot = scenario_slot(scenario["tier"], args.attempt)
                if arm == "W":
                    wiped = wipe_library(wt, oid, keep_ref=f"h3/keep-{arm}-pre-{slot}")
                    print(f"[h3] arm W wiped {len(wiped['removed'])} path(s)", file=sys.stderr)
                else:
                    # PR #60 review: arm L carries ONLY its defined library
                    # forward — stray working residue is untreated state.
                    # On a rerun, the library is further limited to the
                    # tier's ORIGINAL prior_skills (PR #61 review)
                    allow = None
                    if args.attempt > 1:
                        prev = args.out / f"arm_{arm}" / scenario["tier"] / "scenario.json"
                        if prev.exists():
                            allow = json.loads(prev.read_text()).get("prior_skills", [])
                    wiped = clear_nonlibrary_residue(
                        wt, oid, keep_ref=f"h3/keep-{arm}-pre-{slot}", keep_skills=allow
                    )
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
                    run_scenario(
                        wt,
                        oid,
                        arm,
                        scaled,
                        args.out,
                        agent,
                        model,
                        args.attempt,
                        launch_runtime=launch_runtime,
                    )
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
    # non-OK on frozen drift AND on runtime drift (PR #90 round 4): a
    # scenario that ran against a different CLI binary is not a clean
    # campaign result even though its record was written
    ok = all(not r.get("frozen_drift") and not r.get("runtime_drift") for r in records)
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
