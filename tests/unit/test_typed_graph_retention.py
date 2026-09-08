"""MON-12/MON-13: preserve raw typed evidence with its audited identity."""

import hashlib
import json

import pytest

pytestmark = pytest.mark.unit


def _source(tmp_path):
    from aisle.harness.typed_snapshot import _digest

    source = tmp_path / "stage"
    source.mkdir()
    (source / "worker.frame").write_bytes(b"original frame")
    audit = {
        "schema_version": "aisle.typed-graph-audit.v1",
        "stage_root": str(source),
        "stage_id": "fixture",
        "ok": True,
        "files": {"worker.frame": hashlib.sha256(b"original frame").hexdigest()},
        "errors": [],
    }
    audit["immutable_id"] = _digest(audit)
    return source, audit


def test_retention_copies_exact_audited_bytes_and_persists_receipt(tmp_path):
    """MON-12: raw frames survive independently of their original staging directory."""
    from aisle.harness.typed_graph_audit import retain_graph_stage

    source, audit = _source(tmp_path)
    destination = tmp_path / "run/typed-artifacts-0"
    result = retain_graph_stage(source, destination, audit)
    assert result["ok"], result
    assert result["files"] == audit["files"]
    assert result["audit_id"] == audit["immutable_id"]
    assert (destination / "raw/worker.frame").read_bytes() == b"original frame"
    assert json.loads((destination / "collection.json").read_text()) == result


@pytest.mark.parametrize("mutation", ["drift", "missing", "extra", "redirect", "audit"])
def test_retention_records_invalid_artifacts_without_following_redirects(tmp_path, mutation):
    """MON-13: changed or incomplete evidence is retained but cannot pass collection."""
    from aisle.harness.typed_graph_audit import retain_graph_stage

    source, audit = _source(tmp_path)
    if mutation == "drift":
        (source / "worker.frame").write_bytes(b"changed frame")
    elif mutation == "missing":
        (source / "worker.frame").unlink()
    elif mutation == "extra":
        (source / "extra.log").write_text("unexpected")
    elif mutation == "redirect":
        outside = tmp_path / "outside"
        outside.write_text("must not copy")
        (source / "redirect").symlink_to(outside)
    else:
        audit["files"] = {}
    destination = tmp_path / "retained"
    result = retain_graph_stage(source, destination, audit)
    assert not result["ok"]
    assert result["errors"]
    assert (destination / "collection.json").is_file()
    assert not (destination / "raw/redirect").exists()
    if mutation != "missing":
        assert (destination / "raw/worker.frame").read_bytes() == (
            source / "worker.frame"
        ).read_bytes()


@pytest.mark.parametrize("location", ["overlap", "redirect", "existing"])
def test_retention_refuses_unsafe_or_reused_destination(tmp_path, location):
    """MON-13: collecting evidence cannot overwrite a previous attempt or mutate its source."""
    from aisle.harness.typed_graph_audit import retain_graph_stage

    source, audit = _source(tmp_path)
    destination = tmp_path / "retained"
    if location == "overlap":
        destination = source / "nested"
    elif location == "redirect":
        (tmp_path / "target").mkdir()
        destination.symlink_to(tmp_path / "target", target_is_directory=True)
    else:
        destination.mkdir()
        (destination / "sentinel").write_text("preserve")
    with pytest.raises((ValueError, OSError)):
        retain_graph_stage(source, destination, audit)
    assert (source / "worker.frame").read_bytes() == b"original frame"
    if location == "existing":
        assert (destination / "sentinel").read_text() == "preserve"


def test_retention_detects_source_change_during_copy(tmp_path, monkeypatch):
    """MON-13: a matching initial read cannot conceal mutation before collection finishes."""
    from aisle.harness import typed_graph_audit

    source, audit = _source(tmp_path)
    original = typed_graph_audit._read
    changed = False

    def read(root, name):
        nonlocal changed
        data = original(root, name)
        if root == source and not changed:
            changed = True
            (source / name).write_bytes(b"later mutation")
        return data

    monkeypatch.setattr(typed_graph_audit, "_read", read)
    result = typed_graph_audit.retain_graph_stage(source, tmp_path / "retained", audit)
    assert not result["ok"]
    assert any("changed during collection" in error for error in result["errors"])
    assert (tmp_path / "retained/raw/worker.frame").read_bytes() == b"original frame"


def test_failed_audit_can_still_have_complete_raw_collection(tmp_path):
    """MON-12: an invalid run's diagnostic evidence is not discarded or relabeled as valid."""
    from aisle.harness.typed_graph_audit import retain_graph_stage
    from aisle.harness.typed_snapshot import _digest

    source, audit = _source(tmp_path)
    audit["ok"] = False
    audit["errors"] = ["missing host receipt"]
    del audit["immutable_id"]
    audit["immutable_id"] = _digest(audit)
    result = retain_graph_stage(source, tmp_path / "retained", audit)
    assert result["ok"]
    assert not audit["ok"]
    assert result["audit_id"] == audit["immutable_id"]
