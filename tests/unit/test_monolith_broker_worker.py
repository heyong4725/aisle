"""MON-3/MON-4/MON-12: production broker construction and callbacks through a worker."""

import json

import pytest
from test_monolith_worker_launch import _launch_inputs

pytestmark = pytest.mark.unit


def _factory(tmp_path):
    from aisle.monolith.worker_launch import launch_worker

    inputs = _launch_inputs(tmp_path)

    def factory(primitives):
        return launch_worker(**{**inputs, "primitives": primitives})

    return factory, inputs["output"]


def test_broker_uses_worker_without_local_module_execution(tmp_path, monkeypatch):
    """MON-4/MON-12: Broker preserves its trusted feedback route with child-only authored code."""
    from aisle.nodes.monolith_broker import Broker

    factory, output = _factory(tmp_path)
    module = tmp_path / "controller.py"
    module.write_text(
        "API_VERSION='1.0'\nclass Controller:\n"
        " def __init__(self,p,log): pass\n def on_event(self,e): return []\n"
    )

    def forbidden(*args):
        pytest.fail("authored source executed in the parent broker")

    monkeypatch.setattr("aisle.nodes.monolith_broker.load_module", forbidden)
    with Broker(module, "franka", worker_factory=factory) as broker:
        assert broker.record["execution"] == "worker"
        assert broker.deliver("episode_goal", {"target_med": "fixture"}, 0, "goal") == []
        assert broker.deliver("tick", 1, 1_000_000_000) == [
            {"feedback": {"t": 0, "phase": "unknown"}}
        ]
    assert json.loads((output / "rpc/worker.json").read_text())["state"] == "closed"


def test_check_module_uses_worker_and_closes_it(tmp_path):
    """MON-3/MON-12: the supported check function executes the constructor in the child."""
    from aisle.harness.monolith import check_module

    factory, output = _factory(tmp_path)
    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n")
    report = check_module(module, worker_factory=factory)
    assert report["ok"] is True and report["execution"] == "worker"
    assert json.loads((output / "rpc/worker.json").read_text())["state"] == "closed"


def test_worker_syntax_failure_remains_module_error_in_check(tmp_path):
    """MON-3/MON-12: moving execution into a child preserves ordinary Python error meaning."""
    from aisle.harness.monolith import check_module

    factory, output = _factory(tmp_path)
    module = tmp_path / "invalid.py"
    module.write_text("invalid syntax :::")
    report = check_module(module, worker_factory=factory)
    assert report["ok"] is False
    assert "SyntaxError" in report["error"]
    assert not report.get("infrastructure_invalid", False)
    assert json.loads((output / "rpc/worker.json").read_text())["state"] == "failed"


def test_check_reports_worker_profile_drift_as_infrastructure_invalid(tmp_path):
    """MON-8/MON-13: adapter admission failures are not attributed to authored Python."""
    from aisle.harness.monolith import check_module

    factory, output = _factory(tmp_path)
    (tmp_path / "worker.sb").write_text("(allow default)")
    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n")
    report = check_module(module, worker_factory=factory)
    assert report["ok"] is False and report["infrastructure_invalid"] is True
    assert not (output / "rpc").exists()
