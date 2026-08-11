"""Unit contracts for the shared graph-test fixture."""

import importlib.util
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

RECORDER_PATH = Path(__file__).parents[1] / "fixtures" / "nodes" / "recorder.py"
SPEC = importlib.util.spec_from_file_location("fixture_recorder", RECORDER_PATH)
assert SPEC is not None and SPEC.loader is not None
RECORDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECORDER)


def test_recorder_tail_can_be_anchored_to_observed_evidence(tmp_path, dataflow):
    """CON-5/BRG-1 (issue #94): a capture tail can start only after the
    requested stream evidence arrives, instead of racing a nominal driver
    schedule under suite load."""
    graph = dataflow.write(
        tmp_path,
        tmp_path / "records.jsonl",
        duration_s=8.0,
        recorder_wait_for=("reset_done", 2),
    )

    document = yaml.safe_load(graph.read_text())
    recorder = next(node for node in document["nodes"] if node["id"] == "recorder")
    assert recorder["env"]["RECORDER_WAIT_FOR_ID"] == "reset_done"
    assert recorder["env"]["RECORDER_WAIT_FOR_COUNT"] == "2"
    assert recorder["env"]["RECORDER_DURATION_S"] == "8.0"


def test_capture_tail_starts_after_required_evidence():
    """CON-5/BRG-1 (issue #94): nominal elapsed time cannot end capture
    before reset #2."""
    window = RECORDER.CaptureWindow(8.0, "reset_done", 2)

    window.observe("bridge_info", 10.0)
    window.observe("reset_done", 20.0)
    assert window.deadline is None
    assert not window.complete(200.0)

    window.observe("reset_done", 205.0)
    assert window.deadline == 213.0
    assert not window.complete(213.0)
    assert window.complete(213.001)


def test_capture_tail_without_evidence_anchor_keeps_first_event_semantics():
    """CON-5/BRG-1: existing recorder users remain first-event anchored."""
    window = RECORDER.CaptureWindow(8.0, None, 0)

    window.observe("bridge_info", 10.0)
    assert window.deadline == 18.0
    assert not window.complete(18.0)
    assert window.complete(18.001)
