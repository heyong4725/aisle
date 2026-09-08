"""MON-6/MON-12/MON-13: separately bound validation process and retained failures."""

import importlib
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_monolith_worker_launch import _launch_inputs
from test_treatment_confinement import _attestation
from test_typed_validation_snapshot import ROOT, _view

pytestmark = pytest.mark.unit


def _inputs(tmp_path, *, invalid=False):
    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.treatment_confinement import compile_macos_profile
    from aisle.harness.typed_snapshot import build_typed_validation_snapshot
    from aisle.harness.typed_validation import build_validation_bundle

    inputs = _launch_inputs(tmp_path)
    # These tests exercise validation and drift, not a five-second speed target.
    # Timeout behavior is injected explicitly by the interruption fixture.
    inputs["timeout_s"] = 30
    inputs.pop("primitives")
    inputs.pop("source_roots")
    view = _view(tmp_path)
    if invalid:
        (view / "registry/manifests/segmented-pose.yaml").write_text("broken: declaration")
    snapshot = tmp_path / "snapshot"
    inputs["snapshot_record"] = build_typed_validation_snapshot(ROOT, view, snapshot)
    inputs["snapshot"] = snapshot
    inputs["bundle"] = tmp_path / "validator"
    inputs["bundle_manifest"] = build_validation_bundle(inputs["bundle"])
    inputs["embodiment"] = "franka"
    package_root = tmp_path / "runtime-packages"
    package_root.mkdir()
    (package_root / "package.py").write_text("VALUE = 1")
    for name in (
        "yaml",
        "jsonschema",
        "jsonschema_specifications",
        "referencing",
        "rpds",
        "attrs",
        "attr",
        "typing_extensions",
    ):
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if name == "typing_extensions" and exc.name == name and sys.version_info >= (3, 13):
                continue
            raise
        source = Path(module.__file__)
        if hasattr(module, "__path__"):
            shutil.copytree(
                source.parent,
                package_root / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        else:
            shutil.copyfile(source, package_root / source.name)
    inputs["policy"] = replace(
        inputs["policy"],
        runtime_read_roots=(*inputs["policy"].runtime_read_roots, package_root),
        visible_roots=(inputs["bundle"], snapshot),
        hidden_roots=(*inputs["policy"].hidden_roots, ROOT, view),
    )
    inputs["runtime_record"] = capture_runtime(inputs["policy"].runtime_read_roots)
    compiled = compile_macos_profile(inputs["policy"])
    inputs["profile_path"].write_text(compiled.text)
    inputs["attestation"] = _attestation(
        compiled, inputs["profile_path"], tmp_path / "synthetic-adapter"
    )
    return inputs


@pytest.mark.parametrize("invalid", [False, True])
def test_validation_launch_retains_real_cli_result(tmp_path, invalid):
    """MON-12: synthetic adapter proves launch wiring and diagnostics, not OS enforcement."""
    from aisle.harness.typed_validation import run_validation

    inputs = _inputs(tmp_path, invalid=invalid)
    result = run_validation(**inputs)
    assert result["classification"] == "tool_result", result
    assert result["result"]["ok"] is not invalid
    assert result["process"]["rc"] == int(invalid)
    assert json.loads((inputs["output"] / "result.json").read_text()) == result
    assert json.loads((inputs["output"] / "stdout.json").read_text()) == result["result"]
    assert (inputs["output"] / "stderr.log").exists()
    assert result["confirmatory_ready"] is False


@pytest.mark.parametrize("drift", ["profile", "interpreter", "snapshot", "writable", "runtime"])
def test_validation_refuses_stale_or_writable_inputs_before_spawn(tmp_path, monkeypatch, drift):
    """MON-6/MON-13: validator launch does not widen stale or writable authority."""
    from aisle.harness import typed_validation

    inputs = _inputs(tmp_path)
    if drift == "profile":
        inputs["profile_path"].write_text("(allow default)")
    elif drift == "interpreter":
        inputs["python_sha256"] = "0" * 64
    elif drift == "runtime":
        (tmp_path / "runtime-packages/package.py").write_text("VALUE = 2")
    elif drift == "snapshot":
        (inputs["snapshot"] / "extra.py").write_text("pass")
    else:
        inputs["policy"] = replace(
            inputs["policy"],
            output_roots=(*inputs["policy"].output_roots, inputs["snapshot"]),
        )
    calls = []
    monkeypatch.setattr(
        typed_validation, "spawn_isolated_process", lambda *a, **kw: calls.append(a)
    )
    result = typed_validation.run_validation(**inputs)
    assert not calls
    assert result["classification"] == "infrastructure_exclusion"
    assert result["error"]
    assert json.loads((inputs["output"] / "result.json").read_text()) == result


@pytest.mark.parametrize(
    "failure", ["timeout", "cancel", "snapshot_drift", "interpreter_drift", "runtime_drift"]
)
def test_validation_stops_child_and_retains_interrupted_or_changed_attempt(
    tmp_path, monkeypatch, failure
):
    """MON-12/MON-13: wait failures reap the child and postflight rejects changed data."""
    import subprocess

    from aisle.harness import typed_validation

    inputs = _inputs(tmp_path)
    spawn = typed_validation.spawn_isolated_process
    children = []

    class ObservedProcess:
        def __init__(self, child):
            self.child = child
            self.calls = 0

        def __getattr__(self, name):
            return getattr(self.child, name)

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                if failure == "timeout":
                    raise subprocess.TimeoutExpired("validation-fixture", timeout)
                if failure == "cancel":
                    raise KeyboardInterrupt("validation fixture cancellation")
            rc = self.child.wait(timeout=timeout)
            if failure == "snapshot_drift":
                (inputs["snapshot"] / "unexpected.txt").write_text("changed during process")
            if failure == "runtime_drift":
                (tmp_path / "runtime-packages/package.py").write_text("VALUE = 2")
            if failure == "interpreter_drift":
                from pathlib import Path

                read = Path.read_bytes
                monkeypatch.setattr(
                    Path,
                    "read_bytes",
                    lambda path: (
                        read(path) + b"changed" if path == inputs["python"] else read(path)
                    ),
                )
            return rc

    def observed_spawn(*args, **kwargs):
        child = spawn(*args, **kwargs)
        children.append(child)
        return ObservedProcess(child)

    monkeypatch.setattr(typed_validation, "spawn_isolated_process", observed_spawn)
    if failure == "cancel":
        with pytest.raises(KeyboardInterrupt, match="fixture cancellation"):
            typed_validation.run_validation(**inputs)
    else:
        typed_validation.run_validation(**inputs)
    assert len(children) == 1, json.loads((inputs["output"] / "result.json").read_text())
    assert children[0].poll() is not None
    result = json.loads((inputs["output"] / "result.json").read_text())
    assert result["classification"] == "infrastructure_exclusion"
    assert result["process"]["timed_out"] is (failure == "timeout")
    assert result["error"]
    assert (inputs["output"] / "stdout.json").exists()
    assert (inputs["output"] / "stderr.log").exists()


@pytest.mark.parametrize("roots", [(), ("relative/source",)])
def test_validation_launch_requires_canonical_source_bindings(tmp_path, roots):
    """MON-6/MON-13: source protection requires explicit canonical roots."""
    from aisle.harness.typed_validation import ValidationError, verify_validation_launch

    inputs = _inputs(tmp_path)
    kwargs = {
        key: inputs[key]
        for key in (
            "bundle",
            "bundle_manifest",
            "policy",
            "python",
            "python_sha256",
            "runtime_record",
            "environment",
            "environment_record",
        )
    }
    with pytest.raises(ValidationError, match="source"):
        verify_validation_launch(**kwargs, snapshot_storage=inputs["snapshot"], source_roots=roots)


def test_validation_runtime_supports_python313_without_typing_extensions(tmp_path, monkeypatch):
    """CON-1/MON-12: the default Python 3.13 validator works without optional backports."""

    if sys.version_info < (3, 13):
        pytest.skip("typing backport remains required before Python 3.13")
    from aisle.harness.typed_validation import run_validation

    original = importlib.import_module

    def without_backport(name, *args, **kwargs):
        if name == "typing_extensions":
            raise ModuleNotFoundError("optional backport absent", name=name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", without_backport)
    inputs = _inputs(tmp_path)
    assert not (tmp_path / "runtime-packages/typing_extensions.py").exists()
    result = run_validation(**inputs)
    assert result["ok"], result


def test_validator_fixture_allows_setup_before_process_assertions(tmp_path, monkeypatch):
    """MON-12: validator functional checks must reach their child after ordinary setup work."""
    from types import SimpleNamespace

    from aisle.harness import typed_validation

    inputs = _inputs(tmp_path)
    ticks = iter([0.0, 6.0, 6.0])
    monkeypatch.setattr(typed_validation, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    result = typed_validation.run_validation(**inputs)
    assert result["ok"], result
    assert result["process"]["timed_out"] is False
