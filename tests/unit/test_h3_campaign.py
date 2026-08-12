"""Unit tests for tools/h3_campaign.py (ADR-h3-campaign-protocol,
accepted; H3). Pure orchestrator logic — no sim, no agent CLIs."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from h3_campaign import (  # noqa: E402
    ARMS,
    NUDGE,
    SCENARIOS,
    registered_skill_ids,
    skill_reuse,
    wipe_library,
)  # noqa: E402

pytestmark = pytest.mark.unit


def test_plan_matches_resolved_decisions():
    """D6: wiped arm first; D2: the accepted sub-budget split; scenario
    order is the transfer curve S1→S2→S3."""
    assert ARMS == ("W", "L")
    assert [s["tier"] for s in SCENARIOS] == ["S1", "S2", "S3"]
    assert [s["tokens"] for s in SCENARIOS] == [1_000_000, 750_000, 750_000]
    assert [s["wall_h"] for s in SCENARIOS] == [6.0, 5.0, 5.0]
    assert [s["episodes"] for s in SCENARIOS] == [80, 60, 60]


def test_nudge_is_identical_for_both_arms_by_construction():
    """D5: the nudge is a single constant injected into every scenario
    prompt — it cannot differ between arms."""
    from campaign import campaign_prompt

    prompt = campaign_prompt("S1", 1000, 1.0, "0..9", note=NUDGE)
    assert NUDGE in prompt
    assert "registered skills" in NUDGE


def _mini_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "wt"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "registry" / "manifests").mkdir(parents=True)
    (repo / "registry" / "manifests" / "oracle-pose.yaml").write_text(
        "id: oracle-pose\norigin: hub\neval: null\n"
    )
    (repo / "graphs").mkdir()
    (repo / "graphs" / "expert_t0.yaml").write_text("nodes: []\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "pin"],
        cwd=repo,
        check=True,
    )
    oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    return repo, oid


def test_wipe_restores_curated_state_and_preserves_ledger(tmp_path):
    """ADR amendment: the wipe restores registry/manifests byte-exact at
    the pinned OID, removes agent-authored manifests, skills/, agent
    graphs, and the idea tree — but the ledger and run artifacts persist
    (global budget continuity)."""
    wt, oid = _mini_repo(tmp_path)
    curated = wt / "registry" / "manifests" / "oracle-pose.yaml"
    original = curated.read_text()
    # simulate an S1 session's residue
    curated.write_text("id: oracle-pose\nTAMPERED\n")
    (wt / "registry" / "manifests" / "agent-skill.yaml").write_text(
        "id: agent-skill\norigin: agent-authored\neval: {pass_rate: 0.9}\n"
    )
    (wt / "skills" / "agent-skill").mkdir(parents=True)
    (wt / "skills" / "agent-skill" / "node.py").write_text("x")
    (wt / "graphs" / "agent_campaign.yaml").write_text("nodes: []\n")
    (wt / "runs" / "ideas").mkdir(parents=True)
    (wt / "runs" / "ideas" / "detached.jsonl").write_text("{}\n")
    ledger = wt / "runs" / "campaign_ledger.jsonl"
    ledger.write_text('{"entry": 1}\n')

    wipe_library(wt, oid)

    assert curated.read_text() == original  # byte-exact restore
    assert not (wt / "registry" / "manifests" / "agent-skill.yaml").exists()
    assert not (wt / "skills" / "agent-skill").exists()
    assert not (wt / "graphs" / "agent_campaign.yaml").exists()
    assert (wt / "graphs" / "expert_t0.yaml").exists()  # tracked graph kept
    assert not (wt / "runs" / "ideas" / "detached.jsonl").exists()
    assert ledger.read_text() == '{"entry": 1}\n'  # budget continuity


def test_wipe_removes_agent_committed_files(tmp_path):
    """Campaign-2 leak (arm W): files the agent COMMITS on its worktree
    branch are tracked and absent from the pin, so `checkout <pin> -- .`
    skipped them and `git clean` skipped them — s1-driver-v2 plus the S1
    research notes rode through BOTH arm-W wipes (recorded as
    prior_skills in the W/S2 and W/S3 scenario records). The wipe must
    remove committed-but-not-in-pin files while preserving runs/ and the
    agent's branch history for audit (ADR-h3 arm-W wipe surface)."""
    wt, oid = _mini_repo(tmp_path)
    subprocess.run(["git", "checkout", "-qb", "campaign/h3-s1-research"], cwd=wt, check=True)
    committed_skill = wt / "registry" / "manifests" / "s1-driver-v2.yaml"
    committed_skill.write_text("id: s1-driver-v2\norigin: agent-authored\neval: {pass_rate: 1}\n")
    (wt / "docs").mkdir()
    (wt / "docs" / "campaign-notes.md").write_text("what worked in S1\n")
    subprocess.run(["git", "add", "registry", "docs"], cwd=wt, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "register skill"],
        cwd=wt,
        check=True,
    )
    ledger = wt / "runs" / "campaign_ledger.jsonl"
    ledger.parent.mkdir()
    ledger.write_text('{"entry": 1}\n')

    wipe_library(wt, oid)

    assert not committed_skill.exists()  # the campaign-2 leak, closed
    assert not (wt / "docs" / "campaign-notes.md").exists()
    assert (wt / "graphs" / "expert_t0.yaml").exists()  # pin tree intact
    assert ledger.read_text() == '{"entry": 1}\n'  # budget continuity
    branch_head = subprocess.run(
        ["git", "rev-parse", "campaign/h3-s1-research"], cwd=wt, capture_output=True, text=True
    )
    assert branch_head.returncode == 0  # audit trail: agent branch survives


