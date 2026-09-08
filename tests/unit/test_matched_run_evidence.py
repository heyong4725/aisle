"""MON-12/MON-13: retain real rollout file formats without rewriting outcomes."""

import hashlib
import json

import pytest

pytestmark = pytest.mark.unit


def _run(tmp_path):
    source = tmp_path / "engineering-run"
    source.mkdir()
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "engineering-run",
                "seeds": [7],
                "verifier": "oracle",
                "tier": "T1",
            }
        )
    )
    (source / "episodes.jsonl").write_text('{"seed":7,"success":false,"t_end":1.25}\n')
    (source / "traces").mkdir()
    (source / "traces" / "raw.bin").write_bytes(b"raw retained fixture")
    return source


def test_run_collection_preserves_failed_outcome_and_every_raw_file(tmp_path):
    """MON-12: an unsuccessful episode remains unsuccessful after byte-preserving collection."""
    from aisle.harness.matched_evidence import retain_run

    source = _run(tmp_path)
    output = tmp_path / "retained"
    report = retain_run(source, output, run_id="engineering-run")
    assert report["ok"] is True
    assert report["eligible_for_estimate"] is False
    assert report["episodes"][0]["success"] is False
    for name in ("manifest.json", "episodes.jsonl", "traces/raw.bin"):
        assert (output / "raw" / name).read_bytes() == (source / name).read_bytes()
        assert report["files"][name] == hashlib.sha256((source / name).read_bytes()).hexdigest()
    assert report["guards"]["status"] == "not_collected"
    assert json.loads((output / "run-evidence.json").read_text()) == report


@pytest.mark.parametrize("fault", ["identity", "missing_episodes", "invalid_json", "symlink"])
def test_run_collection_preserves_incomplete_attempt_with_error(tmp_path, fault):
    """MON-13: wrong identities, missing streams and foreign bytes fail with retained evidence."""
    from aisle.harness.matched_evidence import retain_run

    source = _run(tmp_path)
    if fault == "identity":
        (source / "manifest.json").write_text('{"run_id":"other-run","seeds":[7]}')
    elif fault == "missing_episodes":
        (source / "episodes.jsonl").unlink()
    elif fault == "invalid_json":
        (source / "episodes.jsonl").write_text('{"unfinished":')
    else:
        outside = tmp_path / "outside.txt"
        outside.write_text("foreign evidence")
        (source / "foreign.txt").symlink_to(outside)
    output = tmp_path / "retained"
    report = retain_run(source, output, run_id="engineering-run")
    assert report["ok"] is False
    assert report["error"]
    assert report["eligible_for_estimate"] is False
    assert json.loads((output / "run-evidence.json").read_text()) == report
    assert not (output / "raw" / "foreign.txt").exists()


def test_run_collection_cannot_overwrite_prior_evidence(tmp_path):
    """MON-11/MON-13: a second attempt cannot replace the first collection."""
    from aisle.harness.matched_evidence import retain_run

    source = _run(tmp_path)
    output = tmp_path / "retained"
    retain_run(source, output, run_id="engineering-run")
    before = (output / "run-evidence.json").read_bytes()
    with pytest.raises(FileExistsError):
        retain_run(source, output, run_id="engineering-run")
    assert (output / "run-evidence.json").read_bytes() == before


def test_run_collector_cli_uses_actual_rollout_files(tmp_path):
    """CON-8/MON-12: the controller CLI retains a run and emits its collection verdict."""
    import subprocess
    import sys
    from pathlib import Path

    source = _run(tmp_path)
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"source": str(source), "run_id": "engineering-run"}))
    output = tmp_path / "retained"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "tools/matched_campaign.py"),
            "collect-run",
            "--request",
            str(request),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == json.loads((output / "run-evidence.json").read_text())
    assert json.loads(result.stdout)["eligible_for_estimate"] is False


