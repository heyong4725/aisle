"""Unit tests for the A3 params-only ablation orchestrator
(ADR-a3-protocol, ratified via PR #187) — no dora, no sim, no agent CLI
(CON-12)."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

pytestmark = pytest.mark.unit


def _mini_repo(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=wt, check=True)
    (wt / "harness").mkdir()
    (wt / "harness" / "CLAUDE.research.md").write_text("# contract\n")
    (wt / "src").mkdir()
    (wt / "src" / "node.py").write_text("original\n")
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "pin"],
        cwd=wt,
        check=True,
    )
    oid = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True
    ).stdout.strip()
    return wt, oid


def test_plan_matches_the_adr():
    """ADR-a3: F before P (direction-of-bias), desk-T1 budgets."""
    from a3_protocol import ARMS, BUDGET

    assert ARMS == ("F", "P")
    assert BUDGET == {"tokens": 400_000, "episodes": 40, "wall_h": 2.5}


def test_contract_variant_is_committed_and_hashed(tmp_path):
    """ADR-a3: the params-only rule is APPENDED and COMMITTED on the
    arm's worktree before the session — the diff is the treatment, its
    sha256 the record; ambient git identity never reaches the commit."""
    from a3_protocol import PARAMS_ONLY_RULE, commit_contract_variant

    wt, _ = _mini_repo(tmp_path)
    sha = commit_contract_variant(wt)
    text = (wt / "harness" / "CLAUDE.research.md").read_text()
    assert "PARAMS-ONLY VARIANT (A3)" in text
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=wt, capture_output=True, text=True
        ).stdout.strip()
        == ""
    )
    import hashlib

    assert sha == hashlib.sha256(PARAMS_ONLY_RULE.encode()).hexdigest()
    author = subprocess.run(
        ["git", "log", "-1", "--format=%an"], cwd=wt, capture_output=True, text=True
    ).stdout.strip()
    assert author == "aisle-a3-protocol"


def test_params_audit_catches_tracked_and_untracked_changes(tmp_path):
    """ADR-a3 enforcement: the audit surface is every post-pin change
    under src/ or skills/ — tracked edits, agent COMMITS, and untracked
    files alike; changes elsewhere (graphs/) are not leaks."""
    from a3_protocol import audit_params_surface

    wt, oid = _mini_repo(tmp_path)
    assert audit_params_surface(wt, oid) == []
    (wt / "src" / "node.py").write_text("edited\n")
    (wt / "skills").mkdir()
    (wt / "skills" / "new_skill.py").write_text("authored\n")
    (wt / "graphs").mkdir()
    (wt / "graphs" / "agent_campaign.yaml").write_text("nodes: []\n")
    leaks = audit_params_surface(wt, oid)
    assert leaks == ["skills/new_skill.py", "src/node.py"]
    # an agent COMMIT does not launder the edit
    subprocess.run(["git", "add", "-A"], cwd=wt, check=True)
    subprocess.run(
        ["git", "-c", "user.email=a@a", "-c", "user.name=a", "commit", "-qm", "agent"],
        cwd=wt,
        check=True,
    )
    assert "src/node.py" in audit_params_surface(wt, oid)


def test_refusals_are_json_on_stdout():
    """CON-8: missing dora identity and bad arm selections refuse."""
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.strip()
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "a3_protocol.py"), "--commit", head],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode != 0 and "expect-dora-sha256" in proc.stdout
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "a3_protocol.py"),
            "--commit",
            head,
            "--arms",
            "F,X",
            "--expect-dora-sha256",
            "0" * 64,
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode != 0 and "bad --arms" in proc.stdout
