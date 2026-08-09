"""Unit tests for tools/env_hash.py (CON-7, CON-5, CON-8).

CON-7: the frozen set is src/aisle/{scenes,verifier,reset}, assets/so101, and
graphs/expert_*.yaml; tools/env_hash.py fingerprints it so rollout can
refuse on mismatch.
"""

import json
import subprocess
from pathlib import Path

import pytest
from cli_helpers import REPO_ROOT, run_tool

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
    asset = tmp_path / "assets" / "so101"
    asset.mkdir(parents=True)
    (asset / "so101.urdf").write_text("<robot name='so101'/>\n")
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


def test_so101_asset_change_changes_hash(tmp_path):
    """CON-5, CON-7, SCN-4: the pinned SO-101 URDF and mesh closure are
    frozen inputs; changing an asset invalidates the environment hash."""
    root = make_root(tmp_path)
    before = get_hash(root)
    (root / "assets" / "so101" / "so101.urdf").write_text("<robot name='changed'/>\n")
    assert get_hash(root) != before


def test_rename_changes_hash(tmp_path):
    """CON-7: file paths are part of the fingerprint, not just contents."""
    root = make_root(tmp_path)
    before = get_hash(root)
    src = root / "src" / "aisle" / "scenes" / "pharmacy.py"
    src.rename(root / "src" / "aisle" / "scenes" / "pharmacy2.py")
    assert get_hash(root) != before


def test_non_frozen_files_ignored(tmp_path):
    """CON-7: files outside the declared frozen paths do not affect the hash."""
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


class TestTrustedBaseline:
    """ADR-21 (PR #24): --check --baseline <ref> defeats the regenerate-
    the-json attack — the baseline hash comes from the git object store at
    a protected ref the research agent cannot move, and the checker itself
    must match its blob there."""

    def _trusted_root(self, tmp_path: Path) -> Path:
        import shutil

        repo = Path(__file__).resolve().parents[2]
        root = make_root(tmp_path)
        shutil.copy(repo / "tools" / "env_hash.py", root / "tools" / "env_hash.py")
        (root / "harness").mkdir()
        (root / "harness" / "budget.toml").write_text(
            "[campaign]\ntokens = 5000000\nepisodes = 500\nwall_h = 40.0\n"
        )
        env = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(tmp_path),
        }

        def git(*args):
            proc = subprocess.run(
                ["git", *args],
                cwd=root,
                capture_output=True,
                text=True,
                env={**env, "PATH": __import__("os").environ["PATH"]},
            )
            assert proc.returncode == 0, (args, proc.stderr)

        git("init", "-q")
        assert run_env_hash("--write", "--root", str(root)).returncode == 0
        git("add", "-A")
        git("commit", "-qm", "baseline")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        return root

    def test_clean_tree_passes_trusted_check(self, tmp_path):
        root = self._trusted_root(tmp_path)
        proc = run_env_hash("--check", "--baseline", "origin/main", "--root", str(root))
        assert proc.returncode == 0, proc.stdout
        assert json.loads(proc.stdout)["baseline"] == "origin/main"

    def test_regenerated_local_json_does_not_bless_frozen_edits(self, tmp_path):
        """The PR #24 attack verbatim: edit frozen code, rerun --write —
        the LOCAL check passes, the TRUSTED check refuses."""
        root = self._trusted_root(tmp_path)
        (root / "src" / "aisle" / "verifier" / "thresholds.toml").write_text("upright_deg = 90\n")
        assert run_env_hash("--write", "--root", str(root)).returncode == 0
        local = run_env_hash("--check", "--root", str(root))
        assert local.returncode == 0  # the attack defeats the local check...
        trusted = run_env_hash("--check", "--baseline", "origin/main", "--root", str(root))
        assert trusted.returncode == 1  # ...but not the trusted one
        assert "diverges from origin/main" in json.loads(trusted.stdout)["error"]

    def test_tampered_checker_is_refused(self, tmp_path):
        """Rewriting the checker itself cannot bless anything: the trusted
        mode verifies tools/env_hash.py against its blob at the ref."""
        root = self._trusted_root(tmp_path)
        with open(root / "tools" / "env_hash.py", "a") as f:
            f.write("\n# patched\n")
        trusted = run_env_hash("--check", "--baseline", "origin/main", "--root", str(root))
        assert trusted.returncode == 1
        assert "not trusted" in json.loads(trusted.stdout)["error"]

    def test_missing_ref_is_an_explicit_error(self, tmp_path):
        root = self._trusted_root(tmp_path)
        proc = run_env_hash("--check", "--baseline", "origin/nope", "--root", str(root))
        assert proc.returncode == 1
        assert "origin/nope" in json.loads(proc.stdout)["error"]


