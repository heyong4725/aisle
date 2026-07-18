"""Unit tests for the capability registry (SPEC 050 CAP-1..6, CON-8).

Acceptance tests named by the spec: test_all_lint (CAP-1..3),
test_search_cli_json (CAP-4), test_registry_completeness (CAP-5).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT, run_module

pytestmark = pytest.mark.unit

MANIFESTS_DIR = REPO_ROOT / "registry" / "manifests"

EXPECTED_IDS = {
    "camera-source",
    "oracle-pose",
    "detector-openvocab",
    "ocr-label",
    "pose-estimator",
    "grasp-planner-topdown",
    "ik-trajectory",
    "arm-driver-sim",
    "gripper-driver-sim",
    "task-state-machine",
    "verifier-oracle",
    "reset",
}


def run_registry_raw(*args: str) -> subprocess.CompletedProcess:
    return run_module("aisle.harness.registry", *args)


def run_registry(*args: str) -> tuple[int, dict]:
    """Run the registry CLI; return (exit code, parsed JSON stdout)."""
    proc = run_registry_raw(*args)
    return proc.returncode, json.loads(proc.stdout)


@pytest.fixture(scope="module")
def repo_lint() -> subprocess.CompletedProcess:
    """One lint run over the committed registry, shared by the tests that
    assert different properties of the same invocation."""
    return run_registry_raw("lint")


def make_root(tmp_path: Path) -> Path:
    """Repo-shaped root with the real schema files and an empty manifests dir."""
    schema_dir = tmp_path / "registry" / "schema"
    schema_dir.mkdir(parents=True)
    shutil.copy(REPO_ROOT / "registry" / "schema" / "capability.schema.json", schema_dir)
    shutil.copy(REPO_ROOT / "registry" / "schema" / "schemas.toml", schema_dir)
    (tmp_path / "registry" / "manifests").mkdir()
    return tmp_path


def valid_manifest(**overrides) -> dict:
    manifest = {
        "id": "fixture-node",
        "kind": "node",
        "provides": ["fixture_ability"],
        "requires": [],
        "inputs": {"rgb": {"schema": "rgb8_image", "rate_hz": 30}},
        "outputs": {"result": {"schema": "json_utf8", "latency_class": "best_effort"}},
        "embodiment": {"arm": ["franka", "so101"], "gripper": "parallel"},
        "safety_class": "perception",
        "eval": None,
        "origin": "hub",
        "source": "src/aisle/nodes/fixture.py",
    }
    manifest.update(overrides)
    return manifest


def write_manifest(root: Path, manifest: dict) -> None:
    path = root / "registry" / "manifests" / f"{manifest['id']}.yaml"
    path.write_text(yaml.safe_dump(manifest, sort_keys=False))


def test_all_lint(repo_lint):
    """CAP-1, CAP-2, CAP-3: every committed manifest validates against
    capability.schema.json and the closed schema vocabulary; lint is a CLI
    with a single JSON object on stdout and exit 0 iff ok (CON-8)."""
    report = json.loads(repo_lint.stdout)
    assert repo_lint.returncode == 0, report
    assert report["ok"] is True
    assert report["checked"] == len(EXPECTED_IDS)
    assert report["errors"] == []


def test_search_cli_json():
    """CAP-4: search --provides grasp_planning returns matching manifests as
    JSON (CON-8); --embodiment filters by supported arm."""
    code, report = run_registry("search", "--provides", "grasp_planning")
    assert code == 0
    assert report["ok"] is True
    assert [m["id"] for m in report["matches"]] == ["grasp-planner-topdown"]

    code, report = run_registry("search", "--provides", "grasp_planning", "--embodiment", "franka")
    assert code == 0
    assert [m["id"] for m in report["matches"]] == ["grasp-planner-topdown"]

    code, report = run_registry("search", "--provides", "object_pose")
    ids = {m["id"] for m in report["matches"]}
    assert ids == {"oracle-pose", "pose-estimator"}  # the perception ladder


def test_search_no_match_is_ok_empty():
    """CAP-4, CON-8: an unmatched --provides returns ok with an empty matches
    list, not an error."""
    code, report = run_registry("search", "--provides", "does_not_exist")
    assert code == 0
    assert report["ok"] is True
    assert report["matches"] == []


def test_registry_completeness():
    """CAP-5: the initial registry is exactly the 12 specified ids, and the
    deliberate gap holds: no capability provides any rearrangement skill."""
    files = sorted(MANIFESTS_DIR.glob("*.yaml"))
    manifests = [yaml.safe_load(f.read_text()) for f in files]
    assert {m["id"] for m in manifests} == EXPECTED_IDS
    assert len(manifests) == len(EXPECTED_IDS)
    all_provides = {p for m in manifests for p in m["provides"]}
    assert not any("rearrang" in p for p in all_provides)


def test_lint_rejects_missing_required_field(tmp_path):
    """CAP-1: a manifest missing a required field fails lint with an error
    naming the manifest."""
    root = make_root(tmp_path)
    bad = valid_manifest()
    del bad["safety_class"]
    write_manifest(root, bad)
    code, report = run_registry("lint", "--root", str(root))
    assert code != 0
    assert report["ok"] is False
    assert any("fixture-node" in e["manifest"] for e in report["errors"])


def test_lint_rejects_unknown_schema_name(tmp_path):
    """CAP-2: a schema name outside registry/schema/schemas.toml is a lint
    error, never silently passed."""
    root = make_root(tmp_path)
    bad = valid_manifest()
    bad["inputs"]["rgb"]["schema"] = "made_up_schema"
    write_manifest(root, bad)
    code, report = run_registry("lint", "--root", str(root))
    assert code != 0
    assert any("made_up_schema" in e["message"] for e in report["errors"])


def test_lint_rejects_bad_enum_value(tmp_path):
    """CAP-1: enum fields (kind, safety_class, origin) reject values outside
    their closed sets."""
    root = make_root(tmp_path)
    write_manifest(root, valid_manifest(safety_class="dangerous"))
    code, report = run_registry("lint", "--root", str(root))
    assert code != 0
    assert report["ok"] is False


def test_lint_enforces_eval_rule(tmp_path):
    """CAP-6: eval may be null only while origin=hub and safety_class is not
    motion; a motion manifest with null eval fails lint."""
    root = make_root(tmp_path)
    write_manifest(root, valid_manifest(safety_class="motion"))
    code, report = run_registry("lint", "--root", str(root))
    assert code != 0
    assert any("eval" in e["message"] for e in report["errors"])


def test_lint_eval_rule_agent_authored(tmp_path):
    """CAP-6: agent-authored capabilities may not have null eval regardless
    of safety class."""
    root = make_root(tmp_path)
    write_manifest(root, valid_manifest(origin="agent-authored"))
    code, report = run_registry("lint", "--root", str(root))
    assert code != 0
    assert any("eval" in e["message"] for e in report["errors"])


def test_lint_accepts_populated_eval_for_motion(tmp_path):
    """CAP-6: a motion capability with a populated evalcard passes lint."""
    root = make_root(tmp_path)
    write_manifest(
        root,
        valid_manifest(
            safety_class="motion",
            eval={"suite": "tc_a1_a3", "pass_rate": 0.98, "last_run": "2026-07-18"},
        ),
    )
    code, report = run_registry("lint", "--root", str(root))
    assert code == 0, report


def test_sim_driver_eval_exception_is_warning(repo_lint):
    """CAP-6: the two sim drivers ship with eval pending M0 evalcards from
    the SPEC 010 acceptance runs; until then lint flags them as warnings,
    not errors. See ADR 3."""
    report = json.loads(repo_lint.stdout)
    assert repo_lint.returncode == 0
    warned = {w["manifest"] for w in report["warnings"]}
    assert {"arm-driver-sim.yaml", "gripper-driver-sim.yaml"} <= warned


def test_bad_root_reported_as_json(tmp_path):
    """CON-8: lint and search with a --root missing schema files or the
    manifests dir emit a JSON error report and exit nonzero — no tracebacks,
    no silent ok-with-zero-results."""
    for command in (["lint"], ["search", "--provides", "grasp_planning"]):
        code, report = run_registry(*command, "--root", str(tmp_path / "nowhere"))
        assert code != 0
        assert report["ok"] is False


def test_lint_empty_manifests_dir_fails(tmp_path):
    """CAP-3: an empty registry is a lint error, never a green gate over
    nothing."""
    root = make_root(tmp_path)
    code, report = run_registry("lint", "--root", str(root))
    assert code != 0
    assert report["ok"] is False


def test_search_scalar_provides_is_not_substring_matched(tmp_path):
    """CAP-4: a manifest whose provides is a YAML scalar (invalid per CAP-1)
    is never substring-matched by search."""
    root = make_root(tmp_path)
    write_manifest(root, valid_manifest(provides="grasp_planning"))
    code, report = run_registry("search", "--root", str(root), "--provides", "grasp")
    assert code == 0
    assert report["matches"] == []


def test_search_survives_malformed_manifest_fields(tmp_path):
    """CON-8: search over manifests missing id or with malformed embodiment
    still returns a JSON report instead of crashing."""
    root = make_root(tmp_path)
    bad = valid_manifest(embodiment="franka")  # not a mapping
    del bad["id"]
    (root / "registry" / "manifests" / "anon.yaml").write_text(yaml.safe_dump(bad, sort_keys=False))
    code, report = run_registry(
        "search", "--root", str(root), "--provides", "fixture_ability", "--embodiment", "franka"
    )
    assert code == 0
    assert report["matches"] == []


def test_lint_warnings_logged_to_stderr(repo_lint):
    """CON-8: warnings (the ADR 3 pending-evalcard carve-out) are visible on
    stderr, not only inside the JSON report."""
    assert repo_lint.returncode == 0
    assert "arm-driver-sim" in repo_lint.stderr
    assert "pending M0 evalcard" in repo_lint.stderr


def test_lint_rejects_duplicate_ids(tmp_path):
    """CAP-5: two manifest files with the same id fail lint (search results
    must be unambiguous)."""
    root = make_root(tmp_path)
    write_manifest(root, valid_manifest())
    dup = valid_manifest()
    (root / "registry" / "manifests" / "other-file.yaml").write_text(
        yaml.safe_dump(dup, sort_keys=False)
    )
    code, report = run_registry("lint", "--root", str(root))
    assert code != 0
    assert report["ok"] is False
