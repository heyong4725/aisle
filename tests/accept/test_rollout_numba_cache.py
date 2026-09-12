"""MON-13: trusted simulator dependencies keep Numba state outside runtime."""

import sys

import pytest

pytestmark = pytest.mark.accept


def test_dora_numba_compilation_keeps_runtime_unchanged(tmp_path):
    """MON-13: actual Numba cache creation belongs to the private run directory."""
    pytest.importorskip("numba")
    from aisle.harness.rollout import _spawn_dora

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    module = runtime / "cache_probe.py"
    module.write_text("from numba import njit\n@njit(cache=True)\ndef value(x): return x + 1\n")
    driver = runtime / "dora"
    driver.write_text(f"#!{sys.executable}\nimport cache_probe\nprint(cache_probe.value(41))\n")
    driver.chmod(0o755)
    before = {p.name: p.read_bytes() for p in runtime.iterdir()}
    run = tmp_path / "run"
    run.mkdir()
    process = _spawn_dora(run / "graph.yaml", run, {"PATH": str(runtime)})
    assert process.wait(timeout=30) == 0, (run / "dora.stderr.log").read_text()
    assert (run / "dora.stdout.log").read_text().strip() == "42"
    assert set(p.name for p in runtime.iterdir()) == set(before)
    assert all((runtime / name).read_bytes() == data for name, data in before.items())
    assert list((run / ".cache/numba").rglob("*.nbc"))