def test_adr24_fingerprint_names_the_selection():
    """CON-5 as amended (ADR-24): the fingerprint hashes the lock AND the
    resolved selection — default vs --extra sim produce DIFFERENT
    identities from the same lock (the footgun the version map missed),
    and the same inputs reproduce the same fingerprint."""
    import sys as _sys

    _sys.path.insert(0, str(REPO_ROOT / "tools"))
    from env_hash import current_selection, env_fingerprint

    lock = b"fake lock bytes"
    default = env_fingerprint(lock, current_selection([]))
    sim = env_fingerprint(lock, current_selection(["sim"]))
    assert default != sim
    assert sim == env_fingerprint(lock, current_selection(["sim"]))  # deterministic
    assert env_fingerprint(b"other lock", current_selection(["sim"])) != sim


def test_adr24_canonical_name_pep503():
    """PR #68 review (minor): every name join folds case and [-_.] runs."""
    import sys as _sys

    _sys.path.insert(0, str(REPO_ROOT / "tools"))
    from env_hash import canonical_name

    assert canonical_name("Dora_YOLO") == "dora-yolo"
    assert canonical_name("genesis.world") == "genesis-world"
    assert canonical_name("torch") == "torch"


def test_adr24_direct_url_classification():
    """ADR-24 D2: editable/local-dir installs are unattestable; VCS needs
    a commit id (dora-rs's pinned-rev git install is legitimate); archive
    installs need a hash; index installs (no direct_url) are fine."""
    import sys as _sys

    _sys.path.insert(0, str(REPO_ROOT / "tools"))
    from env_hash import classify_direct_url

    assert classify_direct_url({"vcs_info": {"vcs": "git", "commit_id": "abc"}}) is None
    assert "commit id" in classify_direct_url({"vcs_info": {"vcs": "git"}})
    assert "editable" in classify_direct_url({"dir_info": {"editable": True}})
    assert "local-directory" in classify_direct_url({"dir_info": {}})
    assert "hash" in classify_direct_url({"archive_info": {}})
    assert classify_direct_url({"archive_info": {"hashes": {"sha256": "x"}}}) is None


def test_adr24_registry_pip_dists_regex(tmp_path):
    """The trusted blob parses pip: sources without a yaml dependency —
    canonical names, quoted or bare, non-pip ignored."""
    import sys as _sys

    _sys.path.insert(0, str(REPO_ROOT / "tools"))
    from env_hash import registry_pip_dists

    mdir = tmp_path / "registry" / "manifests"
    mdir.mkdir(parents=True)
    (mdir / "a.yaml").write_text("id: a\nsource: pip:Dora_YOLO\n")
    (mdir / "b.yaml").write_text("id: b\nsource: 'pip:dora-pose'\n")
    (mdir / "c.yaml").write_text("id: c\nsource: src/aisle/nodes/c.py\n")
    assert registry_pip_dists(tmp_path) == ["dora-pose", "dora-yolo"]


def _fake_md(monkeypatch, dists):
    """Install a fake importlib.metadata.distributions/distribution pair."""
    import importlib.metadata as md

    monkeypatch.setattr(md, "distributions", lambda: list(dists.values()))

    def one(name):
        for d in dists.values():
            if d.metadata.get("Name").lower().replace("_", "-") == name:
                return d
        raise md.PackageNotFoundError(name)

    monkeypatch.setattr(md, "distribution", one)


class _FakeDist:
    def __init__(self, name, version, files, record_text="rec"):
        self.metadata = {"Name": name}
        self.version = version
        self.files = files
        self._record = record_text

    def read_text(self, fname):
        return self._record if fname == "RECORD" else None


def _hashed_file(tmp_path, name, content):
    import base64
    import hashlib as _hashlib

    path = tmp_path / name
    path.write_text(content)

    class FakeHash:
        mode = "sha256"
        value = (
            base64.urlsafe_b64encode(_hashlib.sha256(path.read_bytes()).digest())
            .rstrip(b"=")
            .decode()
        )

    class FakeFile:
        hash = FakeHash()

        def locate(self):
            return path

        def __str__(self):
            return name

    return FakeFile()


