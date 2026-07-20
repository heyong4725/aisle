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
from cli_helpers import REPO_ROOT, make_registry_root, run_json, write_manifest

pytestmark = pytest.mark.unit

BAD_DIR = REPO_ROOT / "tests" / "fixtures" / "graphs" / "bad"
GOOD_DIR = REPO_ROOT / "tests" / "fixtures" / "graphs" / "good"

with open(BAD_DIR / "expected.toml", "rb") as _f:
    EXPECTED = tomllib.load(_f)


def run_validate(graph: Path | str, *args: str) -> tuple[int, dict]:
    return run_json("aisle.harness.cli", "validate", str(graph), *args)


def corpus_args(expectation: dict) -> list[str]:
    args = []
    if "embodiment" in expectation:
        args += ["--embodiment", expectation["embodiment"]]
    if "root" in expectation:
        args += ["--root", str(REPO_ROOT / expectation["root"])]
    return args


_CORPUS_CACHE: dict[str, tuple[int, dict]] = {}


def corpus_report(stem: str) -> tuple[int, dict]:
    """One CLI run per bad-corpus file per session, shared across tests."""
    if stem not in _CORPUS_CACHE:
        _CORPUS_CACHE[stem] = run_validate(BAD_DIR / f"{stem}.yaml", *corpus_args(EXPECTED[stem]))
    return _CORPUS_CACHE[stem]


def codes(report: dict, level: str) -> set[str]:
    return {entry["code"] for entry in report[level]}


def test_corpus_minimums():
    """VAL-7: the golden corpus holds >=20 deliberately broken graphs and
    >=3 valid graphs, and includes the design-doc §8.1.4 typo case BY
    CONTENT: an edge referencing controller/joint_cmd while no node has the
    id controller. graphs/expert_t0.yaml joined the good corpus at T08
    (issue 2 resolved by the poses topic, see test_expert_t0_is_good)."""
    bad = list(BAD_DIR.glob("*.yaml"))
    assert len(bad) >= 20
    assert len(list(GOOD_DIR.glob("*.yaml"))) >= 3
    assert {f.stem for f in bad} == set(EXPECTED)
    typo = yaml.safe_load((BAD_DIR / "input_no_producer_controller_typo.yaml").read_text())
    sources = [source for node in typo["nodes"] for source in (node.get("inputs") or {}).values()]
    assert any(str(s).startswith("controller/joint_cmd") for s in sources)
    assert "controller" not in {n["id"] for n in typo["nodes"]}


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


def test_expert_t0_is_good():
    """VAL-7's good-corpus requirement for graphs/expert_t0.yaml is
    satisfied HERE by validating the real file in place (it stays outside
    tests/fixtures/graphs/good/ so no copy can drift): NORMAL validation
    — no --allow-unproven, which HAR-2's rollout gate never sets — passes
    with zero errors AND zero warnings (the M0 evalcards exist, ADR-3
    retired at T08)."""
    code, report = run_validate(REPO_ROOT / "graphs" / "expert_t0.yaml")
    assert code == 0, report
    assert report["ok"] is True and report["errors"] == []
    assert report["warnings"] == []


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
            assert entry["detail"].strip(), (stem, entry)
            assert "edge" in entry or "node" in entry, (stem, entry)
            assert all(isinstance(v, str) for v in entry.values()), (stem, entry)


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


