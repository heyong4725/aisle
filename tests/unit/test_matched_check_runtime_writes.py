"""MON-13: arm checks must not populate the admitted runtime with bytecode."""

import json

import pytest
from test_matched_tools import _controller

from aisle.harness.matched_runtime import capture_runtime, verify_runtime
from aisle.harness.treatment_ambient import spawn_isolated_process

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_check_interpreter_import_preserves_runtime_inventory(tmp_path, monkeypatch, arm):
    """MON-13: real check-interpreter startup imports cannot write runtime caches."""
    controller, _, _ = _controller(tmp_path, arm)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    dependency = runtime / "dependency.py"
    dependency.write_text("VALUE = 42\n")
    receipt = capture_runtime([runtime])
    probe = (
        "import importlib.util, json; "
        f"spec = importlib.util.spec_from_file_location('dependency', {str(dependency)!r}); "
        "module = importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(module); "
        "print(json.dumps({'ok': module.VALUE == 42}))"
    )

    def spawn(argv, **kwargs):
        # Keep the actual adapter, interpreter, flags and declared environment.
        # A tiny real import replaces the CLI payload to avoid touching installed
        # packages or relying on whether their bytecode caches already exist.
        command = [*argv[: argv.index("-m")], "-c", probe]
        return spawn_isolated_process(command, **kwargs)

    monkeypatch.setattr("aisle.harness.matched_tools.spawn_isolated_process", spawn)
    attempt = controller.check()
    assert attempt["ok"] is True, json.dumps(attempt)
    verify_runtime(receipt)
