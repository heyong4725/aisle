"""Unit tests for the generated contributor inventory (CON-5, CON-8).

The documentation inventory is derived from executable repository sources and
checked in CI. These tests pin deterministic rendering and the write/check CLI
contract before the generator implementation.
"""

import json
from pathlib import Path

import pytest
from cli_helpers import REPO_ROOT, run_tool

pytestmark = pytest.mark.unit


def run_inventory(*args: str):
    return run_tool("docs_inventory.py", *args)


def test_write_is_deterministic_and_covers_repository_surfaces(tmp_path: Path):
    """CON-5: identical repository inputs produce byte-identical graph,
    capability, CLI, ADR, and test inventories across invocations."""
    output = tmp_path / "project-inventory.md"

    first = run_inventory("--root", str(REPO_ROOT), "--output", str(output), "--write")
    assert first.returncode == 0, first.stdout + first.stderr
    first_report = json.loads(first.stdout)
    first_bytes = output.read_bytes()

    second = run_inventory("--root", str(REPO_ROOT), "--output", str(output), "--write")
    assert second.returncode == 0, second.stdout + second.stderr
    second_report = json.loads(second.stdout)
    assert output.read_bytes() == first_bytes
    assert second_report["sha256"] == first_report["sha256"]

    text = first_bytes.decode()
    assert "graphs/expert_t0.yaml" in text
    assert "registry/manifests/budget-guard.yaml" in text
    assert "`harness rollout`" in text
    assert "docs/decisions/ADR-M0.md" in text
    assert "tests/unit/test_docs_inventory.py" in text
    expected_counts = {
        "adrs": len(list((REPO_ROOT / "docs" / "decisions").glob("*.md"))),
        "capabilities": len(list((REPO_ROOT / "registry" / "manifests").glob("*.yaml"))),
        "graphs": len(list((REPO_ROOT / "graphs").glob("*.yaml"))),
        "test_modules": len(list((REPO_ROOT / "tests").rglob("test_*.py"))),
    }
    assert {key: first_report["counts"][key] for key in expected_counts} == expected_counts
    assert first_report["counts"]["cli_commands"] >= 10


def test_check_detects_missing_and_stale_output(tmp_path: Path):
    """CON-8: --check emits one JSON report and exits nonzero when the
    committed inventory is missing or differs from repository reality."""
    output = tmp_path / "project-inventory.md"

    missing = run_inventory("--root", str(REPO_ROOT), "--output", str(output), "--check")
    missing_report = json.loads(missing.stdout)
    assert missing.returncode != 0
    assert missing_report["ok"] is False
    assert missing_report["reason"] == "missing"

    written = run_inventory("--root", str(REPO_ROOT), "--output", str(output), "--write")
    assert written.returncode == 0
    output.write_text(output.read_text() + "stale\n")

    stale = run_inventory("--root", str(REPO_ROOT), "--output", str(output), "--check")
    stale_report = json.loads(stale.stdout)
    assert stale.returncode != 0
    assert stale_report["ok"] is False
    assert stale_report["reason"] == "stale"


def test_check_accepts_fresh_output_and_stdout_is_json(tmp_path: Path):
    """CON-8: a fresh inventory passes with a single JSON object on stdout,
    no stdout prose, and exit zero iff ok is true."""
    output = tmp_path / "project-inventory.md"
    assert (
        run_inventory("--root", str(REPO_ROOT), "--output", str(output), "--write").returncode == 0
    )

    checked = run_inventory("--root", str(REPO_ROOT), "--output", str(output), "--check")
    report = json.loads(checked.stdout)
    assert checked.returncode == 0
    assert report["ok"] is True
    assert report["reason"] == "current"
    assert checked.stderr == ""


@pytest.mark.parametrize("mode", [(), ("--write", "--check")])
def test_exactly_one_mode_is_required(mode: tuple[str, ...]):
    """CON-8: ambiguous mutation/check intent is refused by argparse."""
    proc = run_inventory(*mode)
    assert proc.returncode != 0