def test_malformed_manifests_reported_as_json(tmp_path):
    """CON-8: manifests violating the CAP-1 JSON Schema in ANY field —
    missing id, scalar embodiment, non-mapping ports, non-numeric rate_hz,
    non-string schema value, bad latency_class enum — become GRAPH_INVALID
    registry errors that name the file, never a traceback (the screen is
    the full capability schema, not a bespoke shape check)."""
    for index, mutate in enumerate(
        (
            lambda m: m.pop("id"),
            lambda m: m.update(embodiment="franka"),
            lambda m: m.update(inputs="rgb"),
            lambda m: m["outputs"]["rgb_overhead"].update(schema=42),
            lambda m: m["inputs"].update(tick={"schema": "scalar_f32", "rate_hz": "fast"}),
            lambda m: m["outputs"]["rgb_overhead"].update(latency_class="warp"),
        )
    ):
        root = fixture_root(tmp_path / str(index), {"detector-openvocab": {}})
        manifest = yaml.safe_load(
            (REPO_ROOT / "registry" / "manifests" / "camera-source.yaml").read_text()
        )
        mutate(manifest)
        (root / "registry" / "manifests" / "camera-source.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False)
        )
        graph = write_graph(root, [{"id": "detector-openvocab", "outputs": ["boxes", "labels"]}])
        code, report = run_validate(graph, "--root", str(root))
        assert code != 0
        assert "GRAPH_INVALID" in codes(report, "errors")
        assert any("camera-source.yaml" in e["detail"] for e in report["errors"])


GUARD_ROOT = REPO_ROOT / "tests" / "fixtures" / "roots" / "with_guard"


def test_motion_gate_is_topological(tmp_path):
    """VAL-5: every backward path into a driver command input must traverse
    the resolved budget-guard — a multi-hop path THROUGH the guard passes,
    a direct guard edge passes, and an indirect path that bypasses the
    guard is MOTION_UNGATED. Uses a fixture registry that includes the
    guard manifest (real registry gains it at T07). See ADR 4."""
    direct = write_graph(
        tmp_path,
        [
            {
                "id": "budget-guard",
                "inputs": {"joint_cmd": "dora/timer/millis/10"},
                "outputs": ["joint_cmd_safe"],
            },
            {
                "id": "arm-driver-sim",
                "inputs": {"joint_cmd": "budget-guard/joint_cmd_safe"},
                "outputs": ["joint_state"],
            },
        ],
    )
    code, report = run_validate(direct, "--root", str(GUARD_ROOT))
    assert code == 0, report
    assert report["ok"] is True

    multihop = tmp_path / "multihop.yaml"
    multihop.write_text(
        yaml.safe_dump(
            {
                "nodes": [
                    {
                        "id": "budget-guard",
                        "inputs": {"joint_cmd": "dora/timer/millis/10"},
                        "outputs": ["joint_cmd_safe"],
                    },
                    {
                        "id": "command-smoother",
                        "inputs": {"cmd": "budget-guard/joint_cmd_safe"},
                        "outputs": ["joint_cmd"],
                    },
                    {
                        "id": "arm-driver-sim",
                        "inputs": {"joint_cmd": "command-smoother/joint_cmd"},
                        "outputs": ["joint_state"],
                    },
                ]
            }
        )
    )
    code, report = run_validate(multihop, "--root", str(GUARD_ROOT))
    assert code == 0, report

    bypass = tmp_path / "bypass.yaml"
    bypass.write_text(
        yaml.safe_dump(
            {
                "nodes": [
                    {
                        "id": "command-smoother",
                        "inputs": {"cmd": "dora/timer/millis/10"},
                        "outputs": ["joint_cmd"],
                    },
                    {
                        "id": "arm-driver-sim",
                        "inputs": {"joint_cmd": "command-smoother/joint_cmd"},
                        "outputs": ["joint_state"],
                    },
                ]
            }
        )
    )
    code, report = run_validate(bypass, "--root", str(GUARD_ROOT))
    assert code != 0
    assert "MOTION_UNGATED" in codes(report, "errors")


def test_fixture_root_schemas_match_real_registry():
    """The CAP-1 schema copies inside every fixture root must be identical
    to the real registry's — fixture validation must never drift from the
    live contract."""
    real = (REPO_ROOT / "registry" / "schema" / "capability.schema.json").read_bytes()
    roots = sorted((REPO_ROOT / "tests" / "fixtures" / "roots").iterdir())
    assert roots
    for root in roots:
        copy = root / "registry" / "schema" / "capability.schema.json"
        assert copy.read_bytes() == real, copy