def _guard_traces(source):
    import pyarrow as pa

    commands = pa.table({"data": [[0.5], [1.0]]})
    violations = pa.table({"text": ['[{"reason":"joint_limit","requested":2.0,"clamped":1.0}]']})
    for topic, table in (("joint_cmd_safe", commands), ("violation", violations)):
        path = source / "traces" / f"budget-guard__{topic}.arrow"
        with path.open("wb") as stream, pa.ipc.new_stream(stream, table.schema) as writer:
            writer.write_table(table)


def test_guard_summary_is_derived_from_retained_arrow_streams(tmp_path):
    """MON-12: guard evidence uses actual retained trace data, not a supplied summary."""
    from aisle.harness.matched_evidence import retain_run

    source = _run(tmp_path)
    _guard_traces(source)
    report = retain_run(source, tmp_path / "retained", run_id="engineering-run")
    assert report["ok"] is True
    assert report["guards"]["status"] == "retained"
    assert report["guards"]["summary"]["commands"] == 2
    assert report["guards"]["summary"]["clamped_commands"] == 1
    assert report["guards"]["summary"]["divergence_rate"] == 0.5


def test_truncated_guard_stream_is_retained_but_not_accepted(tmp_path):
    """MON-12/MON-13: a partial stream cannot silently become complete guard evidence."""
    from aisle.harness.matched_evidence import retain_run

    source = _run(tmp_path)
    _guard_traces(source)
    trace = source / "traces/budget-guard__joint_cmd_safe.arrow"
    trace.write_bytes(trace.read_bytes()[:40])
    output = tmp_path / "retained"
    report = retain_run(source, output, run_id="engineering-run")
    assert report["ok"] is False
    assert report["guards"]["status"] != "retained"
    assert "Arrow" in report["error"]
    assert (
        output / "raw/traces/budget-guard__joint_cmd_safe.arrow"
    ).read_bytes() == trace.read_bytes()


def test_evaluator_evidence_preserves_declared_verifier_and_seed_pairing(tmp_path):
    """MON-12: evaluator records retain their declared identity and ordered seed evidence."""
    from aisle.harness.matched_evidence import retain_run

    source = _run(tmp_path)
    report = retain_run(source, tmp_path / "retained", run_id="engineering-run")
    assert report["evaluator"] == {
        "status": "retained",
        "verifier": "oracle",
        "planned_seeds": [7],
        "observed_seeds": [7],
        "complete": True,
        "episodes_path": "raw/episodes.jsonl",
    }
    assert report["eligible_for_estimate"] is False


def test_unfinished_run_keeps_missing_seed_explicit(tmp_path):
    """MON-12: retaining a partial run cannot turn absent episodes into evaluator success."""
    from aisle.harness.matched_evidence import retain_run

    source = _run(tmp_path)
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["seeds"].append(8)
    (source / "manifest.json").write_text(json.dumps(manifest))
    report = retain_run(source, tmp_path / "retained", run_id="engineering-run")
    assert report["ok"] is True
    assert report["evaluator"]["complete"] is False
    assert report["evaluator"]["observed_seeds"] == [7]
    assert report["evaluator"]["planned_seeds"] == [7, 8]


@pytest.mark.parametrize("fault", ["foreign_seed", "missing_seed", "missing_verifier"])
def test_unbound_evaluator_identity_cannot_pass_collection(tmp_path, fault):
    """MON-13: collected evaluator outcomes must bind declared seed and verifier identities."""
    from aisle.harness.matched_evidence import retain_run

    source = _run(tmp_path)
    if fault == "foreign_seed":
        (source / "episodes.jsonl").write_text('{"seed":999,"success":true}\n')
    elif fault == "missing_seed":
        (source / "episodes.jsonl").write_text('{"success":true}\n')
    else:
        manifest = json.loads((source / "manifest.json").read_text())
        del manifest["verifier"]
        (source / "manifest.json").write_text(json.dumps(manifest))
    report = retain_run(source, tmp_path / "retained", run_id="engineering-run")
    assert report["ok"] is False
    assert report["error"]
    assert report["episodes"] is not None


