"""MON-2/MON-6/MON-12: typed validation sees an isolated complete candidate snapshot."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def _view(tmp_path):
    view = tmp_path / "typed"
    view.mkdir()
    editable = json.loads((ROOT / "docs/monolithic/allowlist.json").read_text())["typed"][
        "editable"
    ]
    for name in editable:
        target = view / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    return view


def test_snapshot_validates_full_candidate_without_importing_authored_nodes(tmp_path):
    """MON-2/MON-6: authored nodes are data during validation; trusted originals remain intact."""
    from aisle.harness.typed_snapshot import build_typed_validation_snapshot
    from aisle.harness.validate import validate

    view = _view(tmp_path)
    name = "src/aisle/nodes/segmented_pose.py"
    original = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    (view / name).write_text("raise AssertionError('candidate must not execute in validator')\n")
    output = tmp_path / "snapshot"
    record = build_typed_validation_snapshot(ROOT, view, output)
    report = validate(output / "graphs/expert_t1.yaml", output, "franka", False)
    assert report["ok"] is True, report
    assert (output / name).read_bytes() == (view / name).read_bytes()
    assert record["files"][name]["origin"] == "participant"
    assert record["files"]["src/aisle/nodes/budget_guard.py"]["origin"] == "controller"
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == original
    assert not (output / ".git").exists()
    assert not (output / "runs").exists()


def test_snapshot_preserves_authored_manifest_errors_for_validator(tmp_path):
    """MON-2: the controller does not repair malformed authored manifests."""
    from aisle.harness.typed_snapshot import build_typed_validation_snapshot
    from aisle.harness.validate import validate

    view = _view(tmp_path)
    manifest = "registry/manifests/segmented-pose.yaml"
    (view / manifest).write_text("this: is not a capability manifest\n")
    output = tmp_path / "snapshot"
    build_typed_validation_snapshot(ROOT, view, output)
    assert (output / manifest).read_bytes() == (view / manifest).read_bytes()
    assert validate(output / "graphs/expert_t1.yaml", output, "franka", False)["ok"] is False


def test_snapshot_refuses_redirected_authored_input(tmp_path):
    """MON-6/MON-13: a candidate symlink cannot substitute a source outside its view."""
    from aisle.harness.typed_snapshot import SnapshotError, build_typed_validation_snapshot

    view = _view(tmp_path)
    name = "src/aisle/nodes/segmented_pose.py"
    (view / name).unlink()
    (view / name).symlink_to(ROOT / name)
    with pytest.raises(SnapshotError):
        build_typed_validation_snapshot(ROOT, view, tmp_path / "snapshot")


def test_snapshot_rechecks_controller_manifests_even_when_overlaid(tmp_path, monkeypatch):
    """MON-13: dependency planning cannot silently use a stale controller manifest."""
    from aisle.harness import typed_snapshot

    view = _view(tmp_path)
    read = typed_snapshot._read
    seen = 0

    def changing_read(root, name):
        nonlocal seen
        data = read(root, name)
        if root == ROOT and name == "registry/manifests/segmented-pose.yaml":
            seen += 1
            if seen > 1:
                return data + b"\n# changed dependency declaration\n"
        return data

    monkeypatch.setattr(typed_snapshot, "_read", changing_read)
    with pytest.raises(typed_snapshot.SnapshotError, match="changed during capture"):
        typed_snapshot.build_typed_validation_snapshot(ROOT, view, tmp_path / "snapshot")


def test_snapshot_verification_refuses_changed_files_and_extra_helpers(tmp_path):
    """MON-6/MON-13: the validator's input receipt cannot authorize later file injection."""
    from aisle.harness.typed_snapshot import (
        SnapshotError,
        build_typed_validation_snapshot,
        verify_typed_validation_snapshot,
    )

    view = _view(tmp_path)
    output = tmp_path / "snapshot"
    record = build_typed_validation_snapshot(ROOT, view, output)
    (output / "unexpected.py").write_text("raise RuntimeError('not a validation dependency')")
    with pytest.raises(SnapshotError, match="drift"):
        verify_typed_validation_snapshot(output, record)
    (output / "unexpected.py").unlink()
    node = output / "src/aisle/nodes/segmented_pose.py"
    node.chmod(0o644)
    node.write_text("changed bytes")
    with pytest.raises(SnapshotError, match="drift"):
        verify_typed_validation_snapshot(output, record)


def test_snapshot_read_never_follows_replacement_after_type_check(tmp_path, monkeypatch):
    """MON-6/MON-13: concurrent replacement cannot redirect a controller read."""
    from aisle.harness.typed_snapshot import SnapshotError, _read

    root = tmp_path / "candidate"
    root.mkdir()
    candidate = root / "node.py"
    candidate.write_bytes(b"candidate")
    hidden = tmp_path / "hidden.py"
    hidden.write_bytes(b"private-controller-data")
    lstat = Path.lstat

    switched = False

    def replace_after_check(path, *args, **kwargs):
        nonlocal switched
        result = lstat(path, *args, **kwargs)
        if path == candidate and not switched:
            switched = True
            path.unlink()
            path.symlink_to(hidden)
        return result

    monkeypatch.setattr(Path, "lstat", replace_after_check)
    try:
        data = _read(root, "node.py")
    except (SnapshotError, OSError):
        return
    assert data == b"candidate"


@pytest.mark.parametrize("component", ["parent", "file"])
def test_snapshot_descriptor_open_refuses_concurrent_symlink(tmp_path, monkeypatch, component):
    """MON-6: neither an opened directory nor a leaf may redirect a snapshot read."""
    import os

    from aisle.harness.typed_snapshot import SnapshotError, _read

    root = tmp_path / "candidate"
    root.mkdir()
    (root / "node.py").write_bytes(b"candidate")
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "node.py").write_bytes(b"private")
    opened = os.open
    switched = False

    def replace_before_open(name, flags, *args, **kwargs):
        nonlocal switched
        target = "candidate" if component == "parent" else "node.py"
        if name == target and not switched:
            switched = True
            if component == "parent":
                root.rename(tmp_path / "original")
                root.symlink_to(hidden, target_is_directory=True)
            else:
                (root / "node.py").unlink()
                (root / "node.py").symlink_to(hidden / "node.py")
        return opened(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_before_open)
    with pytest.raises(SnapshotError):
        _read(root, "node.py")
    assert switched


def test_snapshot_archive_preserves_receipt_and_rejects_source_drift(tmp_path):
    """MON-12/MON-13: evidence archives retain original identities and verify source bytes."""
    from aisle.harness.typed_snapshot import (
        SnapshotError,
        archive_typed_snapshot,
        build_typed_validation_snapshot,
    )

    view = _view(tmp_path)
    snapshot = tmp_path / "snapshot"
    record = build_typed_validation_snapshot(ROOT, view, snapshot)
    archive = tmp_path / "archive"
    retained = archive_typed_snapshot(snapshot, record, archive)
    assert retained["snapshot_id"] == record["immutable_id"]
    assert set(retained["files"]) == set(record["files"]) | {"snapshot.json"}
    for name, digest in retained["files"].items():
        assert (archive / name).read_bytes() == (snapshot / name).read_bytes()
        assert hashlib.sha256((archive / name).read_bytes()).hexdigest() == digest
    (snapshot / "added").write_text("drift")
    with pytest.raises(SnapshotError):
        archive_typed_snapshot(snapshot, record, tmp_path / "refused")
    assert not (tmp_path / "refused").exists()
