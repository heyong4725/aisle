"""Unit tests for the generated contributor inventory (CON-5, CON-8).

The documentation inventory is derived from executable repository sources and
checked in CI. These tests pin deterministic rendering and the write/check CLI
contract before the generator implementation.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cli_helpers import REPO_ROOT, run_tool

pytestmark = pytest.mark.unit

TOOL = REPO_ROOT / "tools" / "docs_inventory.py"


def run_inventory(*args: str):
    return run_tool("docs_inventory.py", *args)


def inventory_module():
    """Import tools/docs_inventory.py for direct-call unit assertions."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import docs_inventory

    return docs_inventory


def tracked(pathspec: str) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "--", pathspec],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in proc.stdout.splitlines() if line]


def write_markers(root: Path) -> None:
    """Minimal pyproject the generator's marker lookup accepts."""
    root.joinpath("pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nmarkers = ["unit: no sim"]\n', encoding="utf-8"
    )


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
    # suite DIRECTORIES, not modules — see test_module_churn_does_not_stale_the_inventory
    assert "`tests/unit`" in text
    assert "tests/unit/test_docs_inventory.py" not in text
    # tracked, not globbed: the generator documents COMMITTED surfaces, so a
    # dirty working tree must not move these numbers
    expected_counts = {
        "adrs": len(tracked("docs/decisions/ADR-*.md")),
        "capabilities": len(tracked("registry/manifests/*.yaml")),
        "graphs": len(tracked("graphs/*.yaml")),
    }
    assert {key: first_report["counts"][key] for key in expected_counts} == expected_counts
    assert first_report["counts"]["cli_commands"] >= 10
    assert first_report["counts"]["test_modules"] >= 60


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


def test_committed_inventory_matches_this_tree():
    """CON-5 self-consistency: docs/generated/project-inventory.md must
    describe THIS tree.

    Every other test here writes to tmp_path, so `pytest -m unit` went green
    on a stale committed inventory and the drift only surfaced in the ci/unit
    job's --check step — after push. tools/env_hash.py learned the same lesson
    (test_committed_hash_matches_this_tree); this is its docs counterpart."""
    proc = run_inventory("--root", str(REPO_ROOT), "--check")
    report = json.loads(proc.stdout)
    assert proc.returncode == 0, (
        f"committed inventory is {report.get('reason')}; "
        "run `uv run python tools/docs_inventory.py --write`"
    )
    assert report["ok"] is True


def test_untracked_scratch_files_do_not_stale_the_gate(tmp_path: Path):
    """The gate documents COMMITTED surfaces: an untracked scratch test or an
    agent-authored graph is experiment residue, not documentation drift.

    `harness swap` rewrites graphs/ and `harness skill register` writes
    registry/manifests/ mid-session; globbing the working tree let that
    residue block every unrelated commit, and `--write` would commit it."""
    scratch_test = REPO_ROOT / "tests" / "unit" / "test_zz_untracked_probe.py"
    scratch_graph = REPO_ROOT / "graphs" / "zz_untracked_probe.yaml"
    scratch_test.write_text("# untracked scratch\n", encoding="utf-8")
    scratch_graph.write_text("nodes:\n  - id: zz\n    outputs: [x]\n", encoding="utf-8")
    try:
        proc = run_inventory("--root", str(REPO_ROOT), "--check")
        report = json.loads(proc.stdout)
        assert proc.returncode == 0, report
        assert report["reason"] == "current"
    finally:
        scratch_test.unlink()
        scratch_graph.unlink()


def test_adding_a_test_module_does_not_stale_the_inventory(tmp_path: Path):
    """Two independently-green PRs that each add a test file used to merge
    without conflict into a main whose inventory was stale — CI red on main.

    The rendered Tests section names suite DIRECTORIES, so module churn inside
    an existing suite cannot move the committed bytes."""
    output = tmp_path / "project-inventory.md"
    assert (
        run_inventory("--root", str(REPO_ROOT), "--output", str(output), "--write").returncode == 0
    )
    before = output.read_bytes()

    module = inventory_module()
    content, _ = module.render_inventory(REPO_ROOT)
    assert content.encode("utf-8") == before
    assert "### Test modules" not in content
    assert "| Test modules |" not in content