@pytest.mark.parametrize(
    "duration, valid",
    [(1.25, True), (0, True), (None, False), (-1, False), (True, False), (10**400, False)],
)
def test_episode_simulator_time_requires_observed_valid_duration(tmp_path, duration, valid):
    """MON-12: episode simulation time is measured without turning missing durations into zero."""
    from aisle.harness.matched_evidence import retain_run

    source = _run(tmp_path)
    episode = {"seed": 7, "success": False}
    if duration is not None:
        episode["t_end"] = duration
    (source / "episodes.jsonl").write_text(json.dumps(episode) + "\n")
    report = retain_run(source, tmp_path / "retained", run_id="engineering-run")
    measured = report["simulator"]
    if valid:
        assert measured == {
            "status": "retained",
            "unit": "episode_sim_seconds",
            "value": duration,
            "observed_episodes": 1,
            "includes_reset_work": False,
        }
    else:
        assert measured["status"] == "not_collected"
        assert measured["value"] is None


def test_work_collection_recomputes_raw_records_instead_of_trusting_summary(tmp_path):
    """MON-12/MON-13: retained physical work comes from graph-bound raw operation records."""
    from aisle.harness.matched_evidence import retain_run
    from aisle.harness.simulator_work import reserve_launch, work_context

    source = _run(tmp_path)
    graph = source / "graph.yaml"
    graph.write_text("nodes: []\n")
    digest = hashlib.sha256(graph.read_bytes()).hexdigest()
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["exec_graph_hashes"] = [digest]
    manifest_path.write_text(json.dumps(manifest))
    directory = source / "simulator-work"
    directory.mkdir()
    binding = reserve_launch(directory, run_id="engineering-run", launch=0, graph_sha256=digest)
    with work_context(binding, dt_ns=4, n_envs=2) as work:
        work.call("step", lambda: None)
    (source / "simulator-work-summary.json").write_text('{"completed_env_sim_ns":999999}')
    result = retain_run(source, tmp_path / "retained", run_id="engineering-run")
    accounting = result["simulator_work"]
    assert accounting["status"] == "recomputed"
    assert accounting["summary"]["completed_env_sim_ns"] == 8
    assert accounting["summary"]["producer_coverage_complete"] is False
    assert "simulator-work-summary.json" in result["files"]


def test_work_collection_refuses_substituted_execution_graph(tmp_path):
    """MON-13: a receipt's graph digest must also match the retained executed graph bytes."""
    from aisle.harness.matched_evidence import retain_run
    from aisle.harness.simulator_work import reserve_launch

    source = _run(tmp_path)
    directory = source / "simulator-work"
    directory.mkdir()
    reserve_launch(directory, run_id="engineering-run", launch=0, graph_sha256="a" * 64)
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["exec_graph_hashes"] = ["a" * 64]
    manifest_path.write_text(json.dumps(manifest))
    (source / "graph.yaml").write_text("changed graph")
    result = retain_run(source, tmp_path / "retained", run_id="engineering-run")
    assert result["simulator_work"]["status"] == "unresolved"
    assert result["simulator_work"]["summary"] is None
    assert "graph" in result["simulator_work"]["error"]