def test_wipe_keep_ref_preserves_detached_commits(tmp_path):
    """PR #57 review P1: detaching during wipes can orphan commits made
    on a detached HEAD in the PREVIOUS scenario — the wipe must first pin
    the pre-wipe HEAD under a durable ref and record its hash, so every
    scenario's final state stays reachable for audit."""
    wt, oid = _mini_repo(tmp_path)
    subprocess.run(["git", "checkout", "-q", "--detach", oid], cwd=wt, check=True)
    (wt / "detached_work.md").write_text("scenario state committed on detached HEAD\n")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "detached"],
        cwd=wt,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True
    ).stdout.strip()

    report = wipe_library(wt, oid, keep_ref="h3/keep-W-pre-S2")

    assert report["detached_from"] == head and report["kept_ref"] == "h3/keep-W-pre-S2"
    kept = subprocess.run(
        ["git", "rev-parse", "h3/keep-W-pre-S2^"], cwd=wt, capture_output=True, text=True
    )
    # keep_ref is a SNAPSHOT commit (PR #61: untracked state included);
    # its parent is the pre-wipe HEAD — durable, not reflog-only
    assert kept.stdout.strip() == head
    assert not (wt / "detached_work.md").exists()  # ...and still wiped


def test_scenario_slot_names_reruns_with_new_ids():
    """PR #57 review P1: contaminated cells are rerun under NEW ids —
    attempt 2 gets its own scenario dir and holdout run tag, never
    overwriting the flagged originals."""
    from h3_campaign import scenario_slot

    assert scenario_slot("S2", 1) == "S2"
    assert scenario_slot("S2", 3) == "S2-r3"


def test_skill_reuse_counts_only_prior_evalcarded_skills(tmp_path):
    """Protocol point 5: the transfer signal counts deliverable nodes
    whose evalcarded manifest predates the scenario — not curated nodes,
    not this-scenario registrations."""
    wt = tmp_path
    (wt / "registry" / "manifests").mkdir(parents=True)
    for mid, origin, eval_ in (
        ("nav-helper", "agent-authored", "{pass_rate: 0.9}"),
        ("new-this-scenario", "agent-authored", "{pass_rate: 0.8}"),
        ("oracle-pose", "hub", "null"),
    ):
        (wt / "registry" / "manifests" / f"{mid}.yaml").write_text(
            f"id: {mid}\norigin: {origin}\neval: {eval_}\n"
        )
    deliverable = wt / "graphs" / "agent_campaign.yaml"
    deliverable.parent.mkdir()
    deliverable.write_text(
        yaml.safe_dump(
            {"nodes": [{"id": n} for n in ("nav-helper", "new-this-scenario", "oracle-pose")]}
        )
    )
    assert registered_skill_ids(wt) == {"nav-helper", "new-this-scenario"}
    prior = {"nav-helper"}  # snapshot taken BEFORE the scenario
    assert skill_reuse(deliverable, prior) == ["nav-helper"]
    assert skill_reuse(wt / "missing.yaml", prior) == []


def test_scenario_tiers_are_mobile_scored():
    """RS suite: S-tier holdout scoring must pass --embodiment mobile
    (the store graphs are mobile-only per the research contract)."""
    from campaign import TIER_EMBODIMENT

    assert TIER_EMBODIMENT["S1"] == TIER_EMBODIMENT["S2"] == TIER_EMBODIMENT["S3"] == "mobile"
    assert TIER_EMBODIMENT["T1"] == "franka"