def test_check_survives_a_non_utf8_locale():
    """Reads and writes pin encoding='utf-8' while the digest is over UTF-8.

    Under LC_ALL=C the generator used to die with an opaque
    "'ascii' codec can't decode byte 0xe2" — the repo's graphs, ADRs and the
    inventory itself all carry em dashes — blocking tools/ci.sh with a message
    that says nothing about documentation drift."""
    env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--root", str(REPO_ROOT), "--check"],
        capture_output=True,
        text=True,
        env=env,
    )
    report = json.loads(proc.stdout)
    assert "error" not in report, report
    assert proc.returncode == 0, report


def test_a_non_adr_file_beside_the_adrs_is_ignored(tmp_path: Path):
    """A README, index or template in docs/decisions/ is not a malformed ADR.

    Globbing *.md made any such file raise "ADR has no level-one heading" and
    fail the whole gate for every contributor, on an error path that also
    dropped the reason/sha256/counts keys the JSON contract promises."""
    decisions = tmp_path / "docs" / "decisions"
    decisions.mkdir(parents=True)
    (decisions / "ADR-1.md").write_text(
        "# ADR-1 — a decision\n\nStatus: ACCEPTED\n", encoding="utf-8"
    )
    (decisions / "README.md").write_text("## Index of decisions\n", encoding="utf-8")

    rows = inventory_module()._adr_inventory(tmp_path, None)
    assert [row["title"] for row in rows] == ["ADR-1 — a decision"]


def test_adr_status_is_the_first_status_line_only(tmp_path: Path):
    """The rendered legend promises "the literal first `Status:` line"; the
    continuation loop swept following prose and bullets into the cell."""
    decisions = tmp_path / "docs" / "decisions"
    decisions.mkdir(parents=True)
    (decisions / "ADR-2.md").write_text(
        "# ADR-2 — scoped\n\nStatus: PROPOSED\nTask: T10. Specs: 090.\n- a bullet\n",
        encoding="utf-8",
    )

    rows = inventory_module()._adr_inventory(tmp_path, None)
    assert rows[0]["status"] == "PROPOSED"


def test_suite_directories_are_directories(tmp_path: Path):
    """Grouping by parts[0] rendered a module sitting directly in tests/ as if
    the file itself were a suite directory."""
    tests = tmp_path / "tests"
    (tests / "unit").mkdir(parents=True)
    (tests / "test_root_level.py").write_text("", encoding="utf-8")
    (tests / "unit" / "test_nested.py").write_text("", encoding="utf-8")
    write_markers(tmp_path)

    suites, _ = inventory_module()._test_inventory(tmp_path, None)
    assert suites == ["tests", "tests/unit"]


def test_bridge_is_found_by_module_path_not_node_id(tmp_path: Path):
    """A renamed bridge node used to fall through to {} and publish
    "pharmacy/franka/L0 (default)" for a store/mobile/L2 graph. The rung is
    safety-relevant (TC-9), so a wrong one is worse than none."""
    graphs = tmp_path / "graphs"
    graphs.mkdir(parents=True)
    (graphs / "renamed.yaml").write_text(
        "nodes:\n"
        "  - id: sim-bridge\n"
        "    path: ../src/aisle/nodes/dora_genesis.py\n"
        "    env:\n"
        "      AISLE_SCENE: store\n"
        "      AISLE_EMBODIMENT: mobile\n"
        "      AISLE_PERCEPTION: L2\n"
        "    outputs: [poses]\n",
        encoding="utf-8",
    )
    (graphs / "bridgeless.yaml").write_text(
        "nodes:\n  - id: analysis-only\n    outputs: [report]\n", encoding="utf-8"
    )

    rows = {row["path"].name: row for row in inventory_module()._graph_inventory(tmp_path, None)}
    assert rows["renamed.yaml"]["scene"] == "store"
    assert rows["renamed.yaml"]["embodiment"] == "mobile"
    assert rows["renamed.yaml"]["perception"] == "L2"
    # no bridge => declare nothing rather than assert a default that is false
    assert rows["bridgeless.yaml"]["perception"] == "—"
    assert rows["bridgeless.yaml"]["scene"] == "—"


def test_cli_section_refuses_a_root_it_cannot_introspect(tmp_path: Path):
    """--root is honoured for graphs/manifests/ADRs/tests but the CLI table is
    built from the IMPORTED package, which --root cannot redirect.

    Silently describing the installed tree's CLI under another tree's
    inventory is wrong in both directions: it blocks a correct commit, or it
    certifies as current the very drift the gate exists to catch."""
    proc = run_inventory("--root", str(tmp_path), "--output", str(tmp_path / "out.md"), "--check")
    report = json.loads(proc.stdout)
    assert proc.returncode != 0
    assert report["ok"] is False
    assert "outside --root" in report["error"]
