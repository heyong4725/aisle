"""MON-13: interpreter identity includes its startup configuration paths."""

import hashlib

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("case", ["bound", "alias", "prefix", "changed"])
def test_controller_binds_virtualenv_invocation_and_configuration(tmp_path, case):
    """MON-13: a bound binary cannot authorize an unbound virtual environment."""
    from aisle.harness.matched_run_launch import verify_controller_binding
    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.treatment_ambient import build_declared_environment

    base, venv = tmp_path / "base", tmp_path / "venv"
    for root in (base, venv):
        (root / "bin").mkdir(parents=True)
    executable = base / "bin/python"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    invocation = venv / "bin/python"
    invocation.symlink_to(executable)
    config = venv / "pyvenv.cfg"
    config.write_text(f"home = {base / 'bin'}\ninclude-system-site-packages = false\n")
    roots = [base] if case == "alias" else [base, venv / "bin" if case == "prefix" else venv]
    runtime = capture_runtime(roots)
    environment, record = build_declared_environment(tmp_path / "home", source_env={})
    binding = {
        "schema_version": "aisle.matched-run-controller.v1",
        "python": str(invocation),
        "python_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "environment": environment,
        "environment_record": record,
    }
    if case == "changed":
        config.write_text(config.read_text().replace("false", "true"))
    if case == "bound":
        assert verify_controller_binding(binding, runtime, [], []) == binding
    else:
        with pytest.raises(ValueError, match="runtime|startup"):
            verify_controller_binding(binding, runtime, [], [])