def test_wipe_restores_tracked_files_repo_wide(tmp_path):
    """PR #48 adversarial review: a MODIFIED tracked file anywhere (an
    expert graph, a src helper) is surviving agent state — the wipe now
    restores every tracked file and removes all untracked residue except
    runs/."""
    wt, oid = _mini_repo(tmp_path)
    tracked_graph = wt / "graphs" / "expert_t0.yaml"
    original = tracked_graph.read_text()
    tracked_graph.write_text("nodes: [TAMPERED]\n")
    stash = wt / "src_helper_stash.py"  # untracked, outside old pathspecs
    stash.write_text("secret prior-scenario logic")
    (wt / "runs").mkdir()
    (wt / "runs" / "campaign_ledger.jsonl").write_text("{}\n")
    wipe_library(wt, oid)
    assert tracked_graph.read_text() == original
    assert not stash.exists()
    assert (wt / "runs" / "campaign_ledger.jsonl").exists()


def test_wipe_survives_missing_surfaces(tmp_path):
    """PR #48 review: no skills/, no runs/ideas — the wipe is a no-op for
    absent surfaces, never an error."""
    wt, oid = _mini_repo(tmp_path)
    wipe_library(wt, oid)


def test_malformed_agent_yaml_never_crashes(tmp_path):
    """PR #48 adversarial review: manifests and the deliverable are
    agent-controlled — malformed YAML must be skipped, not abort a
    multi-hour campaign unattributed."""
    (tmp_path / "registry" / "manifests").mkdir(parents=True)
    (tmp_path / "registry" / "manifests" / "bad.yaml").write_text("{: [unclosed")
    (tmp_path / "registry" / "manifests" / "list.yaml").write_text("- just\n- a list\n")
    (tmp_path / "registry" / "manifests" / "good.yaml").write_text(
        "id: ok-skill\norigin: agent-authored\neval: {pass_rate: 0.9}\n"
    )
    assert registered_skill_ids(tmp_path) == {"ok-skill"}
    bad_deliverable = tmp_path / "g.yaml"
    bad_deliverable.write_text("{: [unclosed")
    assert skill_reuse(bad_deliverable, {"ok-skill"}) == []
    bad_deliverable.write_text("- scalar\n- nodes\n")
    assert skill_reuse(bad_deliverable, {"ok-skill"}) == []


def test_campaign_metrics_since_scopes_all_aggregates(tmp_path):
    """PR #48 review (the H3-headline bug): in a persistent worktree,
    first_success and wrong_object from PRIOR scenarios must not leak
    into this scenario's record."""
    import os

    from campaign import campaign_metrics

    old_run = tmp_path / "runs" / "s1-old"
    old_run.mkdir(parents=True)
    (old_run / "manifest.json").write_text('{"run_id": "s1-old"}')
    ep = old_run / "episodes.jsonl"
    ep.write_text('{"episode": 0, "status": "success", "failure": null}\n')
    os.utime(ep, (1_000_000, 1_000_000))
    os.utime(old_run / "manifest.json", (1_000_000, 1_000_000))
    metrics = campaign_metrics(tmp_path, session_t0=2_000_000.0, since=2_000_000.0)
    assert metrics["rollouts"] == []
    assert metrics["first_success_wall_s"] is None  # S1's success must not leak
    assert metrics["wrong_object_total"] == 0
    assert metrics["episodes_total"] == 0


def test_holdout_run_tags_are_unique_per_scenario():
    """PR #48 review: session_index=0 collided run-ids from S2 onward,
    losing pass@1 for 4/6 scenarios; tags are arm+slot strings, where the
    slot carries the rerun suffix (PR #57 review: reruns get NEW ids)."""
    import inspect

    import h3_campaign

    src = inspect.getsource(h3_campaign.run_scenario)
    assert 'f"{arm}-{slot}"' in src


def test_holdout_scoring_argv_carries_tier_and_embodiment(tmp_path, monkeypatch):
    """PR #48 review: assert the BUILT command, not just the mapping —
    S-tier holdout scoring must pass --tier S1 --embodiment mobile and
    the unique run tag."""
    import campaign as c

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class R:
            stdout = '{"ok": true}'
            returncode = 0

        return R()

    monkeypatch.setattr(c.subprocess, "run", fake_run)
    (tmp_path / "graphs").mkdir()
    (tmp_path / "graphs" / "agent_campaign.yaml").write_text("nodes: []\n")
    c.score_holdout(tmp_path, "100..107", "W-S1", "S1")
    cmd = captured["cmd"]
    assert "--tier" in cmd and cmd[cmd.index("--tier") + 1] == "S1"
    assert "--embodiment" in cmd and cmd[cmd.index("--embodiment") + 1] == "mobile"
    assert "campaign-holdout-W-S1" in cmd


