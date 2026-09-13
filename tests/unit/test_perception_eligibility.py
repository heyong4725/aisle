"""BND-7 eligibility derived from a retained perception-audit record.

The record is computed from real auditor output against the frozen envelope
(BND-7), after the report hash, envelope hash and run graph are verified
(BND-16); nothing is relaxed (BND-10); the CLI obeys CON-8.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from aisle.harness import perception_audit as pa
from aisle.harness.perception_eligibility import eligibility, verify_report_hash

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import perception_eligibility as elig_tool  # noqa: E402
import perception_record as record_tool  # noqa: E402
from test_perception_audit import MODEL_HASHES, _perfect_audit_inputs  # noqa: E402

pytestmark = pytest.mark.unit

GRAPH = "graphs/expert_t1_l2.yaml"


def _reports():
    corpus, envelope, scored = _perfect_audit_inputs()
    passing = pa.audit(corpus, envelope, scored=scored, model_hashes=MODEL_HASHES)
    broken = copy.deepcopy(scored)
    ids = {
        r["record_id"]
        for r in corpus["records"]
        if r["split"] == "evaluation"
        and r["camera"] == "overhead"
        and r["strata"]["target_class"] == "ibuprofen"
    }
    for s in broken:
        if s["record_id"] in ids:
            s["outcome"] = "wrong_identity"
    failing = pa.audit(corpus, envelope, scored=broken, model_hashes=MODEL_HASHES)
    manifest = {"run_id": "r", "graph": GRAPH, "graph_hash": "a" * 64}
    return passing, failing, envelope, manifest


def _elig(report, envelope, manifest, **overrides):
    kwargs = dict(
        envelope=envelope,
        manifest=manifest,
        raw_predictions=report["raw_predictions"],
    )
    kwargs.update(
        candidate="t1-l2-expert-graph",
        role="short_composition",
        graph=GRAPH,
    )
    return eligibility(report, **{**kwargs, **overrides})


def test_eligibility_is_derived_from_measurements_against_the_envelope():
    """BND-7: a real passing report is eligible; a real failing stratum is named."""
    passing, failing, envelope, manifest = _reports()
    good = _elig(passing, envelope, manifest)
    assert good["ok"] is True and good["eligibility"] == "perception_eligible"
    assert all(s["eligible"] for s in good["strata"]) and good["errors"] == []
    assert good["graph"] == GRAPH and good["graph_hash"] == "a" * 64
    bad = _elig(failing, envelope, manifest)
    assert bad["ok"] is False
    failed = [s["name"] for s in bad["strata"] if not s["eligible"]]
    assert "target_class=ibuprofen" in failed
    assert any("target_class=ibuprofen" in e for e in bad["errors"])
    assert "perception eligibility failed in a stratum" in bad["errors"]


def test_report_pass_flags_cannot_rescue_a_failing_measurement():
    """BND-16 / BND-10: flipping the report's own booleans changes nothing; the hash refuses."""
    _, failing, envelope, manifest = _reports()
    tampered = copy.deepcopy(failing)
    for cells in tampered["strata"].values():
        for cell in cells.values():
            cell["passes_floor"] = cell["refusal_within_limit"] = True
    tampered["eligibility"] = "perception_eligible"
    with pytest.raises(pa.PerceptionAuditError, match="hash"):
        _elig(tampered, envelope, manifest)
    verify_report_hash(failing, failing["raw_predictions"])


@pytest.mark.parametrize("defect", ["envelope", "graph", "run_id", "cell", "failures"])
def test_unbound_inputs_refuse(defect):
    """BND-16: envelope, run graph, run identity and malformed cells fail closed."""
    passing, _, envelope, manifest = _reports()
    overrides = {}
    report = passing
    if defect == "envelope":
        overrides["envelope"] = {**envelope, "accuracy_floor": 0.5}
    elif defect == "graph":
        overrides["graph"] = "graphs/expert_t2.yaml"
    elif defect == "run_id":
        overrides["manifest"] = {**manifest, "run_id": "other"}
    elif defect == "cell":
        report = copy.deepcopy(passing)
        report["strata"]["seed"]["1"] = None
    else:
        report = copy.deepcopy(passing)
        report["failures"] = "not a list"
    with pytest.raises(pa.PerceptionAuditError):
        _elig(
            report,
            overrides.pop("envelope", envelope),
            overrides.pop("manifest", manifest),
            **overrides,
        )


def test_non_cell_failures_are_recorded_as_errors_and_ineligible_rows():
    """BND-7 / BND-16: a latency failure marks every row ineligible and is listed."""
    corpus, envelope, scored = _perfect_audit_inputs()
    slow = copy.deepcopy(scored)
    evaluation = {
        r["record_id"]
        for r in corpus["records"]
        if r["split"] == "evaluation" and r["camera"] == "overhead"
    }
    for s in [s for s in slow if s["record_id"] in evaluation][:3]:
        s["latency_s"] = 99.0
    report = pa.audit(corpus, envelope, scored=slow, model_hashes=MODEL_HASHES)
    assert report["eligibility"] == "not_eligible"
    manifest = {"run_id": "r", "graph": GRAPH, "graph_hash": "a" * 64}
    result = _elig(report, envelope, manifest)
    assert result["ok"] is False
    assert all(s["eligible"] is False for s in result["strata"])
    assert any("latency" in e for e in result["errors"])


def test_record_split_roundtrips_and_cli_follows_con8(tmp_path, capsys):
    """CON-8 / BND-16: the record tool retains a hash-verifiable split; the eligibility
    CLI reads it back, prints JSON, and exits 0 iff eligible."""
    passing, failing, envelope, manifest = _reports()
    run = tmp_path / "run"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps(manifest))
    (run / "episodes.jsonl").write_text("{}\n")
    (tmp_path / "full.json").write_text(json.dumps(passing))
    (tmp_path / "envelope.json").write_text(json.dumps(envelope))
    record = tmp_path / "record"
    assert (
        record_tool.main(
            ["--report", str(tmp_path / "full.json"), "--run", str(run), "--output", str(record)]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["raw_rows"] == len(passing["raw_predictions"])
    assert (
        record_tool.main(
            ["--report", str(tmp_path / "full.json"), "--run", str(run), "--output", str(record)]
        )
        == 1
    )
    capsys.readouterr()
    argv = [
        "--report",
        str(record / "report.json"),
        "--envelope",
        str(tmp_path / "envelope.json"),
        "--candidate",
        "t1-l2-expert-graph",
        "--role",
        "short_composition",
        "--graph",
        GRAPH,
        "--output",
        str(tmp_path / "eligibility.json"),
    ]
    assert elig_tool.main(argv) == 0
    printed = json.loads(capsys.readouterr().out)
    assert (
        printed["ok"] is True and json.loads((tmp_path / "eligibility.json").read_text()) == printed
    )
    (record / "report.json").write_text("not json")
    assert elig_tool.main(argv) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    (tmp_path / "full.json").write_text(json.dumps(failing))
    record2 = tmp_path / "record2"
    assert (
        record_tool.main(
            ["--report", str(tmp_path / "full.json"), "--run", str(run), "--output", str(record2)]
        )
        == 0
    )
    capsys.readouterr()
    argv[1] = str(record2 / "report.json")
    assert elig_tool.main(argv) == 1
    assert json.loads(capsys.readouterr().out)["eligibility"] == "not_eligible"
