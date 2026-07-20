"""Unit tests for the capability registry (SPEC 050 CAP-1..6, CON-8).

Acceptance tests named by the spec: test_all_lint (CAP-1..3),
test_search_cli_json (CAP-4), test_registry_completeness (CAP-5).
"""

import json
import subprocess

import pytest
import yaml
from cli_helpers import REPO_ROOT, make_registry_root, run_json, run_module, write_manifest

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
    "budget-guard",  # SPEC 080 BG-1 (T07)
    "dora-genesis",  # executable bridge identity (T08)
    "rollout-client",  # episode driver for runnable graphs (T08)
}


def run_registry_raw(*args: str) -> subprocess.CompletedProcess:
    return run_module("aisle.harness.registry", *args)


def run_registry(*args: str) -> tuple[int, dict]:
    """Run the registry CLI; return (exit code, parsed JSON stdout)."""
    return run_json("aisle.harness.registry", *args)


@pytest.fixture(scope="module")
def repo_lint() -> subprocess.CompletedProcess:
    """One lint run over the committed registry, shared by the tests that
    assert different properties of the same invocation."""
    return run_registry_raw("lint")


make_root = make_registry_root


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


CAP1_REQUIRED = [
    "id",
    "kind",
    "provides",
    "requires",
    "inputs",
    "outputs",
    "embodiment",
    "safety_class",
    "eval",
    "origin",
    "source",
]


def test_schema_required_set_matches_cap1():
    """CAP-1: the JSON Schema requires exactly the CAP-1 field set, with
    params as the sole optional top-level field."""
    schema = json.loads((REPO_ROOT / "registry" / "schema" / "capability.schema.json").read_text())
    assert set(schema["required"]) == set(CAP1_REQUIRED)
    assert set(schema["properties"]) - set(schema["required"]) == {"params"}


@pytest.mark.parametrize("field", CAP1_REQUIRED)
def test_lint_rejects_missing_required_field(tmp_path, field):
    """CAP-1: a manifest missing any required field fails lint with an error
    naming the manifest."""
    root = make_root(tmp_path)
    bad = valid_manifest()
    del bad[field]
    if field == "id":
        (root / "registry" / "manifests" / "anon.yaml").write_text(
            yaml.safe_dump(bad, sort_keys=False)
        )
    else:
        write_manifest(root, bad)
    code, report = run_registry("lint", "--root", str(root))
    assert code != 0
    assert report["ok"] is False


def test_oracle_state_has_a_producer():
    """CAP-5: the bridge's observation facet (camera-source) declares the
    oracle_state output from the SPEC 010 topic table, so the verifier's
    permitted input has a producer when graphs are validated."""
    manifest = yaml.safe_load((MANIFESTS_DIR / "camera-source.yaml").read_text())
    assert manifest["outputs"]["oracle_state"]["schema"] == "posearray7d_f32"


def test_hot_topics_are_not_json():
    """CON-4: no manifest port at >=10 Hz uses the json_utf8 schema — JSON
    is allowed only on low-rate goal/result/report topics."""
    for f in sorted(MANIFESTS_DIR.glob("*.yaml")):
        manifest = yaml.safe_load(f.read_text())
        for port, spec in manifest.get("inputs", {}).items():
            if spec["schema"] == "json_utf8":
                assert spec["rate_hz"] < 10, f"{f.stem}/{port} is a hot JSON topic"


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


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "daemon"},
        {"safety_class": "dangerous"},
        {"origin": "vendored"},
        {"embodiment": {"arm": ["ur5"], "gripper": "parallel"}},
        {"embodiment": {"arm": ["franka"], "gripper": "paralell"}},
        {"outputs": {"result": {"schema": "json_utf8", "latency_class": "asap"}}},
    ],
)
def test_lint_rejects_bad_enum_value(tmp_path, overrides):
    """CAP-1: every closed enum (kind, safety_class, origin, arm, gripper,
    latency_class) rejects values outside its set."""
    root = make_root(tmp_path)
    write_manifest(root, valid_manifest(**overrides))
    code, report = run_registry("lint", "--root", str(root))
    assert code != 0
    assert report["ok"] is False