def test_motion_gate_mixed_fanin_is_ungated(tmp_path):
    """VAL-5 (ADR 4): one unguarded input taints the node — a smoother fed
    by BOTH the guard and a bare timer leaves an unguarded path into the
    driver, so the sink is MOTION_UNGATED."""
    graph = write_graph(
        tmp_path,
        [
            {
                "id": "budget-guard",
                "inputs": {"joint_cmd": "dora/timer/millis/10"},
                "outputs": ["joint_cmd_safe"],
            },
            {
                "id": "command-smoother",
                "inputs": {"cmd": "budget-guard/joint_cmd_safe", "aux": "dora/timer/millis/10"},
                "outputs": ["joint_cmd"],
            },
            {
                "id": "arm-driver-sim",
                "inputs": {"joint_cmd": "command-smoother/joint_cmd"},
                "outputs": ["joint_state"],
            },
        ],
    )
    code, report = run_validate(graph, "--root", str(GUARD_ROOT))
    assert code != 0
    assert "MOTION_UNGATED" in codes(report, "errors")


def test_motion_gate_cycle_without_guard_is_ungated(tmp_path):
    """VAL-5 (ADR 4): a cycle with no guard on it never reaches a gated
    root, so a driver fed from the cycle is MOTION_UNGATED (and the
    validator terminates rather than recursing forever)."""
    graph = write_graph(
        tmp_path,
        [
            {
                "id": "command-smoother",
                "inputs": {"cmd": "command-mixer/joint_cmd"},
                "outputs": ["joint_cmd"],
            },
            {
                "id": "command-mixer",
                "inputs": {"cmd": "command-smoother/joint_cmd"},
                "outputs": ["joint_cmd"],
            },
            {
                "id": "arm-driver-sim",
                "inputs": {"joint_cmd": "command-smoother/joint_cmd"},
                "outputs": ["joint_state"],
            },
        ],
    )
    code, report = run_validate(graph, "--root", str(GUARD_ROOT))
    assert code != 0
    assert "MOTION_UNGATED" in codes(report, "errors")


def test_verifier_feedback_loop_is_legal():
    """VAL-6 (ADR 5): verifier verdicts feeding lifecycle nodes is the
    sanctioned pattern — episode_result consumption downstream of the
    verifier is not an oracle leak."""
    code, report = run_validate(GOOD_DIR / "verifier_feedback_loop.yaml")
    assert code == 0, report
    assert "ORACLE_LEAK" not in codes(report, "errors")


def test_unwired_manifest_inputs_are_legal(tmp_path):
    """ADR 5: wiring none of a manifest's declared inputs is legal (dora
    permits subsets; source nodes have zero inputs by design)."""
    graph = write_graph(
        tmp_path, [{"id": "detector-openvocab", "inputs": {}, "outputs": ["boxes", "labels"]}]
    )
    code, report = run_validate(graph)
    assert code == 0, report


def test_missing_node_hint_lists_graph_nodes():
    """VAL-3: when no similar node id exists, the INPUT_NO_PRODUCER hint
    lists the graph's actual nodes instead of an empty did-you-mean."""
    _, report = corpus_report("input_no_producer_missing_node")
    hints = " ".join(e["hint"] for e in report["errors"])
    assert "did you mean ''" not in hints
    assert "detector-openvocab" in hints


def test_weak_similarity_gives_search_hint():
    """VAL-3: a node id with no close manifest match (warp-drive) gets the
    registry-search command, not a misleading did-you-mean."""
    _, report = corpus_report("manifest_missing_unknown_node")
    hints = " ".join(e["hint"] for e in report["errors"])
    assert "arm-driver-sim" not in hints
    assert "search --provides" in hints


def test_undeclared_port_hint_is_actionable():
    """VAL-3: the undeclared-input hint names the corrective action and the
    real ports, not just a bare list."""
    _, report = corpus_report("schema_mismatch_undeclared_port")
    mismatch = [e for e in report["errors"] if e["code"] == "SCHEMA_MISMATCH"]
    assert any("rename the input" in e["hint"] and "'rgb'" in e["hint"] for e in mismatch)


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
    code, report = run_validate(
        graph, "--allow-unproven", "--root", str(REPO_ROOT / "tests/fixtures/roots/eval_null")
    )
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
