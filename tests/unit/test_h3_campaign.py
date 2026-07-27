"""Unit tests for tools/h3_campaign.py (ADR-h3-campaign-protocol,
accepted; H3). Pure orchestrator logic — no sim, no agent CLIs."""

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
    losing pass@1 for 4/6 scenarios; tags are now arm+tier strings."""
    import inspect

    import h3_campaign

    src = inspect.getsource(h3_campaign.run_scenario)
    assert 'f"{arm}-{tier}"' in src


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
        [sys.executable, str(REPO_ROOT / "tools" / "h3_campaign.py"), "--arms", "w"],
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

    assert "TENS OF MINUTES" in campaign_prompt("S1", 1000, 1.0, "0..9")
