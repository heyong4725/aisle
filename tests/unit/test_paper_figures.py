"""tools/paper_figures.py regenerates every paper figure from committed
records only (RPR-8; issue #390).

A clean clone has `runs/` = `.gitkeep`, so any figure source under `runs/`
is an ENOENT on the independent-reproduction path. Every source a figure
declares must be git-tracked, and the whole script must exit 0 from a
checkout whose `runs/` is empty.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "paper_figures.py"


def _load_paper_figures():
    spec = importlib.util.spec_from_file_location("paper_figures", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def manifest():
    """Run every figure function in-process with the PNG write stubbed out;
    return {figure name: declared sources}."""
    mod = _load_paper_figures()

    def _no_save(fig, name):
        mod.plt.close(fig)
        return name

    mod._save = _no_save
    return {fn.__name__: fn()[1] for fn in mod.FIGURES}


def test_no_figure_source_under_runs(manifest):
    """RPR-8: no figure may read gitignored runs/ (issue #390)."""
    offenders = [
        (fig, s) for fig, srcs in manifest.items() for s in srcs if Path(s).parts[0] == "runs"
    ]
    assert offenders == []


def test_every_figure_source_is_git_tracked(manifest):
    """RPR-8: every declared source is a git-tracked file, or a directory
    containing one."""
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    tracked_paths = set(tracked) | {str(parent) for p in tracked for parent in Path(p).parents}
    missing = [
        (fig, s)
        for fig, srcs in manifest.items()
        for s in srcs
        if str(Path(s)) not in tracked_paths
    ]
    assert missing == []


def test_promoted_streams_are_complete_records(manifest):
    """The five episode streams fig_t2_arc/fig_m1_mix read were promoted from
    runs/ with their run manifest: eight scored episodes each, carrying the
    fields the figures consume."""
    promoted = [
        s
        for fig in ("fig_t2_arc", "fig_m1_mix")
        for s in manifest[fig]
        if s.endswith("episodes.jsonl")
    ]
    assert len(promoted) == 5
    for rel in promoted:
        eps = [json.loads(x) for x in (REPO / rel).read_text().splitlines() if x.strip()]
        assert len(eps) == 8, rel
        assert all({"status", "failure", "seed"} <= e.keys() for e in eps), rel
        run_manifest = json.loads((REPO / rel).with_name("manifest.json").read_text())
        assert run_manifest["run_id"] == Path(rel).parent.name, rel
        assert sorted(e["seed"] for e in eps) == sorted(run_manifest["seeds"]), rel


def test_paper_figures_cli_exits_zero_from_clean_checkout(tmp_path):
    """RPR-8 / CON-8: the entry point exits 0 with a JSON manifest on stdout
    from a tree that has committed analysis/ and an EMPTY runs/ — the
    clean-clone condition issue #390 reproduced."""
    clone = tmp_path / "clone"
    (clone / "tools").mkdir(parents=True)
    (clone / "runs").mkdir()
    (clone / "analysis").symlink_to(REPO / "analysis", target_is_directory=True)
    shutil.copy(SCRIPT, clone / "tools" / "paper_figures.py")
    proc = subprocess.run(
        [sys.executable, str(clone / "tools" / "paper_figures.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"] is True
    assert [f for f in out["figures"] if "error" in f] == []
    assert sorted(p.name for p in (clone / "docs" / "paper" / "figures").iterdir()) == sorted(
        Path(f["figure"]).name for f in out["figures"]
    )
