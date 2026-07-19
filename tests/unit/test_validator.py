"""Unit tests for the dataflow validator (SPEC 060 VAL-1..7, CON-8).

Acceptance tests named by the spec:
test_bad_corpus_all_rejected_with_expected_codes (VAL-1..6),
test_good_corpus_passes, test_hints_nonempty (VAL-3).
The validator imports neither genesis nor dora.
"""

import tomllib
from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT, make_registry_root, run_json, write_manifest

pytestmark = pytest.mark.unit

BAD_DIR = REPO_ROOT / "tests" / "fixtures" / "graphs" / "bad"
GOOD_DIR = REPO_ROOT / "tests" / "fixtures" / "graphs" / "good"

with open(BAD_DIR / "expected.toml", "rb") as _f:
    EXPECTED = tomllib.load(_f)


def run_validate(graph: Path | str, *args: str) -> tuple[int, dict]:
    return run_json("aisle.harness.cli", "validate", str(graph), *args)


def corpus_args(expectation: dict) -> list[str]:
    return ["--embodiment", expectation["embodiment"]] if "embodiment" in expectation else []


_CORPUS_CACHE: dict[str, tuple[int, dict]] = {}


def corpus_report(stem: str) -> tuple[int, dict]:
    """One CLI run per bad-corpus file per session, shared across tests."""
    if stem not in _CORPUS_CACHE:
        _CORPUS_CACHE[stem] = run_validate(BAD_DIR / f"{stem}.yaml", *corpus_args(EXPECTED[stem]))
    return _CORPUS_CACHE[stem]


def codes(report: dict, level: str) -> set[str]:
    return {entry["code"] for entry in report[level]}


def test_corpus_minimums():
    """VAL-7: the golden corpus holds >=20 deliberately broken graphs (incl.
    the design-doc node-id typo) and >=3 valid graphs. graphs/expert_t0.yaml
    joins the good corpus at T08 (see ADR 4; blocked on issue 2)."""
    bad = list(BAD_DIR.glob("*.yaml"))
    assert len(bad) >= 20
    assert len(list(GOOD_DIR.glob("*.yaml"))) >= 3
    assert {f.stem for f in bad} == set(EXPECTED)


@pytest.mark.parametrize("stem", sorted(EXPECTED))
def test_bad_corpus_all_rejected_with_expected_codes(stem):
    """VAL-1, VAL-2, VAL-4, VAL-5, VAL-6: every bad-corpus graph is rejected
    with EXACTLY its expected stable error codes (so a regression can neither
    drop the named code nor sneak in a second one), or flagged with its
    expected warning code for warning-class checks; exit 0 iff ok (CON-8)."""
    expectation = EXPECTED[stem]
    code, report = corpus_report(stem)
    assert codes(report, "errors") == set(expectation["codes"]), report
    assert codes(report, "warnings") == set(expectation.get("warnings", [])), report
    if expectation["codes"]:
        assert report["ok"] is False
        assert code != 0
    else:
        assert report["ok"] is True
        assert code == 0


