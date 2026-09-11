"""MON-13: verify runtime contents once on each side of the child execution boundary."""

import pytest
from test_matched_run_launch import _inputs

pytestmark = pytest.mark.unit


def test_runtime_verification_brackets_actual_child_without_duplicate_scans(tmp_path, monkeypatch):
    """MON-13: fresh before/after inventories remain mandatory; adjacent duplicate scans do not."""
    from aisle.harness import matched_run_launch, matched_runtime

    inputs = _inputs(tmp_path)
    events = []
    original_capture = matched_runtime.capture_runtime
    original_spawn = matched_run_launch.spawn_isolated_process

    def capture(roots):
        events.append("verify")
        return original_capture(roots)

    def spawn(*args, **kwargs):
        events.append("spawn")
        return original_spawn(*args, **kwargs)

    monkeypatch.setattr(matched_runtime, "capture_runtime", capture)
    monkeypatch.setattr(matched_run_launch, "spawn_isolated_process", spawn)
    result = matched_run_launch.launch_configured_run(**inputs)
    assert result["process"]["rc"] == 1
    assert result["error"] is None
    assert events == ["verify", "spawn", "verify"]