def test_selection_typos_refuse():
    """PR #48 review: a typo'd --arms/--scenarios must refuse (CON-8),
    never exit 0 with an empty campaign record."""
    import subprocess as sp

    proc = sp.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "h3_campaign.py"),
            "--arms",
            "w",
            "--expect-dora-sha256",
            "0" * 64,
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert '"ok": false' in proc.stdout.lower()


def test_holdout_timeout_scales_and_records(tmp_path, monkeypatch):
    """H3 dry-run finding: S-tier scoring needs ~5h, and a timeout is a
    recorded outcome, never a campaign abort."""
    import campaign as c

    seen = {}

    def fake_run(cmd, timeout=None, **kw):
        seen["timeout"] = timeout
        raise c.subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(c.subprocess, "run", fake_run)
    (tmp_path / "graphs").mkdir()
    (tmp_path / "graphs" / "agent_campaign.yaml").write_text("nodes: []\n")
    out = c.score_holdout(tmp_path, "100..107", "W-S1", "S1")
    assert seen["timeout"] > 17000
    assert out["ok"] is False and "exceeded" in out["error"]


def test_s_tier_prompt_warns_about_slow_rollouts():
    from campaign import campaign_prompt

    prompt = campaign_prompt("S1", 1000, 1.0, "0..9")
    assert "TENS OF MINUTES" in prompt
    # measured-campaign S1 failure: the agent backgrounded its rollout and
    # "waited" — fatal in -p mode; the prompt must forbid it explicitly
    assert "NON-INTERACTIVE" in prompt and "NEVER background" in prompt


def test_rerun_results_file_never_clobbers_the_campaign_record():
    """PR #57 self-review: --attempt 2 must write h3_results-r2.json —
    overwriting h3_results.json would destroy the primary campaign
    record the rerun exists to repair."""
    import inspect

    import h3_campaign

    src = inspect.getsource(h3_campaign.main)
    assert "h3_results{'' if args.attempt == 1 else f'-r{args.attempt}'}" in src


def test_arm_l_residue_guard_keeps_only_the_defined_library(tmp_path):
    """PR #60 review: arm L's persistence surface is the DEFINED library
    (registered skills + idea tree + ledger), not whatever working
    residue the previous session left. The L/S1 session left an
    unregistered skills/ dir and a working graph in worktree_L; a naive
    resume would carry them into S2 as untreated state. The guard must
    remove stray untracked files, agent-COMMITTED files, and tracked
    modifications, while preserving registered skills (manifest + code),
    runs/ (ledger + idea tree), and the agent's branch history."""
    from h3_campaign import clear_nonlibrary_residue

    wt, oid = _mini_repo(tmp_path)
    subprocess.run(["git", "checkout", "-qb", "campaign/l-s1"], cwd=wt, check=True)
    # 1. a REGISTERED skill (evalcarded manifest + code dir): the library
    (wt / "registry" / "manifests" / "nav-helper.yaml").write_text(
        "id: nav-helper\norigin: agent-authored\neval: {pass_rate: 0.9}\n"
    )
    (wt / "skills" / "nav-helper").mkdir(parents=True)
    (wt / "skills" / "nav-helper" / "node.py").write_text("the registered code")
    # 2. UNREGISTERED residue: working graph + no-evalcard skill dir
    (wt / "graphs" / "agent_campaign.yaml").write_text("nodes: []\n")
    (wt / "skills" / "wip-skill").mkdir(parents=True)
    (wt / "skills" / "wip-skill" / "draft.py").write_text("unregistered")
    # 3. agent-COMMITTED non-library file + tracked modification
    (wt / "notes.md").write_text("cross-scenario notes")
    subprocess.run(["git", "add", "notes.md"], cwd=wt, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "notes"],
        cwd=wt,
        check=True,
    )
    (wt / "graphs" / "expert_t0.yaml").write_text("nodes: [TAMPERED]\n")
    # 4. runs/ = ledger + idea tree
    (wt / "runs" / "ideas").mkdir(parents=True)
    (wt / "runs" / "ideas" / "l.jsonl").write_text('{"id": "I1"}\n')
    (wt / "runs" / "campaign_ledger.jsonl").write_text('{"entry": 1}\n')

    report = clear_nonlibrary_residue(wt, oid, keep_ref="h3/keep-L-pre-S2")

    # library preserved
    assert (wt / "registry" / "manifests" / "nav-helper.yaml").exists()
    assert (wt / "skills" / "nav-helper" / "node.py").read_text() == "the registered code"
    # residue gone: unregistered, committed, and tracked-modified
    assert not (wt / "graphs" / "agent_campaign.yaml").exists()
    assert not (wt / "skills" / "wip-skill").exists()
    assert not (wt / "notes.md").exists()
    assert "TAMPERED" not in (wt / "graphs" / "expert_t0.yaml").read_text()
    # runs/ intact
    assert (wt / "runs" / "ideas" / "l.jsonl").exists()
    assert (wt / "runs" / "campaign_ledger.jsonl").exists()
    # audit: pre-guard HEAD durable, kept skills reported
    assert report["kept_skills"] == ["nav-helper"]
    kept = subprocess.run(
        ["git", "rev-parse", "h3/keep-L-pre-S2^"], cwd=wt, capture_output=True, text=True
    )
    # snapshot-commit semantics (PR #61): parent = pre-guard HEAD
    assert kept.returncode == 0 and kept.stdout.strip() == report["detached_from"]


