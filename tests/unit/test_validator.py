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
        # VAL-3 as amended by ADR-24 D5: dist_state is the labeled
        # non-attesting diagnostic
        assert set(report) == {"ok", "graph", "errors", "warnings", "dist_state"}
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
    graph = write_graph(tmp_path, [{"id": "oracle-pose", "inputs": {}, "outputs": ["target_pose"]}])
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
    support the requested arm profile — the franka-only expert graph
    rejected for so101. (mobile is NOT such a case any more: MOB-4 resolves
    it to the franka arm; see test_mobility.)"""
    code, report = run_validate(REPO_ROOT / "graphs" / "expert_t0.yaml", "--embodiment", "so101")
    assert code != 0
    assert "EMBODIMENT_MISMATCH" in codes(report, "errors")


def test_install_missing_hint_names_installed_alternative():
    """VAL-2 INSTALL_MISSING (H1-discovered class, PR #33 findings): a
    pip:-sourced manifest whose distribution is absent errors rather than
    validating a graph that cannot launch; per VAL-3 the hint MUST name an
    installed registry alternative with the same capability when one
    exists (oracle-pose provides object_pose alongside pose-estimator).
    Runs against the reserved_dists root like every INSTALL_MISSING
    expectation (PR #65 review: this dedicated test was the one remaining
    ambient-env coupling)."""
    code, report = corpus_report("install_missing_detector")
    assert code != 0
    entries = [e for e in report["errors"] if e["code"] == "INSTALL_MISSING"]
    assert {e["node"] for e in entries} == {"detector-openvocab", "pose-estimator"}
    pose_hint = next(e["hint"] for e in entries if e["node"] == "pose-estimator")
    assert "oracle-pose" in pose_hint
    # the NO-alternative fallback branch (detector-openvocab is the sole
    # object_detection provider) must stay actionable, not just non-empty —
    # naming the RESERVED distribution, not the ambient-coupled real one
    det_hint = next(e["hint"] for e in entries if e["node"] == "detector-openvocab")
    assert "aisle-corpus-reserved-yolo" in det_hint and "install" in det_hint


def test_corpus_and_hint_survive_dora_dists_installed(monkeypatch):
    """PR #65 review P1 (the reviewer's reproduction, pinned): with fake
    installed metadata for ALL THREE real dora perception dists — the
    exact env-change the INSTALL_MISSING hint invites — the corpus
    fixture AND the dedicated hint path must be unmoved: reserved names
    keep firing INSTALL_MISSING."""
    import aisle.harness.validate as val
    from aisle.harness.validate import validate

    real = val._pip_installed.__wrapped__  # unwrap the functools.cache

    def fake_installed(dist):
        return True if dist in ("dora-yolo", "dora-pose", "dora-ocr") else real(dist)

    monkeypatch.setattr(val, "_pip_installed", fake_installed)
    report = validate(
        BAD_DIR / "install_missing_detector.yaml",
        REPO_ROOT / "tests" / "fixtures" / "roots" / "reserved_dists",
        "franka",
        False,
    )
    codes_fired = {e["code"] for e in report["errors"]}
    assert codes_fired == {"INSTALL_MISSING"}, codes_fired
    det_hint = next(e["hint"] for e in report["errors"] if e["node"] == "detector-openvocab")
    assert "aisle-corpus-reserved-yolo" in det_hint


def _pip_manifest(base_id: str, **overrides) -> dict:
    manifest = yaml.safe_load(
        (REPO_ROOT / "registry" / "manifests" / f"{base_id}.yaml").read_text()
    )
    manifest.update(overrides)
    return manifest


def _single_node_graph(root, manifest):
    return write_graph(
        root, [{"id": manifest["id"], "inputs": {}, "outputs": list(manifest["outputs"])}]
    )


def test_install_missing_corpus_is_env_independent():
    """Issue #37 (full fix of the PR #34 finding): the INSTALL_MISSING
    corpus entries run against the reserved_dists fixture root, whose
    perception manifests name RESERVED never-published distributions —
    installing the real dora-yolo/dora-pose/dora-ocr (exactly what the
    INSTALL_MISSING hint invites) must shift NOTHING. The reserved names
    stay uninstallable by convention; assert it so a collision fails in
    one labeled place."""
    from aisle.harness.validate import _pip_installed

    for dist in (
        "aisle-corpus-reserved-yolo",
        "aisle-corpus-reserved-pose",
        "aisle-corpus-reserved-ocr",
    ):
        assert not _pip_installed(dist), (
            f"reserved corpus name {dist!r} is installed in this environment — "
            "these names exist to be permanently absent; pick a new reserved "
            "name in tests/fixtures/roots/reserved_dists and expected.toml"
        )


def test_reserved_root_mirrors_real_registry():
    """Issue #37: the reserved_dists fixture root is the REAL registry
    with exactly three perception sources swapped to reserved names —
    pinned here so registry changes cannot silently drift the corpus
    environment."""
    import yaml as _yaml

    swaps = {
        "detector-openvocab": "pip:aisle-corpus-reserved-yolo",
        "pose-estimator": "pip:aisle-corpus-reserved-pose",
        "ocr-label": "pip:aisle-corpus-reserved-ocr",
    }
    real_dir = REPO_ROOT / "registry" / "manifests"
    fixture_dir = REPO_ROOT / "tests" / "fixtures" / "roots" / "reserved_dists"
    fixture_manifests = fixture_dir / "registry" / "manifests"
    assert {p.name for p in real_dir.glob("*.yaml")} == {
        p.name for p in fixture_manifests.glob("*.yaml")
    }
    for real_path in sorted(real_dir.glob("*.yaml")):
        real = _yaml.safe_load(real_path.read_text())
        copy = _yaml.safe_load((fixture_manifests / real_path.name).read_text())
        if real["id"] in swaps:
            assert copy["source"] == swaps[real["id"]], real["id"]
            real, copy = dict(real), dict(copy)
            real.pop("source"), copy.pop("source")
        assert copy == real, f"{real_path.name} drifted from the real registry"
        source = _yaml.safe_load((fixture_manifests / real_path.name).read_text()).get("source")
        if isinstance(source, str) and ":" not in source:
            assert (fixture_dir / source).is_file(), f"missing stub for {source}"


def test_install_missing_installed_distribution_passes(tmp_path):
    """VAL-2: the POSITIVE path — a pip: source whose distribution IS
    installed (pytest, guaranteed in the test env) must not error; case
    and PEP 503 name normalization are importlib.metadata's job (PyTest ==
    pytest). Kills the `_pip_installed = lambda d: False` mutation the
    corpus alone cannot catch (PR #34 review)."""
    root = make_registry_root(tmp_path)
    for source in ("pip:pytest", "pip:PyTest"):
        write_manifest(root, _pip_manifest("oracle-pose", source=source))
        graph = _single_node_graph(root, _pip_manifest("oracle-pose"))
        code, report = run_validate(graph, "--root", str(root))
        assert code == 0, (source, report)
        assert "INSTALL_MISSING" not in codes(report, "errors")


def test_install_missing_empty_and_decorated_dist_names(tmp_path):
    """PR #34 adversarial review (as evolved by issue #35): `source:
    "pip:"` once crashed the CLI with an uncaught ValueError — the CAP-1
    source pattern now rejects an empty distribution at the schema layer
    (structured GRAPH_INVALID naming the manifest, still no traceback,
    CON-8). Decorations (`pip:pytest[x]`, `pip:pytest==1.0`,
    `pip: pytest`) stay valid: the dist name is normalized (strip + cut
    at [=<>!~;@) before probing, so INSTALLED dists are never falsely
    flagged."""
    root = make_registry_root(tmp_path)
    graph = _single_node_graph(root, _pip_manifest("oracle-pose"))
    # PR #63 review P2: whitespace-only dists normalize to empty and must
    # die at the SAME schema layer as the bare `pip:`
    for source in ("pip:", "pip:  ", "pip:\t"):
        write_manifest(root, _pip_manifest("oracle-pose", source=source))
        code, report = run_validate(graph, "--root", str(root))
        assert code != 0, source
        assert "GRAPH_INVALID" in codes(report, "errors"), source  # JSON, no crash
        assert any("does not match" in e["detail"] for e in report["errors"]), source
    for source in ("pip:pytest[extra]", "pip:pytest==1.0", "pip: pytest"):
        write_manifest(root, _pip_manifest("oracle-pose", source=source))
        code, report = run_validate(graph, "--root", str(root))
        assert code == 0, (source, report)


def test_pip_scheme_casing_is_a_schema_violation(tmp_path):
    """PR #34 adversarial review (as evolved by issue #35): `PIP:x` once
    dodged INSTALL_MISSING via exact-lowercase matching. The CAP-1 source
    pattern now rejects any colon-bearing source that is not lowercase
    `pip:` at the SCHEMA layer — casing (and every unknown scheme) is
    closed off before validation, not merely normalized."""
    root = make_registry_root(tmp_path)
    write_manifest(root, _pip_manifest("oracle-pose", source="PIP:aisle-review-absent-dist"))
    graph = _single_node_graph(root, _pip_manifest("oracle-pose"))
    code, report = run_validate(graph, "--root", str(root))
    assert code != 0
    assert "GRAPH_INVALID" in codes(report, "errors")
    assert any("oracle-pose" in e["detail"] for e in report["errors"])  # names the manifest


def test_install_missing_alternatives_are_usable(tmp_path):
    """PR #34 adversarial review: the hint must not send the agent to an
    alternative that fails the NEXT compile — an uninstalled pip peer, an
    embodiment-incompatible node, or a partial provider (covers only some
    of the missing node's capabilities)."""
    root = make_registry_root(tmp_path)
    missing = _pip_manifest("pose-estimator", source="pip:aisle-review-absent-dist")
    write_manifest(root, missing)
    # uninstalled pip peer: same capability, also absent — must NOT appear
    write_manifest(
        root,
        _pip_manifest("oracle-pose", id="pose-peer-pip", source="pip:aisle-review-absent-peer"),
    )
    # embodiment-incompatible peer: so101-only arm on a franka graph
    so101_only = _pip_manifest("oracle-pose", id="pose-peer-so101")
    so101_only["embodiment"] = {"arm": ["so101"], "gripper": "any"}
    write_manifest(root, so101_only)
    # the genuinely usable peer
    write_manifest(root, _pip_manifest("oracle-pose"))
    graph = _single_node_graph(root, missing)
    code, report = run_validate(graph, "--root", str(root))
    assert code != 0
    hint = next(e["hint"] for e in report["errors"] if e["code"] == "INSTALL_MISSING")
    assert "oracle-pose" in hint
    assert "pose-peer-pip" not in hint
    assert "pose-peer-so101" not in hint


def test_path_manifest_mismatch_rule_edges(tmp_path):
    """VAL-2 PATH_MANIFEST_MISMATCH (issue #36): pip sources accept the
    manifest source verbatim or the bare distribution name — anything
    else is a mismatch; path-less nodes are dora's launch problem, not
    this check's; absolute paths resolving to the manifest source (the
    instrumented/staged-copy form) match."""
    from aisle.harness.validate import validate_nodes

    manifests = {
        "detector-openvocab": {
            "id": "detector-openvocab",
            "provides": [],
            "source": "pip:dora-yolo",
            "safety_class": "perception",
            "embodiment": {"arm": ["franka"]},
        },
        "oracle-pose": {
            "id": "oracle-pose",
            "provides": [],
            "source": "src/aisle/nodes/oracle_pose.py",
            "safety_class": "perception",
            "embodiment": {"arm": ["franka"]},
        },
    }

    def codes_for(node):
        errors, _ = validate_nodes(
            [node],
            manifests,
            set(),
            "franka",
            True,
            graph_dir=REPO_ROOT / "graphs",
            root=REPO_ROOT,
        )
        return {e["code"] for e in errors}

    ok_verbatim = {"id": "detector-openvocab", "path": "pip:dora-yolo"}
    ok_bare = {"id": "detector-openvocab", "path": "dora-yolo"}
    spoofed_pip = {"id": "detector-openvocab", "path": "../skills/evil.py"}
    pathless = {"id": "detector-openvocab"}
    # PR #62 review P2: decorated/case-varied pip sources must accept the
    # NORMALIZED bare dist (reuse _pip_dist), matching the INSTALL_MISSING
    # normalization contract — not the unparsed suffix
    manifests["detector-decorated"] = {
        "id": "detector-decorated",
        "provides": [],
        "source": "pip:dora-yolo[gpu]",
        "safety_class": "perception",
        "embodiment": {"arm": ["franka"]},
    }
    manifests["detector-pinned"] = {
        "id": "detector-pinned",
        "provides": [],
        "source": "PIP:dora-yolo==1.0",
        "safety_class": "perception",
        "embodiment": {"arm": ["franka"]},
    }
    ok_decorated_bare = {"id": "detector-decorated", "path": "dora-yolo"}
    ok_decorated_verbatim = {"id": "detector-decorated", "path": "pip:dora-yolo[gpu]"}
    ok_cased_bare = {"id": "detector-pinned", "path": "dora-yolo"}
    spoofed_decorated = {"id": "detector-decorated", "path": "dora-yolo-evil"}
    ok_absolute = {
        "id": "oracle-pose",
        "path": str((REPO_ROOT / "src" / "aisle" / "nodes" / "oracle_pose.py").resolve()),
    }
    spoofed_path = {"id": "oracle-pose", "path": "../src/aisle/nodes/grasp_topdown.py"}

    assert "PATH_MANIFEST_MISMATCH" not in codes_for(ok_verbatim)
    assert "PATH_MANIFEST_MISMATCH" not in codes_for(ok_bare)
    assert "PATH_MANIFEST_MISMATCH" in codes_for(spoofed_pip)
    assert "PATH_MANIFEST_MISMATCH" not in codes_for(pathless)
    assert "PATH_MANIFEST_MISMATCH" not in codes_for(ok_absolute)
    assert "PATH_MANIFEST_MISMATCH" in codes_for(spoofed_path)
    assert "PATH_MANIFEST_MISMATCH" not in codes_for(ok_decorated_bare)
    assert "PATH_MANIFEST_MISMATCH" not in codes_for(ok_decorated_verbatim)
    assert "PATH_MANIFEST_MISMATCH" not in codes_for(ok_cased_bare)
    assert "PATH_MANIFEST_MISMATCH" in codes_for(spoofed_decorated)


def test_path_check_has_no_second_base_bypass(tmp_path):
    """PR #62 review P1: the graphs-dir fallback approved any path that
    WOULD match if the graph lived in graphs/ — but a staged graph (the
    live-swap tmpdir) lives elsewhere, and dora resolves against ITS
    base, so validator and runtime could resolve different code under an
    approved id. There is exactly ONE base: the graph's own directory. A
    relative path in a tmpdir-staged graph that only matches via the
    graphs/ composition is a MISMATCH."""
    from aisle.harness.validate import validate_nodes

    manifests = {
        "oracle-pose": {
            "id": "oracle-pose",
            "provides": [],
            "source": "src/aisle/nodes/oracle_pose.py",
            "safety_class": "perception",
            "embodiment": {"arm": ["franka"]},
        }
    }
    staged_dir = tmp_path / "aisle-swap-adversary"
    staged_dir.mkdir()
    node = {"id": "oracle-pose", "path": "../src/aisle/nodes/oracle_pose.py"}
    errors, _ = validate_nodes(
        [node], manifests, set(), "franka", True, graph_dir=staged_dir, root=REPO_ROOT
    )
    assert "PATH_MANIFEST_MISMATCH" in {e["code"] for e in errors}
    absolute = {
        "id": "oracle-pose",
        "path": str((REPO_ROOT / "src/aisle/nodes/oracle_pose.py").resolve()),
    }
    errors, _ = validate_nodes(
        [absolute], manifests, set(), "franka", True, graph_dir=staged_dir, root=REPO_ROOT
    )
    assert "PATH_MANIFEST_MISMATCH" not in {e["code"] for e in errors}


def _path_manifest(mid, source):
    return {
        "id": mid,
        "kind": "node",
        "provides": ["object_pose"],
        "requires": [],
        "inputs": {},
        "outputs": {},
        "embodiment": {"arm": ["franka"], "gripper": "any"},
        "safety_class": "perception",
        "eval": None,
        "origin": "hub",
        "source": source,
    }


def test_source_invalid_requires_contained_regular_file(tmp_path):
    """PR #63 review P1: `root / source` is NOT containment — an absolute
    source (`/etc/hosts`) survives the join, `../` escapes the root, a
    symlink can resolve outside it, and exists() accepts directories.
    The source must be a REGULAR FILE resolved UNDER the root."""
    from aisle.harness.validate import validate_nodes

    root = tmp_path / "root"
    (root / "src").mkdir(parents=True)
    (root / "src" / "real.py").write_text("ok")
    outside = tmp_path / "outside.py"
    outside.write_text("external mutable code")
    (root / "src" / "link.py").symlink_to(outside)

    def codes_for(source):
        manifests = {"n": _path_manifest("n", source)}
        errors, _ = validate_nodes(
            [{"id": "n"}], manifests, set(), "franka", True, graph_dir=root, root=root
        )
        return {e["code"] for e in errors}

    assert "SOURCE_INVALID" not in codes_for("src/real.py")  # the honest case
    assert "SOURCE_INVALID" in codes_for(str(outside.resolve()))  # absolute
    assert "SOURCE_INVALID" in codes_for("../outside.py")  # traversal
    assert "SOURCE_INVALID" in codes_for("src")  # directory, not a file
    assert "SOURCE_INVALID" in codes_for("src/link.py")  # symlink escaping root


def test_install_missing_alternatives_exclude_invalid_sources(tmp_path):
    """PR #63 review P2: the INSTALL_MISSING hint MUST never name an
    alternative that fails the NEXT compile (VAL-2) — a same-capability
    manifest whose path source names no file is exactly such an
    alternative and must not be recommended."""
    root = make_registry_root(tmp_path)
    write_manifest(root, _pip_manifest("pose-estimator", source="pip:aisle-review-absent-dist"))
    ghost = _path_manifest("ghost-pose", "src/aisle/nodes/does_not_exist.py")
    ghost["provides"] = ["object_pose"]
    path = root / "registry" / "manifests" / "ghost-pose.yaml"
    path.write_text(yaml.safe_dump(ghost, sort_keys=False))  # NO auto-stub: stays ghost
    real = _path_manifest("real-pose", "src/aisle/nodes/real_pose.py")
    real["provides"] = ["object_pose"]
    write_manifest(root, real)  # helper stubs the source file: launchable
    # PR #64 review P2: EVERY manifest-level next-compile check must be
    # mirrored — a base-requiring peer on a fixed-base graph fails
    # EMBODIMENT_MISMATCH, an eval-null MOTION peer fails
    # EVAL_MISSING_FOR_MOTION; neither may be recommended
    mobile_peer = _path_manifest("mobile-pose", "src/aisle/nodes/mobile_pose.py")
    mobile_peer["provides"] = ["object_pose"]
    mobile_peer["embodiment"] = {"arm": ["franka"], "gripper": "any", "base": ["mobile"]}
    write_manifest(root, mobile_peer)
    motion_peer = _path_manifest("motion-pose", "src/aisle/nodes/motion_pose.py")
    motion_peer["provides"] = ["object_pose"]
    motion_peer["safety_class"] = "motion"
    write_manifest(root, motion_peer)  # eval stays null: unproven motion
    graph = _single_node_graph(root, _pip_manifest("pose-estimator"))
    code, report = run_validate(graph, "--root", str(root))
    assert code != 0
    hint = next(e["hint"] for e in report["errors"] if e["code"] == "INSTALL_MISSING")
    assert "real-pose" in hint
    assert "ghost-pose" not in hint  # would fail the next compile (SOURCE_INVALID)
    assert "mobile-pose" not in hint  # would fail it (EMBODIMENT_MISMATCH, base)
    assert "motion-pose" not in hint  # would fail it (EVAL_MISSING_FOR_MOTION)
