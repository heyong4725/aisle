"""Unit tests for tools/env_hash.py (CON-7, CON-5, CON-8).

CON-7: the frozen set is src/aisle/{scenes,verifier,reset} and
graphs/expert_*.yaml; tools/env_hash.py fingerprints it so rollout can
refuse on mismatch.
"""

import json
import subprocess
from pathlib import Path

import pytest
from cli_helpers import run_tool

pytestmark = pytest.mark.unit


def run_env_hash(*args: str) -> subprocess.CompletedProcess:
    return run_tool("env_hash.py", *args)


def make_root(tmp_path: Path) -> Path:
    """Minimal repo root containing the CON-7 frozen set plus non-frozen files."""
    for pkg in ("scenes", "verifier", "reset"):
        d = tmp_path / "src" / "aisle" / pkg
        d.mkdir(parents=True)
        (d / "__init__.py").write_text("")
    (tmp_path / "src" / "aisle" / "scenes" / "pharmacy.py").write_text("SHELF_LEVELS = 3\n")
    (tmp_path / "src" / "aisle" / "verifier" / "thresholds.toml").write_text("upright_deg = 30\n")
    (tmp_path / "graphs").mkdir()
    (tmp_path / "graphs" / "expert_t0.yaml").write_text("nodes: []\n")
    (tmp_path / "graphs" / "scratch.yaml").write_text("nodes: []\n")
    (tmp_path / "tools").mkdir()
    return tmp_path


def get_hash(root: Path) -> str:
    proc = run_env_hash("--root", str(root))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    return report["env_hash"]


def test_cli_json_stdout(tmp_path):
    """CON-8: env_hash emits a single JSON object on stdout, logs to stderr
    only, exit 0 iff ok."""
    root = make_root(tmp_path)
    proc = run_env_hash("--root", str(root))
    assert proc.returncode == 0
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert len(report["env_hash"]) == 64
    assert int(report["env_hash"], 16) >= 0  # hex sha256


def test_deterministic(tmp_path):
    """CON-5: same input tree ⇒ same hash, across invocations and across
    freshly created identical trees (no time/inode dependence)."""
    root_a = make_root(tmp_path / "a")
    root_b = make_root(tmp_path / "b")
    assert get_hash(root_a) == get_hash(root_a)
    assert get_hash(root_a) == get_hash(root_b)


def test_content_change_changes_hash(tmp_path):
    """CON-7: a single mutated byte inside the frozen set changes env_hash."""
    root = make_root(tmp_path)
    before = get_hash(root)
    (root / "src" / "aisle" / "verifier" / "thresholds.toml").write_text("upright_deg = 31\n")
    assert get_hash(root) != before


def test_rename_changes_hash(tmp_path):
    """CON-7: file paths are part of the fingerprint, not just contents."""
    root = make_root(tmp_path)
    before = get_hash(root)
    src = root / "src" / "aisle" / "scenes" / "pharmacy.py"
    src.rename(root / "src" / "aisle" / "scenes" / "pharmacy2.py")
    assert get_hash(root) != before


def test_non_frozen_files_ignored(tmp_path):
    """CON-7: only src/aisle/{scenes,verifier,reset} and graphs/expert_*.yaml
    are fingerprinted; other files do not affect the hash."""
    root = make_root(tmp_path)
    before = get_hash(root)
    (root / "graphs" / "scratch.yaml").write_text("nodes: [changed]\n")
    (root / "src" / "aisle" / "scenes" / "__pycache__").mkdir()
    (root / "src" / "aisle" / "scenes" / "__pycache__" / "x.pyc").write_text("junk")
    assert get_hash(root) == before
    (root / "graphs" / "expert_t0.yaml").write_text("nodes: [changed]\n")
    assert get_hash(root) != before


def test_write_then_check_ok(tmp_path):
    """CON-7: --write commits tools/env_hash.json; --check passes while the
    frozen set is unchanged."""
    root = make_root(tmp_path)
    proc = run_env_hash("--root", str(root), "--write")
    assert proc.returncode == 0
    assert (root / "tools" / "env_hash.json").exists()
    check = run_env_hash("--root", str(root), "--check")
    report = json.loads(check.stdout)
    assert check.returncode == 0
    assert report["ok"] is True


def test_check_mismatch_fails(tmp_path):
    """CON-7: after a frozen-set edit, --check exits nonzero with ok=false
    so the rollout runner can refuse to launch."""
    root = make_root(tmp_path)
    run_env_hash("--root", str(root), "--write")
    (root / "src" / "aisle" / "verifier" / "thresholds.toml").write_text("upright_deg = 45\n")
    check = run_env_hash("--root", str(root), "--check")
    report = json.loads(check.stdout)
    assert check.returncode != 0
    assert report["ok"] is False


def test_check_corrupted_hash_file_reported_as_json(tmp_path):
    """CON-8: a corrupted tools/env_hash.json (invalid JSON or missing key)
    yields a JSON error report on stdout, not a Python traceback."""
    root = make_root(tmp_path)
    for bad in ("not json{", '{"wrong_key": 1}'):
        (root / "tools" / "env_hash.json").write_text(bad)
        check = run_env_hash("--root", str(root), "--check")
        report = json.loads(check.stdout)
        assert check.returncode != 0
        assert report["ok"] is False


def test_file_boundaries_are_unambiguous(tmp_path):
    """CON-7: content containing NUL bytes cannot make two different frozen
    trees hash equal (per-file digests frame each file's content)."""
    root_a = make_root(tmp_path / "a")
    root_b = make_root(tmp_path / "b")
    scenes_a = root_a / "src" / "aisle" / "scenes"
    scenes_b = root_b / "src" / "aisle" / "scenes"
    # Under naive path\0content\0 concatenation these two trees would feed
    # the hasher identical byte streams.
    (scenes_a / "a").write_bytes(b"b\0src/aisle/scenes/c\0d")
    (scenes_b / "a").write_bytes(b"b")
    (scenes_b / "c").write_bytes(b"d")
    assert get_hash(root_a) != get_hash(root_b)


def test_check_without_committed_hash_fails(tmp_path):
    """CON-8: --check with no committed tools/env_hash.json is an explicit
    error, not a silent pass."""
    root = make_root(tmp_path)
    check = run_env_hash("--root", str(root), "--check")
    report = json.loads(check.stdout)
    assert check.returncode != 0
    assert report["ok"] is False


def test_guard_and_limits_are_hashed(tmp_path):
    """SPEC 080 / CON-7 (PR review): the frozen safety artifacts —
    env/limits.toml and the budget-guard module — are part of the env
    hash; adding or changing either changes it."""
    root = make_root(tmp_path)
    base = get_hash(root)
    guard = root / "src" / "aisle" / "nodes" / "budget_guard.py"
    guard.parent.mkdir(parents=True)
    guard.write_text("GUARD = 1\n")
    with_guard = get_hash(root)
    assert with_guard != base
    limits = root / "env" / "limits.toml"
    limits.parent.mkdir()
    limits.write_text("[embodiment.franka]\n")
    with_limits = get_hash(root)
    assert with_limits != with_guard
    limits.write_text("[embodiment.franka]\nq = 1\n")
    assert get_hash(root) != with_limits