def test_work_without_run_manifest_remains_retained_and_unresolved(tmp_path):
    """MON-12: failed startup evidence cannot invent a complete expected-launch inventory."""
    from aisle.harness.matched_evidence import retain_run
    from aisle.harness.simulator_work import reserve_launch

    source = _run(tmp_path)
    (source / "manifest.json").unlink()
    directory = source / "simulator-work"
    directory.mkdir()
    reserve_launch(directory, run_id="engineering-run", launch=0, graph_sha256="a" * 64)
    result = retain_run(source, tmp_path / "retained", run_id="engineering-run")
    assert not result["ok"]
    assert result["simulator_work"]["status"] == "unresolved"
    assert "simulator-work/launch-0.expected.json" in result["files"]


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("fault", [None, "missing_run", "interrupted", "unaudited"])
def test_common_work_aggregation_preserves_partial_coverage(arm, fault):
    """MON-12/CSE-4: session operation totals preserve provenance and missing-run uncertainty."""
    from aisle.harness.matched_evidence import common_envelope

    runs = []
    for index, quantity in enumerate((8, 10)):
        summary = {
            "completed_env_steps": 2,
            "completed_env_sim_ns": quantity,
            "recorded_step_work_exact": not (fault == "interrupted" and index == 1),
            "producer_coverage_complete": False,
        }
        runs.append(
            {
                "run_id": f"run-{index}",
                "attempt_id": f"attempt-{index}",
                "collection_path": f"run-{index}/run-evidence.json",
                "collection": {
                    "evaluator": {"status": "not_collected"},
                    "guards": {"status": "not_collected"},
                    "simulator_work": {"status": "recomputed", "summary": summary, "error": None},
                },
            }
        )
    if fault == "missing_run":
        runs[1]["collection"] = None
    record = {
        "session_id": "session",
        "plan_id": "plan",
        "arm": arm,
        "artifacts": {},
        "process": None,
        "lifecycle": {},
        "snapshots": {},
        "events": [],
        "postflight": None,
        "error": None,
        "tool_audit": {
            "ok": fault != "unaudited",
            "runs": runs,
            "attempted_tools": 2,
            "reserved_runs": 2,
            "reserved_episodes": 2,
            "executed_tools": 2,
            "wall_s": 1,
        },
    }
    envelope = common_envelope(record, None, None)
    work = envelope["simulator_operations"]
    assert envelope["complete"] is False
    assert envelope["budgets"]["observed"]["simulator_work"] is None
    assert work["producer_coverage_complete"] is False
    if fault == "unaudited":
        assert work["known_completed_env_sim_ns"] is None
        assert work["entries"] == []
    else:
        assert work["known_completed_env_sim_ns"] == (8 if fault == "missing_run" else 18)
        assert work["known_completed_env_steps"] == (2 if fault == "missing_run" else 4)
        assert work["entries"][0]["attempt_id"] == "attempt-0"
    assert work["recorded_step_work_exact"] is (fault is None)
    if fault == "missing_run":
        assert work["unresolved_runs"] == ["run-1"]


@pytest.mark.parametrize("missing", ["journal", "receipt"])
def test_relocated_incomplete_work_archive_recomputes_identically(tmp_path, missing):
    """MON-12/MON-13: archive relocation cannot alter the identity of missing-work evidence."""
    from aisle.harness.matched_evidence import _retained_simulator_work, retain_run
    from aisle.harness.simulator_work import reserve_launch, work_context

    source = _run(tmp_path)
    graph = source / "graph.yaml"
    graph.write_text("nodes: []\n")
    digest = hashlib.sha256(graph.read_bytes()).hexdigest()
    path = source / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["exec_graph_hashes"] = [digest]
    path.write_text(json.dumps(manifest))
    directory = source / "simulator-work"
    directory.mkdir()
    binding = reserve_launch(directory, run_id="engineering-run", launch=0, graph_sha256=digest)
    if missing == "receipt":
        with work_context(binding, dt_ns=4, n_envs=1) as work:
            work.call("step", lambda: None)
        (directory / "launch-0.expected.json").unlink()
    retained = tmp_path / "retained"
    result = retain_run(source, retained, run_id="engineering-run")
    moved = tmp_path / "moved"
    retained.rename(moved)
    recomputed = _retained_simulator_work(moved / "raw", result["manifest"])
    assert result["simulator_work"] == recomputed
    assert recomputed["summary"]["journals_complete"] is False
    assert recomputed["summary"]["recorded_step_work_exact"] is False
    assert recomputed["summary"]["launches"][0]["error"]