@pytest.mark.parametrize("path", sorted(GOOD_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_good_corpus_passes(path):
    """VAL-7: every good-corpus graph validates with ok=true, no errors,
    exit 0 (CON-8)."""
    code, report = run_validate(path)
    assert code == 0, report
    assert report["ok"] is True
    assert report["errors"] == []


def test_hints_nonempty():
    """VAL-3: every error and warning across the whole bad corpus carries a
    non-empty hint naming a registry capability or concrete fix, and the
    report is a single JSON object of the specified shape."""
    for stem in sorted(EXPECTED):
        _, report = corpus_report(stem)
        assert set(report) == {"ok", "graph", "errors", "warnings"}
        for entry in report["errors"] + report["warnings"]:
            assert entry["code"], (stem, entry)
            assert entry["hint"].strip(), (stem, entry)
            assert "edge" in entry or "node" in entry, (stem, entry)


def test_manifest_missing_hint_names_closest():
    """VAL-3: MANIFEST_MISSING hints name the closest registry id, turning
    the design-doc typo class into a one-edit fix for the agent."""
    _, report = corpus_report("manifest_missing_typo_oracle_pos")
    hints = " ".join(e["hint"] for e in report["errors"])
    assert "oracle-pose" in hints


def test_schema_mismatch_hint_names_schemas():
    """VAL-3, VAL-4: SCHEMA_MISMATCH hints name both the produced and the
    expected schema from the CAP-2 vocabulary."""
    _, report = corpus_report("schema_mismatch_depth_to_rgb")
    mismatch = [e for e in report["errors"] if e["code"] == "SCHEMA_MISMATCH"]
    assert any("depth_f32" in e["hint"] and "rgb8_image" in e["hint"] for e in mismatch)


def fixture_root(tmp_path: Path, mutations: dict[str, dict]) -> Path:
    """Registry-shaped root with selected real manifests copied in, applying
    {manifest_id: {direction.port: new_schema}} mutations."""
    root = make_registry_root(tmp_path)
    for manifest_id, changes in mutations.items():
        manifest = yaml.safe_load(
            (REPO_ROOT / "registry" / "manifests" / f"{manifest_id}.yaml").read_text()
        )
        for path, schema in changes.items():
            direction, port = path.split(".")
            manifest[direction][port]["schema"] = schema
        write_manifest(root, manifest)
    return root


def write_graph(root: Path, nodes: list[dict]) -> Path:
    graph = root / "g.yaml"
    graph.write_text(yaml.safe_dump({"nodes": nodes}, sort_keys=False))
    return graph


def test_schema_unknown_from_bad_manifest(tmp_path):
    """VAL-4: a manifest schema name outside the CAP-2 vocabulary is its own
    error (SCHEMA_UNKNOWN), never silently passed."""
    root = fixture_root(
        tmp_path,
        {"camera-source": {"outputs.rgb_overhead": "not_a_schema"}, "detector-openvocab": {}},
    )
    graph = write_graph(
        root,
        [
            {"id": "camera-source", "outputs": ["rgb_overhead"]},
            {
                "id": "detector-openvocab",
                "inputs": {"rgb": "camera-source/rgb_overhead"},
                "outputs": ["boxes", "labels"],
            },
        ],
    )
    code, report = run_validate(graph, "--root", str(root))
    assert code != 0
    assert "SCHEMA_UNKNOWN" in codes(report, "errors")


def test_schema_unknown_reports_every_name_deterministically(tmp_path):
    """VAL-4, CON-5: when both ends of an edge carry unknown schema names,
    both are reported, in producer-then-consumer order, identically across
    runs (no hash-seed dependence)."""
    root = fixture_root(
        tmp_path,
        {
            "camera-source": {"outputs.rgb_overhead": "zzz_unknown"},
            "detector-openvocab": {"inputs.rgb": "aaa_unknown"},
        },
    )
    graph = write_graph(
        root,
        [
            {"id": "camera-source", "outputs": ["rgb_overhead"]},
            {
                "id": "detector-openvocab",
                "inputs": {"rgb": "camera-source/rgb_overhead"},
                "outputs": ["boxes", "labels"],
            },
        ],
    )
    _, first = run_validate(graph, "--root", str(root))
    _, second = run_validate(graph, "--root", str(root))
    unknown = [e["detail"] for e in first["errors"] if e["code"] == "SCHEMA_UNKNOWN"]
    assert len(unknown) == 2
    assert "zzz_unknown" in unknown[0] and "aaa_unknown" in unknown[1]
    assert first == second


def test_oracle_leak_not_hidden_by_schema_unknown(tmp_path):
    """VAL-6: an oracle leak is reported even when the same edge also has an
    unknown schema name — safety findings are never masked by schema
    errors."""
    root = fixture_root(
        tmp_path,
        {"camera-source": {"outputs.oracle_state": "mystery"}, "oracle-pose": {}},
    )
    graph = write_graph(
        root,
        [
            {"id": "camera-source", "outputs": ["oracle_state"]},
            {
                "id": "oracle-pose",
                "inputs": {"poses": "camera-source/oracle_state"},
                "outputs": ["target_pose"],
            },
        ],
    )
    code, report = run_validate(graph, "--root", str(root))
    assert code != 0
    assert {"ORACLE_LEAK", "SCHEMA_UNKNOWN"} <= codes(report, "errors")


def test_manifest_without_id_does_not_crash(tmp_path):
    """CON-8: a registry manifest missing its id (a lint-level defect) still
    yields a JSON MANIFEST_MISSING report for graphs, not a traceback."""
    root = fixture_root(tmp_path, {"detector-openvocab": {}})
    anon = yaml.safe_load((REPO_ROOT / "registry" / "manifests" / "camera-source.yaml").read_text())
    del anon["id"]
    (root / "registry" / "manifests" / "camera-source.yaml").write_text(
        yaml.safe_dump(anon, sort_keys=False)
    )
    graph = write_graph(root, [{"id": "camera-sorce", "outputs": ["rgb_overhead"]}])
    code, report = run_validate(graph, "--root", str(root))
    assert code != 0
    assert "MANIFEST_MISSING" in codes(report, "errors")


def test_non_utf8_graph_reported_as_json(tmp_path):
    """CON-8: a non-UTF-8 graph file is a GRAPH_INVALID JSON report, not an
    UnicodeDecodeError traceback."""
    graph = tmp_path / "binary.yaml"
    graph.write_bytes(b"\x80\x81\x82\xff")
    code, report = run_validate(graph)
    assert code != 0
    assert "GRAPH_INVALID" in codes(report, "errors")


def test_allow_unproven_downgrades_eval_error():
    """VAL-2: --allow-unproven downgrades EVAL_MISSING_FOR_MOTION to a
    warning (design doc §8.2.1); the harness never sets it for agents."""
    graph = BAD_DIR / "eval_missing_for_motion.yaml"
    code, report = run_validate(graph, "--allow-unproven")
    assert "EVAL_MISSING_FOR_MOTION" not in codes(report, "errors")
    assert "EVAL_MISSING_FOR_MOTION" in codes(report, "warnings")


def test_missing_graph_file_reported_as_json(tmp_path):
    """CON-8: validating a nonexistent graph path yields a JSON error report
    and nonzero exit, not a traceback."""
    code, report = run_validate(tmp_path / "nope.yaml")
    assert code != 0
    assert report["ok"] is False
    assert "GRAPH_INVALID" in codes(report, "errors")


def test_good_graph_rejected_for_other_embodiment():
    """VAL-2: EMBODIMENT_MISMATCH fires when a node's manifest does not
    support the requested arm profile."""
    code, report = run_validate(GOOD_DIR / "perception_min.yaml", "--embodiment", "mobile")
    assert code != 0
    assert "EMBODIMENT_MISMATCH" in codes(report, "errors")
