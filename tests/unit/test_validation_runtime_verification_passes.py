"""MON-13: validator runtime inventories bracket execution without repeated scans."""

import pytest
from test_typed_validation_launch import _inputs

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("drift", [None, "before_spawn", "after_child"])
def test_validation_inventories_at_execution_boundaries(tmp_path, monkeypatch, drift):
    """MON-13: preparation and child drift remain refused with one scan per boundary."""
    from aisle.harness import matched_runtime, typed_validation

    inputs = _inputs(tmp_path)
    events = []
    capture = matched_runtime.capture_runtime
    spawn = typed_validation.spawn_isolated_process
    write_json = typed_validation._json
    dependency = tmp_path / "runtime-packages/package.py"

    def changed_runtime():
        dependency.write_text("VALUE = 2\n")

    def observed_capture(roots):
        events.append("scan")
        return capture(roots)

    def observed_write(path, value):
        write_json(path, value)
        if drift == "before_spawn" and path.name == "capability.json":
            changed_runtime()

    class ObservedProcess:
        def __init__(self, child):
            self.child = child

        def __getattr__(self, name):
            return getattr(self.child, name)

        def wait(self, timeout=None):
            result = self.child.wait(timeout=timeout)
            if drift == "after_child":
                changed_runtime()
            return result

    def observed_spawn(*args, **kwargs):
        events.append("spawn")
        return ObservedProcess(spawn(*args, **kwargs))

    monkeypatch.setattr(matched_runtime, "capture_runtime", observed_capture)
    monkeypatch.setattr(typed_validation, "_json", observed_write)
    monkeypatch.setattr(typed_validation, "spawn_isolated_process", observed_spawn)
    result = typed_validation.run_validation(**inputs)
    if drift is None:
        assert result["ok"] is True, result
        assert result["process"] == {"rc": 0, "timed_out": False}
    else:
        assert result["ok"] is False
        assert "runtime inventory has drifted" in result["error"]
    assert events == (["scan"] if drift == "before_spawn" else ["scan", "spawn", "scan"])
