"""MON-13: launching simulation must not mutate the bound Python runtime."""

import sys

import pytest

pytestmark = pytest.mark.unit


def test_dora_children_do_not_create_runtime_bytecode(tmp_path):
    """MON-13: the Dora launch passes cache suppression to Python node children."""
    from aisle.harness.rollout import _spawn_dora

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "cache_probe.py").write_text("VALUE = 42\n")
    driver = runtime / "dora"
    driver.write_text(f"#!{sys.executable}\nimport cache_probe\nprint(cache_probe.VALUE)\n")
    driver.chmod(0o755)
    run = tmp_path / "run"
    run.mkdir()
    environment = {"PATH": str(runtime)}
    process = _spawn_dora(run / "graph.yaml", run, environment)
    assert process.wait(timeout=10) == 0
    assert (run / "dora.stdout.log").read_text().strip() == "42"
    assert not (runtime / "__pycache__").exists()
    assert environment == {"PATH": str(runtime)}