def test_results_backup_stacks_behaviorally(tmp_path):
    """PR #61 review: test the BEHAVIOR, not the source text. An existing
    aggregate is copied to h3_results-prev1.json; a second invocation
    stacks to -prev2; a missing file is a no-op returning None."""
    from h3_campaign import backup_existing_results

    results = tmp_path / "h3_results.json"
    assert backup_existing_results(results) is None  # nothing to back up
    results.write_text('{"leg": 1}')
    b1 = backup_existing_results(results)
    assert b1.name == "h3_results-prev1.json" and b1.read_text() == '{"leg": 1}'
    results.write_text('{"leg": 2}')
    b2 = backup_existing_results(results)
    assert b2.name == "h3_results-prev2.json" and b2.read_text() == '{"leg": 2}'
    assert b1.read_text() == '{"leg": 1}'  # prev1 untouched


def test_rerun_guard_carries_only_the_pre_tier_library(tmp_path):
    """PR #61 review: a skill registered DURING a failed attempt of tier
    T must not ride into T's rerun and be counted as prior-tier reuse —
    the rerun replays the scenario as it originally started. The guard
    takes an explicit keep_skills allowlist (main derives it from the
    attempt-1 record's prior_skills); evalcarded skills outside the
    allowlist are removed like any residue."""
    from h3_campaign import clear_nonlibrary_residue

    wt, oid = _mini_repo(tmp_path)
    for sid in ("from-s1", "from-failed-s2"):
        (wt / "registry" / "manifests" / f"{sid}.yaml").write_text(
            f"id: {sid}\norigin: agent-authored\neval: {{pass_rate: 1.0}}\n"
        )
        (wt / "skills" / sid).mkdir(parents=True)
        (wt / "skills" / sid / "node.py").write_text(sid)

    report = clear_nonlibrary_residue(wt, oid, keep_skills=["from-s1"])

    assert (wt / "skills" / "from-s1" / "node.py").exists()
    assert not (wt / "skills" / "from-failed-s2").exists()
    assert not (wt / "registry" / "manifests" / "from-failed-s2.yaml").exists()
    assert report["kept_skills"] == ["from-s1"]


def test_guard_refuses_hostile_skill_ids(tmp_path):
    """PR #61 review: manifest ids are AGENT-CONTROLLED and used as path
    components — a traversal-shaped id must be skipped (and reported),
    never joined into a filesystem path."""
    from h3_campaign import clear_nonlibrary_residue

    wt, oid = _mini_repo(tmp_path)
    (wt / "registry" / "manifests" / "evil.yaml").write_text(
        'id: "../../escape"\norigin: agent-authored\neval: {pass_rate: 1.0}\n'
    )
    (wt / "registry" / "manifests" / "ok-skill.yaml").write_text(
        "id: ok-skill\norigin: agent-authored\neval: {pass_rate: 1.0}\n"
    )
    (wt / "skills" / "ok-skill").mkdir(parents=True)
    (wt / "skills" / "ok-skill" / "node.py").write_text("x")

    report = clear_nonlibrary_residue(wt, oid)

    assert report["kept_skills"] == ["ok-skill"]
    assert report["skipped_ids"] == ["../../escape"]
    assert not (tmp_path / "escape").exists()  # nothing written outside the worktree
    assert (wt / "skills" / "ok-skill" / "node.py").exists()


