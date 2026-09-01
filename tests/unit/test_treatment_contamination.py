"""Acceptance tests for SPEC 420 independent arm construction."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness.treatment_contamination import (
    ContaminationError,
    build_independent_view,
    run_contamination_capability_audit,
    write_contamination_capability_audit,
)

pytestmark = pytest.mark.unit


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    (repo / "AGENTS.md").write_text("frozen contract\n")
    (repo / "src").mkdir()
    (repo / "src" / "worker.py").write_text("print('baseline')\n")
    _git(repo, "add", "--all")
    _git(
        repo,
        "-c",
        "user.name=AISLE test",
        "-c",
        "user.email=aisle-test@invalid",
        "commit",
        "--quiet",
        "-m",
        "frozen baseline",
    )
    baseline = _git(repo, "rev-parse", "HEAD")
    (repo / "analysis").mkdir()
    (repo / "analysis" / "same-experiment.json").write_text(
        "SYNTHETIC-CONTAMINATION-SENTINEL-5FD2\n"
    )
    _git(repo, "add", "--all")
    _git(
        repo,
        "-c",
        "user.name=AISLE test",
        "-c",
        "user.email=aisle-test@invalid",
        "commit",
        "--quiet",
        "-m",
        "later same-experiment analysis",
    )
    return repo, baseline, _git(repo, "rev-parse", "HEAD")


def test_independent_view_uses_exact_baseline_and_contains_no_git_namespace(tmp_path: Path):
    """TRT-12: later arms are fresh allowlisted exports of the frozen baseline."""
    repo, baseline, later = _repository(tmp_path)
    destination = tmp_path / "later-arm-view"

    manifest = build_independent_view(repo, baseline, ["AGENTS.md", "src/worker.py"], destination)

    assert baseline != later
    assert (destination / "AGENTS.md").read_text() == "frozen contract\n"
    assert (destination / "src" / "worker.py").read_text() == "print('baseline')\n"
    assert not (destination / ".git").exists()
    assert not (destination / "analysis").exists()
    assert manifest["baseline_commit"] == baseline
    assert manifest["baseline_tree"] == _git(repo, "rev-parse", f"{baseline}^{{tree}}")
    assert [row["path"] for row in manifest["visible_files"]] == [
        "AGENTS.md",
        "src/worker.py",
    ]
    assert manifest["immutable_id"].startswith("sha256:")
    assert "SYNTHETIC-CONTAMINATION" not in json.dumps(manifest)


@pytest.mark.parametrize(
    "allowlist",
    [
        ["../outside"],
        ["/absolute"],
        ["AGENTS.md", "AGENTS.md"],
        ["src"],
        ["missing.txt"],
    ],
)
def test_unsafe_ambiguous_or_nonfile_allowlist_refuses(tmp_path: Path, allowlist: list[str]):
    """TRT-12: an unsafe or unresolved later-arm view fails before creation."""
    repo, baseline, _ = _repository(tmp_path)
    destination = tmp_path / "later-arm-view"

    with pytest.raises(ContaminationError):
        build_independent_view(repo, baseline, allowlist, destination)

    assert not destination.exists()


def test_short_or_unknown_baseline_and_existing_destination_refuse(tmp_path: Path):
    """TRT-12: baseline identity and fresh destination are fail-closed inputs."""
    repo, baseline, _ = _repository(tmp_path)
    destination = tmp_path / "later-arm-view"

    with pytest.raises(ContaminationError, match="full commit"):
        build_independent_view(repo, baseline[:8], ["AGENTS.md"], destination)
    with pytest.raises(ContaminationError, match="unknown baseline"):
        build_independent_view(repo, "f" * 40, ["AGENTS.md"], destination)
    with pytest.raises(ContaminationError, match="unknown baseline"):
        build_independent_view(repo, "f" * 64, ["AGENTS.md"], destination)
    destination.mkdir()
    with pytest.raises(ContaminationError, match="already exists"):
        build_independent_view(repo, baseline, ["AGENTS.md"], destination)


def test_synthetic_contamination_audit_proves_sources_exist_but_do_not_cross(tmp_path: Path):
    """TRT-12: a seeded sentinel demonstrates six predecessor channels are excluded."""
    report = run_contamination_capability_audit()

    assert report["evidence_class"] == "synthetic_unscored_contamination"
    assert report["confirmatory_ready"] is False
    assert report["capability_pass"] is True
    assert report["summary"] == {
        "contamination_sources": 6,
        "source_baselines_exposed": 6,
        "view_exposures": 0,
    }
    assert {row["source_class"] for row in report["sources"]} == {
        "predecessor_cache",
        "predecessor_home",
        "predecessor_worktree",
        "prior_analysis",
        "prior_deliverable_ref",
        "prior_transcript",
    }
    assert {row["source_class"]: row["mechanism"] for row in report["sources"]} == {
        "predecessor_cache": "filesystem_cache",
        "predecessor_home": "filesystem_home",
        "predecessor_worktree": "git_worktree",
        "prior_analysis": "later_commit_path",
        "prior_deliverable_ref": "git_ref",
        "prior_transcript": "filesystem_transcript",
    }
    assert all(row["baseline_exposed"] for row in report["sources"])
    assert "SYNTHETIC-CONTAMINATION-SENTINEL" not in json.dumps(report)

    output = tmp_path / "contamination-audit.json"
    written = write_contamination_capability_audit(output)
    assert json.loads(output.read_text()) == written
    with pytest.raises(ContaminationError, match="already exists"):
        write_contamination_capability_audit(output)


def test_contamination_cli_retains_only_synthetic_nonconfirmatory_evidence(tmp_path: Path):
    """TRT-12: the reproducible audit CLI cannot be mistaken for campaign evidence."""
    output = tmp_path / "contamination-audit.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.treatment_contamination",
            "audit-synthetic",
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["evidence_class"] == "synthetic_unscored_contamination"
    assert summary["confirmatory_ready"] is False
    assert json.loads(output.read_text())["capability_pass"] is True
