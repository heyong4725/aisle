"""MON-6/MON-12/MON-13: validation uses bound code and candidate data."""

import json
import subprocess
import sys

import pytest
from test_typed_validation_snapshot import ROOT, _view

pytestmark = pytest.mark.unit


def test_bundle_runs_real_cli_without_importing_candidate(tmp_path):
    """MON-2/MON-6: typed node implementations are validated as data in a separate process."""
    from aisle.harness.typed_snapshot import build_typed_validation_snapshot
    from aisle.harness.typed_validation import build_validation_bundle, validation_command

    view = _view(tmp_path)
    (view / "src/aisle/nodes/segmented_pose.py").write_text("raise AssertionError('authored')")
    snapshot = tmp_path / "snapshot"
    record = build_typed_validation_snapshot(ROOT, view, snapshot)
    bundle = tmp_path / "bundle"
    manifest = build_validation_bundle(bundle)
    command = validation_command(sys.executable, bundle, manifest, snapshot, record, "franka")
    result = subprocess.run(command, cwd=bundle, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True
    assert not (bundle / "aisle/nodes").exists()
    (snapshot / "registry/manifests/segmented-pose.yaml").chmod(0o644)
    (snapshot / "registry/manifests/segmented-pose.yaml").write_text("broken: declaration")
    from aisle.harness.typed_snapshot import SnapshotError

    with pytest.raises(SnapshotError):
        validation_command(sys.executable, bundle, manifest, snapshot, record, "franka")


@pytest.mark.parametrize("change", ["extra", "mode", "bytes", "symlink"])
def test_bundle_refuses_changed_inventory(tmp_path, change):
    """MON-13: a bundle receipt cannot authorize changed validator code or helpers."""
    from aisle.harness.typed_validation import (
        ValidationError,
        build_validation_bundle,
        verify_validation_bundle,
    )

    bundle = tmp_path / "bundle"
    manifest = build_validation_bundle(bundle)
    target = bundle / "aisle/harness/validate.py"
    if change == "extra":
        (bundle / "extra.py").write_text("pass")
    elif change == "mode":
        target.chmod(0o644)
    elif change == "bytes":
        target.chmod(0o644)
        target.write_text("pass")
        target.chmod(0o444)
    else:
        target.unlink()
        target.symlink_to(ROOT / "src/aisle/harness/validate.py")
    with pytest.raises(ValidationError):
        verify_validation_bundle(bundle, manifest)


def test_bundle_preserves_normal_cli_validation_errors(tmp_path):
    """MON-2/CON-8: invalid authored manifests retain normal JSON and stderr diagnostics."""
    from aisle.harness.typed_snapshot import build_typed_validation_snapshot
    from aisle.harness.typed_validation import build_validation_bundle, validation_command

    view = _view(tmp_path)
    (view / "registry/manifests/segmented-pose.yaml").write_text("broken: declaration")
    snapshot = tmp_path / "snapshot"
    record = build_typed_validation_snapshot(ROOT, view, snapshot)
    bundle = tmp_path / "bundle"
    manifest = build_validation_bundle(bundle)
    result = subprocess.run(
        validation_command(sys.executable, bundle, manifest, snapshot, record, "franka"),
        cwd=bundle,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert report["errors"]
    assert "validate error:" in result.stderr


def test_bundle_refuses_changed_controller_source(tmp_path, monkeypatch):
    """MON-13: a matching old bundle and receipt cannot mask controller source drift."""
    import shutil

    from aisle.harness import typed_validation

    bundle = tmp_path / "bundle"
    manifest = typed_validation.build_validation_bundle(bundle)
    sources = tmp_path / "sources"
    shutil.copytree(bundle, sources)
    target = sources / "aisle/harness/validate.py"
    target.chmod(0o644)
    target.write_text(target.read_text() + "\n# changed validator\n")
    monkeypatch.setattr(typed_validation, "_SOURCE_ROOT", sources)
    with pytest.raises(typed_validation.ValidationError, match="controller sources"):
        typed_validation.verify_validation_bundle(bundle, manifest)


def test_bound_virtualenv_runs_real_validator(tmp_path):
    """MON-6/MON-13: bound venv startup retains validator dependencies without base-site
    fallback.
    """
    from pathlib import Path

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.typed_snapshot import build_typed_validation_snapshot
    from aisle.harness.typed_validation import build_validation_bundle, validation_command

    runtime = capture_runtime(sorted({Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}))
    view = _view(tmp_path)
    snapshot = tmp_path / "snapshot"
    record = build_typed_validation_snapshot(ROOT, view, snapshot)
    bundle = tmp_path / "bundle"
    manifest = build_validation_bundle(bundle)
    command = validation_command(
        sys.executable, bundle, manifest, snapshot, record, "franka", runtime_record=runtime
    )
    result = subprocess.run(command, cwd=bundle, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True


@pytest.mark.parametrize("case", ["bound", "alias", "prefix", "changed"])
def test_validator_binds_startup_configuration(tmp_path, case):
    """MON-13: executable hashes cannot authorize an unbound or changed pyvenv.cfg."""
    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.typed_snapshot import build_typed_validation_snapshot
    from aisle.harness.typed_validation import build_validation_bundle, validation_command

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
    view = _view(tmp_path)
    snapshot = tmp_path / "snapshot"
    record = build_typed_validation_snapshot(ROOT, view, snapshot)
    bundle = tmp_path / "bundle"
    manifest = build_validation_bundle(bundle)
    if case == "changed":
        config.write_text(config.read_text().replace("false", "true"))
    if case == "bound":
        command = validation_command(
            invocation, bundle, manifest, snapshot, record, "franka", runtime_record=runtime
        )
        assert command[0] == str(invocation)
    else:
        with pytest.raises(ValueError, match="runtime|startup"):
            validation_command(
                invocation, bundle, manifest, snapshot, record, "franka", runtime_record=runtime
            )