def test_occupied_scenario_slot_is_rotated_aside(tmp_path):
    """PR #61 review: reusing an occupied scenario dir corrupts token
    telemetry (token_samples.jsonl appends -> a new session lands after
    the aborted prefix; session.jsonl opens 'w' -> the aborted transcript
    is destroyed). A non-empty slot is rotated to <slot>-supersededN,
    preserving the prior attempt's artifacts; rotations stack."""
    from h3_campaign import rotate_occupied_slot

    slot = tmp_path / "S2"
    assert rotate_occupied_slot(slot) is None  # absent: no-op
    slot.mkdir()
    assert rotate_occupied_slot(slot) is None  # empty: no-op
    (slot / "token_samples.jsonl").write_text('{"wall_s": 5.0, "tokens": 0}\n')
    moved = rotate_occupied_slot(slot)
    assert moved.name == "S2-superseded1" and not slot.exists()
    assert (moved / "token_samples.jsonl").exists()  # aborted telemetry preserved
    slot.mkdir()
    (slot / "session.jsonl").write_text("{}\n")
    assert rotate_occupied_slot(slot).name == "S2-superseded2"


def test_keep_ref_snapshot_preserves_untracked_residue(tmp_path):
    """PR #61 review: the keep-ref pinned only the pre-wipe HEAD, which
    cannot contain UNTRACKED residue — the audit could not show what was
    removed. The keep-ref now points at a snapshot COMMIT (parent =
    pre-wipe HEAD) whose tree includes the untracked state, so removed
    files are recoverable via `git show <keep_ref>:<path>`."""
    wt, oid = _mini_repo(tmp_path)
    (wt / "graphs" / "agent_campaign.yaml").write_text("nodes: [the removed residue]\n")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True
    ).stdout.strip()

    report = wipe_library(wt, oid, keep_ref="h3/keep-W-pre-S2")

    assert not (wt / "graphs" / "agent_campaign.yaml").exists()  # still wiped
    parent = subprocess.run(
        ["git", "rev-parse", "h3/keep-W-pre-S2^"], cwd=wt, capture_output=True, text=True
    ).stdout.strip()
    assert parent == head == report["detached_from"]
    recovered = subprocess.run(
        ["git", "show", "h3/keep-W-pre-S2:graphs/agent_campaign.yaml"],
        cwd=wt,
        capture_output=True,
        text=True,
    )
    assert recovered.returncode == 0 and "the removed residue" in recovered.stdout


