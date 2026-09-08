"""MON-12/MON-13: postflight binds completed worker evidence to static staging."""

import json
from pathlib import Path

import pytest
from test_turn_node import Raw
from test_typed_graph_preflight import _stage

pytestmark = pytest.mark.unit


def _completed(tmp_path):
    from aisle.harness.typed_graph_stage import preflight_graph_stage
    from aisle.harness.typed_node_host import run_configured_node

    stage, record = _stage(tmp_path)
    preflight_graph_stage(stage, record)
    for binding in record["hosts"].values():
        result = run_configured_node(
            binding["config_path"], binding["config_sha256"], raw_node_factory=lambda: Raw([])
        )
        assert result["ok"], result
    return stage, record


def test_postflight_accepts_real_worker_receipts_and_hashes_all_artifacts(tmp_path):
    """MON-12/MON-13: completed real-child records remain auditable after worker dirs exist."""
    from aisle.harness.typed_graph_audit import audit_graph_stage

    stage, record = _completed(tmp_path)
    result = audit_graph_stage(stage, record)
    assert result["ok"], result
    assert set(result["workers"]) == set(record["hosts"])
    assert all(row["classification"] == "module_result" for row in result["workers"].values())
    assert any(name.endswith("rpc/worker.json") for name in result["files"])
    assert result["confirmatory_ready"] is False
    from aisle.harness.typed_graph_audit import retain_graph_stage

    collected = retain_graph_stage(stage, tmp_path / "retained", result)
    assert collected["ok"], collected
    assert collected["files"] == result["files"]
    for name in result["files"]:
        assert (tmp_path / "retained/raw" / name).read_bytes() == (stage / name).read_bytes()


@pytest.mark.parametrize(
    "mutation",
    [
        "host",
        "frame",
        "missing",
        "extra",
        "graph",
        "command",
        "quota",
        "boolean_sequence",
        "float_sequence",
        "float_bytes",
        "float_messages",
        "copied_worker_counter",
        "quota_type",
    ],
)
def test_postflight_rejects_drift_or_missing_evidence(tmp_path, mutation):
    """MON-13: missing, altered, or cross-bound evidence cannot support a valid run."""
    from aisle.harness.typed_graph_audit import audit_graph_stage

    stage, record = _completed(tmp_path)
    binding = next(iter(record["hosts"].values()))
    config = json.loads(Path(binding["config_path"]).read_text())
    output = Path(config["output"])
    if mutation == "host":
        path = output / "host.json"
        host = json.loads(path.read_text())
        host["host_config_sha256"] = "0" * 64
        path.write_text(json.dumps(host))
    elif mutation == "copied_worker_counter":
        path = output / "host.json"
        host = json.loads(path.read_text())
        host["result"]["worker"]["messages"] = float(host["result"]["worker"]["messages"])
        path.write_text(json.dumps(host))
    elif mutation in {"command", "quota", "quota_type"}:
        path = output / "launch.json"
        launch = json.loads(path.read_text())
        if mutation == "command":
            launch["wrapped_argv"] = ["unbound-interpreter"]
        elif mutation == "quota_type":
            launch["max_calls"] = float(launch["max_calls"])
        else:
            launch["max_calls"] += 1
        path.write_text(json.dumps(launch))
    elif mutation in {"boolean_sequence", "float_sequence", "float_bytes"}:
        path = output / "rpc/messages.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if mutation == "float_bytes":
            rows[0]["bytes"] = float(rows[0]["bytes"])
        else:
            rows[0]["sequence"] = True if mutation == "boolean_sequence" else 1.0
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    elif mutation == "float_messages":
        path = output / "rpc/worker.json"
        worker = json.loads(path.read_text())
        worker["messages"] = float(worker["messages"])
        path.write_text(json.dumps(worker))
    elif mutation == "frame":
        next((output / "rpc").glob("*.frame")).write_bytes(b"changed")
    elif mutation == "missing":
        (output / "host.json").unlink()
    elif mutation == "extra":
        (stage / "workers/undeclared").mkdir()
    else:
        path = stage / "graph.yaml"
        path.chmod(0o644)
        path.write_text("nodes: []")
    result = audit_graph_stage(stage, record)
    assert not result["ok"]
    assert result["errors"]
    assert result["files"]
