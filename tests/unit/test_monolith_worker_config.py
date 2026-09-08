"""MON-8/MON-12/MON-13: hash-bound worker selection through public launch interfaces."""

import hashlib
import json
import subprocess
import sys

import pytest
from test_monolith_worker_launch import _launch_inputs

pytestmark = pytest.mark.unit


def _config(tmp_path, module):
    inputs = _launch_inputs(tmp_path)
    launch = {key: value for key, value in inputs.items() if key not in {"primitives", "output"}}
    launch["policy"] = inputs["policy"].canonical_dict()
    for name in ("bundle", "profile_path", "python"):
        launch[name] = str(launch[name])
    launch.update(max_primitive_calls=1000, max_handles=100)
    record = {
        "schema_version": "aisle.monolith.worker-config.v1",
        "purpose": "expert_parity",
        "embodiment": "franka",
        "module_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
        "output_root": str(inputs["output"]),
        "launch": launch,
    }
    path = tmp_path / "worker-config.json"
    path.write_text(json.dumps(record))
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), inputs["output"]


def test_cli_check_selects_bound_worker(tmp_path):
    """MON-8/MON-12: the CLI constructs code in a worker and retains its attempt."""
    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n")
    config, digest, output = _config(tmp_path, module)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.cli",
            "monolith",
            "check",
            "--module",
            str(module),
            "--worker-config",
            str(config),
            "--worker-config-sha256",
            digest,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0 and report["ok"] is True, report
    assert report["execution"] == "worker"
    assert json.loads((output / "check/rpc/worker.json").read_text())["state"] == "closed"


@pytest.mark.parametrize("change", ["config", "module"])
def test_bound_config_and_source_drift_are_refused(tmp_path, change):
    """MON-8/MON-13: neither runtime configuration nor authored source can change after binding."""
    from aisle.harness.monolith import check_module
    from aisle.monolith.supervisor import WorkerFailure
    from aisle.monolith.worker_config import configured_worker_factory

    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n")
    config, digest, _ = _config(tmp_path, module)
    if change == "config":
        config.write_text(config.read_text() + " ")
        with pytest.raises(WorkerFailure, match="hash"):
            configured_worker_factory(config, digest, phase="check")
    else:
        module.write_text("raise AssertionError('changed code must never execute')")
        report = check_module(
            module, worker_factory=configured_worker_factory(config, digest, phase="check")
        )
        assert report["ok"] is False and report["infrastructure_invalid"] is True
        assert "source" in report["error"]


def test_partial_worker_binding_never_selects_legacy_execution():
    """MON-8: incomplete worker selection is refused, never silently downgraded."""
    from aisle.monolith.supervisor import WorkerFailure
    from aisle.monolith.worker_config import configured_worker_factory

    with pytest.raises(WorkerFailure):
        configured_worker_factory(None, "0" * 64, phase="run")
    with pytest.raises(WorkerFailure):
        configured_worker_factory("missing.json", None, phase="check")
    assert configured_worker_factory(None, None, phase="check") is None


def test_stamped_graph_binds_worker_configuration_to_broker_only(tmp_path):
    """MON-8/MON-13: the runtime graph carries the exact configuration hash to its broker."""
    from pathlib import Path

    import yaml

    from aisle.harness.monolith import stamp_graph

    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n")
    config, digest, _ = _config(tmp_path, module)
    graph = stamp_graph(
        Path(__file__).resolve().parents[2],
        module,
        tmp_path / "graphs",
        worker_config=config,
        worker_config_sha256=digest,
    )
    nodes = yaml.safe_load(graph.read_text())["nodes"]
    selected = [node for node in nodes if "AISLE_MONOLITH_WORKER_CONFIG" in node.get("env", {})]
    assert len(selected) == 1
    assert selected[0]["env"]["AISLE_MONOLITH_MODULE"] == str(module)
    assert selected[0]["env"]["AISLE_MONOLITH_WORKER_CONFIG"] == str(config)
    assert selected[0]["env"]["AISLE_MONOLITH_WORKER_CONFIG_SHA256"] == digest


def test_dora_broker_construction_selects_configured_worker(tmp_path):
    """MON-4/MON-12: the broker constructor used by the Dora entry point selects the run phase."""
    from aisle.nodes.monolith_broker import broker_from_environment

    module = tmp_path / "controller.py"
    module.write_text(
        "API_VERSION='1.0'\nclass Controller:\n"
        " def __init__(self,p,log): pass\n def on_event(self,e): return []\n"
    )
    config, digest, output = _config(tmp_path, module)
    with broker_from_environment(
        {
            "AISLE_MONOLITH_MODULE": str(module),
            "AISLE_MONOLITH_WORKER_CONFIG": str(config),
            "AISLE_MONOLITH_WORKER_CONFIG_SHA256": digest,
        }
    ) as broker:
        assert broker.record["execution"] == "worker"
        assert broker.deliver("reset_done", None, 0) == []
    assert json.loads((output / "run/rpc/worker.json").read_text())["state"] == "closed"


def test_cli_partial_worker_configuration_returns_json_refusal(tmp_path):
    """CON-8/MON-8: missing worker identity stays a structured infrastructure refusal."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.cli",
            "monolith",
            "check",
            "--module",
            str(tmp_path / "unused.py"),
            "--worker-config",
            str(tmp_path / "missing.json"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 1 and report["ok"] is False
    assert report["infrastructure_invalid"] is True