def test_keep_ref_snapshot_needs_no_ambient_git_identity(tmp_path, monkeypatch):
    """The snapshot commit must not depend on the operator's git config.
    `git commit-tree` requires a committer identity, so on a host with no
    global config -- a CI runner, a fresh clone -- it exited 128 and the
    wipe lost the residue evidence the keep-ref exists to preserve. Passing
    an explicit machinery identity makes the snapshot host-independent.

    Reproduces the failure by hiding the global and system git config
    rather than by trusting the test host to have none.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.delenv("EMAIL", raising=False)

    wt, oid = _mini_repo(tmp_path)
    (wt / "graphs" / "agent_campaign.yaml").write_text("nodes: [residue]\n")

    # PR #82 review: environment variables OUTRANK `-c` config — seed
    # HOSTILE ambient identity AFTER the fixture repo is built (a
    # conflicting name, an EMPTY author name that crashes commit-tree
    # despite -c) instead of only deleting variables, and assert the
    # recorded identity is the machinery's
    monkeypatch.setenv("GIT_AUTHOR_NAME", "")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "operator@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Ambient Operator")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "operator@example.com")

    report = wipe_library(wt, oid, keep_ref="h3/keep-W-pre-S2")

    assert report["detached_from"]
    recovered = subprocess.run(
        ["git", "show", "h3/keep-W-pre-S2:graphs/agent_campaign.yaml"],
        cwd=wt,
        capture_output=True,
        text=True,
    )
    assert recovered.returncode == 0 and "residue" in recovered.stdout
    ident = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>|%cn <%ce>", "h3/keep-W-pre-S2"],
        cwd=wt,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert ident == (
        "aisle-h3-campaign <h3-campaign@aisle.invalid>|"
        "aisle-h3-campaign <h3-campaign@aisle.invalid>"
    )


def test_h3_runner_identity_is_the_orchestrator_hash():
    """PR #61 review: campaign.py's runner_sha256 does not cover
    h3_campaign.py, so treatment-policy changes (like the arm-L residue
    guard) were invisible in recorded identity. h3_runner_identity()
    hashes the orchestrator itself; main records it in the treatment."""
    import hashlib as _hashlib

    from h3_campaign import h3_runner_identity

    expected = _hashlib.sha256((REPO_ROOT / "tools" / "h3_campaign.py").read_bytes()).hexdigest()
    assert h3_runner_identity() == expected


def _fake_dora(tmp_path, name, body_comment):
    """An executable printing the SAME semver as real dora builds do, with
    binary content varied via a comment — equal version, different sha."""
    d = tmp_path / name
    d.mkdir()
    exe = d / "dora"
    exe.write_text(f"#!/bin/sh\n# {body_comment}\necho 'dora-cli 1.0.0-rc.4'\n")
    exe.chmod(0o755)
    return d


def test_equal_semver_different_binary_is_runtime_drift(tmp_path, monkeypatch):
    """PR #90 round 4: dora's build_version_string() is only
    CARGO_PKG_VERSION — 7eb4a5f8b and cd597e705 BOTH report 1.0.0-rc.4,
    so a version string can never prove runtime identity. Two builds
    with the SAME semver but different content must yield different
    sha256 identities, and runtime_drift_check must flag the pair."""
    from h3_campaign import host_dora_runtime, runtime_drift_check

    monkeypatch.setenv("PATH", str(_fake_dora(tmp_path, "pin_era", "rev 7eb4a5f8b")))
    launch = host_dora_runtime()
    monkeypatch.setenv("PATH", str(_fake_dora(tmp_path, "post_85", "rev cd597e705")))
    current = host_dora_runtime()

    assert launch["version"] == current["version"] == "dora-cli 1.0.0-rc.4"
    assert launch["sha256"] and current["sha256"]
    assert launch["sha256"] != current["sha256"]
    drift = runtime_drift_check(launch, current)
    assert drift is not None and drift["reason"] == "CLI binary changed mid-campaign"
    # the same binary re-captured is NOT drift
    assert runtime_drift_check(launch, dict(launch)) is None


def test_unresolved_cli_identity_fails_closed(tmp_path, monkeypatch):
    """A missing or unhashable CLI cannot prove the treatment runtime:
    launch preflight refuses (main returns non-OK) and the per-scenario
    check reports drift rather than assuming cleanliness."""
    from h3_campaign import host_dora_runtime, runtime_drift_check

    monkeypatch.setenv("PATH", str(tmp_path))  # no dora anywhere
    missing = host_dora_runtime()
    assert missing["sha256"] is None
    good = {"path": "/x/dora", "sha256": "a" * 64, "version": "dora-cli 1.0.0-rc.4"}
    assert runtime_drift_check(good, missing)["reason"] == "unresolved CLI identity"
    assert runtime_drift_check(missing, good)["reason"] == "unresolved CLI identity"


def test_launch_requires_and_enforces_the_pin_era_hash(tmp_path, monkeypatch):
    """PR #90 round 5: an OPTIONAL expectation let the S3-r3 mismatch
    class self-certify clean — the operator's pin-era hash assertion is
    now REQUIRED, and launching against a different binary refuses with
    a CON-8 error before any scenario work."""
    import json as _json
    import os

    fake = _fake_dora(tmp_path, "host", "rev cd597e705")
    env = {**os.environ, "PATH": f"{fake}:/usr/bin:/bin"}
    script = str(REPO_ROOT / "tools" / "h3_campaign.py")

    # missing --expect-dora-sha256: a CON-8 JSON refusal on stdout,
    # exit nonzero — NOT an argparse usage error on stderr (round 6)
    missing = subprocess.run(
        [sys.executable, script, "--arms", "W", "--scenarios", "S1"],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert missing.returncode != 0
    refusal = _json.loads(missing.stdout)
    assert refusal["ok"] is False and "--expect-dora-sha256" in refusal["error"]

    # wrong hash: refuses with the CON-8 error object, exit nonzero
    wrong = subprocess.run(
        [
            sys.executable,
            script,
            "--arms",
            "W",
            "--scenarios",
            "S1",
            "--expect-dora-sha256",
            "f" * 64,
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
    )
    assert wrong.returncode != 0
    out = _json.loads(wrong.stdout)
    assert out["ok"] is False and "pin-era" in out["error"]
    assert out["found"]["sha256"] and out["found"]["sha256"] != "f" * 64


def test_preflight_runtime_mismatch_refuses_before_the_session(tmp_path, monkeypatch):
    """PR #90 round 6: a DETECTED preflight mismatch must abort before
    the multi-hour session spends its budget — never record-and-run."""
    import h3_campaign as h3

    monkeypatch.setenv("PATH", str(_fake_dora(tmp_path, "drifted", "rev cd597e705")))

    def session_must_not_launch(*a, **kw):
        raise AssertionError("run_session launched despite preflight runtime drift")

    monkeypatch.setattr(h3, "run_session", session_must_not_launch)
    launch = {"path": "/pin/dora", "sha256": "a" * 64, "version": "dora-cli 1.0.0-rc.4"}
    with pytest.raises(RuntimeError, match="runtime drift at scenario preflight"):
        h3.run_scenario(
            tmp_path,
            "deadbeef",
            "W",
            {"tier": "S1", "tokens": 1000, "episodes": 8, "wall_h": 1.0},
            tmp_path / "out",
            "claude",
            "m",
            launch_runtime=launch,
        )
    # refused before ANY scenario side effect: no slot dir was created
    assert not (tmp_path / "out").exists()


def test_scenario_session_runs_under_the_isolated_home(tmp_path, monkeypatch):
    """Issue #96: run_scenario must pass the isolated env into
    run_session and stamp session_isolation into the scenario record —
    the absence of the field is what future audits detect."""
    import h3_campaign as h3

    monkeypatch.setenv("PATH", f"{_fake_dora(tmp_path, 'host', 'rev x')}:/usr/bin:/bin")
    seen = {}

    def fake_run_session(agent, cmd, wt, out, ceilings, env=None):
        seen["env"] = env
        return {"stopped": "agent_done", "rc": 0, "tokens": 1, "wall_s": 1.0}

    monkeypatch.setattr(h3, "run_session", fake_run_session)
    monkeypatch.setattr(
        h3, "seed_session_credentials", lambda *a, **k: ({"credential_seed": "t"}, None)
    )
    monkeypatch.setattr(h3, "sweep_worktree", lambda wt: None)
    monkeypatch.setattr(h3, "audit_frozen", lambda wt, oid: [])
    monkeypatch.setattr(h3, "score_holdout", lambda *a, **k: {"ok": True, "pass1": 0.0})
    monkeypatch.setattr(
        h3,
        "campaign_metrics",
        lambda *a, **k: {"first_success_wall_s": None, "wrong_object_total": 0, "rollouts": []},
    )
    monkeypatch.setattr(h3, "registered_skill_ids", lambda wt: set())
    monkeypatch.setattr(h3, "skill_reuse", lambda *a: [])
    launch = h3.host_dora_runtime()
    rec = h3.run_scenario(
        tmp_path,
        "deadbeef",
        "W",
        {"tier": "S1", "tokens": 1000, "episodes": 8, "wall_h": 1.0},
        tmp_path / "out",
        "claude",
        "m",
        launch_runtime=launch,
    )
    session_dir = tmp_path / "out" / "arm_W" / "S1"
    assert seen["env"]["HOME"] == str(session_dir / "agent_home")
    assert seen["env"]["AISLE_ENV_BASELINE"] == "deadbeef"
    assert rec["session_isolation"]["home"] == str(session_dir / "agent_home")
    assert rec["session_isolation"]["env_baseline_oid"] == "deadbeef"
    assert "runtime_drift" not in rec


def test_launch_refuses_when_the_isolated_auth_probe_fails(tmp_path, monkeypatch):
    """Issue #96 fail-closed: HOME isolation can break credential
    stores; a failed probe must refuse the CAMPAIGN with a CON-8 error
    before any scenario budget is spent — never fall back silently to
    the operator home."""
    import sys as _sys

    import h3_campaign as h3

    fake = _fake_dora(tmp_path, "host", "rev x")
    monkeypatch.setenv("PATH", f"{fake}:/usr/bin:/bin")
    sha = h3.host_dora_runtime()["sha256"]
    monkeypatch.setattr(h3, "probe_agent_auth", lambda *a, **k: "auth probe exited 1: no creds")
    monkeypatch.setattr(
        h3, "seed_session_credentials", lambda *a, **k: ({"credential_seed": "t"}, None)
    )
    monkeypatch.setattr(
        _sys,
        "argv",
        [
            "h3_campaign.py",
            "--arms",
            "W",
            "--scenarios",
            "S1",
            "--out",
            str(tmp_path / "out"),
            "--expect-dora-sha256",
            sha,
        ],
    )
    rc = h3.main()
    assert rc == 1


def test_launch_refusal_prints_the_probe_error(tmp_path, monkeypatch, capsys):
    import json as _json
    import sys as _sys

    import h3_campaign as h3

    fake = _fake_dora(tmp_path, "host2", "rev y")
    monkeypatch.setenv("PATH", f"{fake}:/usr/bin:/bin")
    sha = h3.host_dora_runtime()["sha256"]
    monkeypatch.setattr(h3, "probe_agent_auth", lambda *a, **k: "auth probe exited 1: no creds")
    monkeypatch.setattr(
        h3, "seed_session_credentials", lambda *a, **k: ({"credential_seed": "t"}, None)
    )
    monkeypatch.setattr(
        _sys,
        "argv",
        [
            "h3_campaign.py",
            "--arms",
            "W",
            "--scenarios",
            "S1",
            "--out",
            str(tmp_path / "out"),
            "--expect-dora-sha256",
            sha,
        ],
    )
    assert h3.main() == 1
    out = _json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is False and "auth probe" in out["error"]