def test_lint_rejects_malformed_vocabulary(tmp_path):
    """CAP-2: a schemas.toml entry that is not exactly an {arrow, shape}
    string mapping is a lint error, so the vocabulary itself stays closed."""
    root = make_root(tmp_path)
    write_manifest(root, valid_manifest())
    (root / "registry" / "schema" / "schemas.toml").write_text(
        '[rgb8_image]\narrow = "UInt8"\nshape = "h*w*3"\n\n'
        '[json_utf8]\narrow = "Utf8"\nshape = "1"\n\n[broken]\nshape = "1"\n'
    )
    code, report = run_registry("lint", "--root", str(root))
    assert code != 0
    assert any("broken" in e["message"] for e in report["errors"])


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


def test_sim_driver_evalcards_shipped_and_carveout_retired(repo_lint):
    """CAP-6 end state (ADR-3 retired at T08): the sim-driver manifests
    carry evalcards generated from the passing acceptance suite runs
    (tests/accept/test_contract.py),
    lint reports ZERO warnings, and the pending carve-out is an empty
    tombstone — the T10 gate's condition, met early."""
    from aisle.harness.registry import PENDING_M0_EVALCARDS

    report = json.loads(repo_lint.stdout)
    assert repo_lint.returncode == 0
    assert report["warnings"] == []
    assert PENDING_M0_EVALCARDS == set()
    for mid in ("arm-driver-sim", "gripper-driver-sim", "dora-genesis"):
        manifest = yaml.safe_load((MANIFESTS_DIR / f"{mid}.yaml").read_text())
        assert manifest["eval"] is not None, mid
        assert manifest["eval"]["pass_rate"] == 1.0
        assert "TC-A1..A3" in manifest["eval"]["suite"]


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
    """CON-8: search over manifests with malformed or missing fields
    (scalar embodiment, arm: null, no id) still returns a JSON report
    instead of crashing."""
    root = make_root(tmp_path)
    bad = valid_manifest(embodiment="franka")  # not a mapping
    del bad["id"]
    (root / "registry" / "manifests" / "anon.yaml").write_text(yaml.safe_dump(bad, sort_keys=False))
    write_manifest(root, valid_manifest(id="null-arm", embodiment={"arm": None, "gripper": "any"}))
    code, report = run_registry(
        "search", "--root", str(root), "--provides", "fixture_ability", "--embodiment", "franka"
    )
    assert code == 0
    assert report["matches"] == []


def test_search_serializes_yaml_dates(tmp_path):
    """CON-8: an unquoted YAML date (parsed as datetime.date) in a matching
    manifest is serialized in the JSON report, not a TypeError traceback."""
    root = make_root(tmp_path)
    path = root / "registry" / "manifests" / "fixture-node.yaml"
    manifest = valid_manifest(eval={"suite": "s", "pass_rate": 0.9, "last_run": "placeholder"})
    text = yaml.safe_dump(manifest, sort_keys=False).replace("placeholder", "2026-07-18")
    path.write_text(text)
    code, report = run_registry("search", "--root", str(root), "--provides", "fixture_ability")
    assert code == 0
    assert report["matches"][0]["eval"]["last_run"] == "2026-07-18"


def test_lint_findings_logged_to_stderr():
    """CON-8: lint findings are visible on stderr, not only inside the
    JSON report — exercised against the eval_null fixture root, whose
    eval-null motion drivers are ERRORS now that the ADR-3 carve-out is
    retired (no warning class remains in lint)."""
    proc = run_registry_raw(
        "lint", "--root", str(REPO_ROOT / "tests" / "fixtures" / "roots" / "eval_null")
    )
    assert proc.returncode == 1
    assert "arm-driver-sim" in proc.stderr
    assert "CAP-6" in proc.stderr


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
