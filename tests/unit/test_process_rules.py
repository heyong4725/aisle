"""Process-rule proxy tests (M0-4): CON-10/11/14/15 are conduct rules
enforced by repo governance; these tests pin the governance MECHANISMS in
place so the strict trace gate can retire the ADR-1 waivers. They verify
the enforcement machinery exists and stays configured — not the human
behavior itself (which PR review enforces).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
CODEOWNERS = (REPO_ROOT / ".github" / "CODEOWNERS").read_text()

# the Class C surface (CON-10): frozen-set and contract paths whose merge
# requires human review via CODEOWNERS + branch protection
CLASS_C_PATHS = [
    "/specs/",
    "/src/aisle/scenes/",
    "/src/aisle/verifier/",
    "/src/aisle/reset/",
    "/graphs/expert_*",
    "/registry/schema/",
    "/.github/",
]

CONVENTIONAL = re.compile(r"^(feat|fix|test|spec|chore|docs|refactor|perf|ci)(\([^)]+\))?!?: .+")


def test_class_c_paths_have_human_codeowner():
    """CON-10: every Class C path routes through a human CODEOWNERS review
    before merge — removing one from CODEOWNERS silently downgrades the
    risk class."""
    for path in CLASS_C_PATHS:
        entry = next(
            (ln for ln in CODEOWNERS.splitlines() if ln.split() and ln.split()[0] == path),
            None,
        )
        assert entry is not None, f"Class C path {path} missing from CODEOWNERS"
        owners = entry.split()[1:]
        assert any(o.startswith("@") for o in owners), f"{path} has no owner"


def test_commit_history_is_conventional():
    """CON-11: every commit subject on the MAINLINE follows conventional
    commits — the repo convention the rule mandates (squash-merge titles
    inherit the PR title, so this also pins PR titling). Scoped to the
    default branch: intermediate feature-branch commits are squashed away
    and must not fail the unit gate."""
    mainline = None
    for ref in ("origin/main", "main"):
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        if probe.returncode == 0:
            mainline = ref
            break
    if mainline is None:
        pytest.skip("no main ref visible (shallow or detached checkout)")
    subjects = subprocess.run(
        ["git", "log", "--no-merges", "--format=%s", mainline],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=True,
    ).stdout.splitlines()
    assert subjects, "no git history visible"
    bad = [s for s in subjects if not CONVENTIONAL.match(s)]
    assert not bad, f"non-conventional commit subjects: {bad}"


def test_pr_template_demands_requirement_ids():
    """CON-11: the PR template forces every PR description to list the
    requirement IDs it implements or affects."""
    template = (REPO_ROOT / ".github" / "pull_request_template.md").read_text()
    assert "Requirement IDs" in template
    assert "Gates run" in template


def test_spec_edits_require_owner_review():
    """CON-14: specs/ is a CODEOWNERS path owned by a human — no agent can
    merge a spec edit without the owner approving the spec-change PR."""
    entry = next(
        (ln for ln in CODEOWNERS.splitlines() if ln.split() and ln.split()[0] == "/specs/"),
        None,
    )
    assert entry is not None and "@" in entry


def test_adr_log_exists_and_is_wellformed():
    """CON-15: the ADR mechanism is real — docs/decisions/ holds ADR-<n>.md
    files, each a non-empty record with a title, so recorded
    interpretations have a durable, numbered home."""
    adr_dir = REPO_ROOT / "docs" / "decisions"
    adrs = sorted(adr_dir.glob("ADR-*.md"))
    assert adrs, "no ADRs recorded"
    for adr in adrs:
        assert re.fullmatch(r"ADR-\w+\.md", adr.name), adr.name
        text = adr.read_text()
        assert text.strip().startswith("#"), f"{adr.name} missing a title heading"
        assert len(text.strip()) > 100, f"{adr.name} is empty boilerplate"


def test_m0_4_strict_trace_gate_is_green():
    """M0-4 (HAR-9): tools/trace_check.py --strict --specs 000-080 passes —
    every MUST in specs 000-080 is cited by at least one test, with the
    waiver file ignored."""
    proc = subprocess.run(
        [sys.executable, "tools/trace_check.py", "--strict", "--specs", "000-080"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    report = json.loads(proc.stdout)
    uncovered = [m for m in report["must_ids"] if m not in report["covered"]]
    assert proc.returncode == 0 and report["ok"] is True, f"uncovered MUSTs: {uncovered}"