def test_adr24_verify_records_fails_closed(tmp_path, monkeypatch):
    """PR #69 review F1: the post-session audit verifies against the
    GATE-TIME inventory — removal, addition, version change, a changed
    RECORD (self-blessing), zero verifiable entries, and file mutation
    are each problems; the intact environment verifies."""
    import hashlib as _hashlib
    import sys as _sys

    _sys.path.insert(0, str(REPO_ROOT / "tools"))
    import env_hash as eh

    good = _FakeDist("alpha", "1.0", [_hashed_file(tmp_path, "a.py", "ok")])
    expected = {
        "alpha": {
            "version": "1.0",
            "record_sha256": _hashlib.sha256(b"rec").hexdigest(),
        }
    }
    _fake_md(monkeypatch, {"alpha": good})
    assert eh.verify_records(expected)["ok"] is True

    # removed after the gate
    _fake_md(monkeypatch, {})
    report = eh.verify_records(expected)
    assert report["ok"] is False and any("removed" in p for p in report["problems"])

    # installed after the gate
    extra = _FakeDist("beta", "2.0", [_hashed_file(tmp_path, "b.py", "new")])
    _fake_md(monkeypatch, {"alpha": good, "beta": extra})
    report = eh.verify_records(expected)
    assert any("installed after the gate" in p for p in report["problems"])

    # RECORD changed (self-blessing attempt)
    blessed = _FakeDist("alpha", "1.0", [_hashed_file(tmp_path, "c.py", "evil")], "rec2")
    _fake_md(monkeypatch, {"alpha": blessed})
    report = eh.verify_records(expected)
    assert any("RECORD changed" in p for p in report["problems"])

    # zero verifiable entries
    empty = _FakeDist("alpha", "1.0", [])
    _fake_md(monkeypatch, {"alpha": empty})
    report = eh.verify_records(expected)
    assert any("zero hash-verifiable" in p for p in report["problems"])

    # mutated file under an intact RECORD
    mut_file = _hashed_file(tmp_path, "d.py", "before")
    (tmp_path / "d.py").write_text("after")
    mutated = _FakeDist("alpha", "1.0", [mut_file])
    _fake_md(monkeypatch, {"alpha": mutated})
    report = eh.verify_records(expected)
    assert any("does not match its RECORD hash" in p for p in report["problems"])


def test_adr24_pip_source_parser_parity():
    """PR #69 review F2: the trusted scanner and the validator's
    _pip_dist must be the SAME parser — decorated, pinned, case-varied,
    quoted, and indented forms all land on the canonical name."""
    import sys as _sys

    _sys.path.insert(0, str(REPO_ROOT / "tools"))
    from env_hash import parse_pip_source

    from aisle.harness.registry import _pip_dist

    cases = [
        "pip:dora-yolo",
        "pip:Dora_YOLO[gpu]==1.2",
        "PIP:dora.pose",
        "pip: dora-ocr ",
        "pip:torch>=2.0; python_version>'3.10'",
    ]
    for source in cases:
        assert parse_pip_source(source) == _pip_dist({"source": source}), source
    assert parse_pip_source("src/aisle/nodes/x.py") is None
    assert _pip_dist({"source": "src/aisle/nodes/x.py"}) is None


def test_adr24_scanner_handles_indented_and_decorated_manifests(tmp_path):
    """PR #69 review F2 (the exact bypasses): indented `source:` lines and
    decorated pip values must land as canonical base names."""
    import sys as _sys

    _sys.path.insert(0, str(REPO_ROOT / "tools"))
    from env_hash import registry_pip_dists

    mdir = tmp_path / "registry" / "manifests"
    mdir.mkdir(parents=True)
    (mdir / "a.yaml").write_text("id: a\nsource: pip:Dora_YOLO[gpu]==1.2\n")
    (mdir / "b.yaml").write_text("id: b\n  source: pip:dora_pose\n")
    (mdir / "c.yaml").write_text("id: c\nsource: 'pip:dora-ocr'\n")
    (mdir / "d.yaml").write_text("id: d\nsource: src/aisle/nodes/d.py\n")
    assert registry_pip_dists(tmp_path) == ["dora-ocr", "dora-pose", "dora-yolo"]


def test_adr24_selection_covers_abi_groups_and_tags():
    """PR #69 review F4: the selection names the full interpreter identity
    (version + ABI cache tag), the platform tag set, extras AND groups —
    and each axis moves the fingerprint."""
    import sys as _sys

    _sys.path.insert(0, str(REPO_ROOT / "tools"))
    from env_hash import current_selection, env_fingerprint

    sel = current_selection(["sim"])
    assert set(sel) == {"python", "abi", "platform_tags", "extras", "groups"}
    assert sel["groups"] == ["dev"]  # uv's default sync includes dev
    assert sel["abi"].startswith("cpython-")
    lock = b"lock"
    base = env_fingerprint(lock, sel)
    assert env_fingerprint(lock, current_selection(["sim"], groups=[])) != base
    assert env_fingerprint(lock, current_selection([])) != base
