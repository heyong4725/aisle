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
)

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
    assert skill_reuse(wt, deliverable, prior) == ["nav-helper"]
    assert skill_reuse(wt, wt / "missing.yaml", prior) == []


def test_scenario_tiers_are_mobile_scored():
    """RS suite: S-tier holdout scoring must pass --embodiment mobile
    (the store graphs are mobile-only per the research contract)."""
    from campaign import TIER_EMBODIMENT

    assert TIER_EMBODIMENT["S1"] == TIER_EMBODIMENT["S2"] == TIER_EMBODIMENT["S3"] == "mobile"
    assert TIER_EMBODIMENT["T1"] == "franka"
