"""CON-5/BRG-1: wall ticks cannot truncate the reset comparison window."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "acknowledged,progress_ns,expected",
    [(False, 2_000_000_000, 1), (True, 30_000_000, 1), (True, 1_200_000_000, 2)],
)
def test_next_reset_requires_acknowledged_simulation_window(
    monkeypatch, acknowledged, progress_ns, expected
):
    """CON-5: advance wall time without inventing simulation coverage."""
    source = Path(__file__).resolve().parents[1] / "fixtures" / "nodes" / "driver.py"
    monkeypatch.setitem(sys.modules, "dora", SimpleNamespace(Node=None))
    spec = importlib.util.spec_from_file_location("reset_driver_under_test", source)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    for name, value in {
        "DRIVER_MODE": "reset",
        "DRIVER_RESET_SEEDS": "7,11",
        "DRIVER_RESET_SPACING": "2",
        "DRIVER_WAIT_BRIDGE_INFO": "1",
        "DRIVER_RESET_MIN_SIM_NS": "1200000000",
    }.items():
        monkeypatch.setenv(name, value)

    def event(name, **metadata):
        return {"type": "INPUT", "id": name, "metadata": metadata}

    events = [event("bridge_info"), event("tick"), event("tick")]
    if acknowledged:
        events.append(event("reset_done", request_id="req-1-7", sim_time_ns=0))
    events.append(event("joint_state", sim_time_ns=progress_ns))
    events.extend(event("tick") for _ in range(20))
    sent = []

    class FakeNode:
        def __iter__(self):
            return iter(events)

        def send_output(self, name, value, metadata):
            sent.append((name, value.to_pylist(), metadata))

    monkeypatch.setattr(driver, "Node", FakeNode)
    driver.main()
    assert len(sent) == expected
    assert sent[0][1] == [7, 0]
    if expected == 2:
        assert sent[1][1] == [11, 0]


def test_writer_binds_reset_acknowledgment_and_progress(dataflow, tmp_path):
    """CON-5: the driver gate cannot be configured without its real inputs."""
    writer = dataflow.write
    graph = writer(
        tmp_path,
        tmp_path / "records.jsonl",
        driver_env={"DRIVER_MODE": "reset"},
        driver_reset_min_sim_ns=1_200_000_000,
    )
    driver = next(n for n in yaml.safe_load(graph.read_text())["nodes"] if n["id"] == "driver")
    assert driver["inputs"]["reset_done"] == {"source": "bridge/reset_done", "queue_size": 100}
    assert driver["inputs"]["joint_state"] == "bridge/joint_state"
    assert driver["env"]["DRIVER_RESET_MIN_SIM_NS"] == "1200000000"
    with pytest.raises(ValueError, match="inputs are wired"):
        writer(
            tmp_path,
            tmp_path / "records.jsonl",
            driver_env={"DRIVER_MODE": "reset", "DRIVER_RESET_MIN_SIM_NS": "1200000000"},
        )


@pytest.mark.parametrize("window", [True, 0, -1, 1.2, "1200000000"])
def test_writer_rejects_invalid_progress_window(dataflow, tmp_path, window):
    """CON-5: invalid window configuration fails before launching a graph."""
    writer = dataflow.write
    with pytest.raises(ValueError, match="positive integer and reset mode"):
        writer(
            tmp_path,
            tmp_path / "records.jsonl",
            driver_env={"DRIVER_MODE": "reset"},
            driver_reset_min_sim_ns=window,
        )
