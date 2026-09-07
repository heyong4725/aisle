"""MON-8/MON-13: session admission pins executable runtime trees, not just Python."""

import pytest

pytestmark = pytest.mark.unit


def _runtime(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "package.py").write_text("VALUE = 1\n")
    return root


@pytest.mark.parametrize("change", ["bytes", "extra", "mode", "symlink"])
def test_runtime_receipt_rejects_changed_inventory(tmp_path, change):
    """MON-13: a runtime identity cannot survive package substitution or extra imports."""
    from aisle.harness.matched_runtime import RuntimeDrift, capture_runtime, verify_runtime

    root = _runtime(tmp_path)
    record = capture_runtime([root])
    verify_runtime(record)
    path = root / "package.py"
    if change == "bytes":
        path.write_text("VALUE = 2\n")
    elif change == "extra":
        (root / "startup.pth").write_text("import injected\n")
    elif change == "mode":
        path.chmod(0o400)
    else:
        path.unlink()
        path.symlink_to(tmp_path / "outside.py")
    with pytest.raises(RuntimeDrift):
        verify_runtime(record)


def test_runtime_links_must_remain_inside_inventory_roots(tmp_path):
    """MON-6/MON-13: declared runtime symlinks cannot pull code from an unbound tree."""
    from aisle.harness.matched_runtime import RuntimeDrift, capture_runtime, verify_runtime

    root = _runtime(tmp_path)
    base = tmp_path / "base"
    base.mkdir()
    (base / "python").write_text("synthetic executable bytes")
    (root / "python").symlink_to(base / "python")
    with pytest.raises(RuntimeDrift, match="outside"):
        capture_runtime([root])
    record = capture_runtime([root, base])
    verify_runtime(record)
    (base / "python").write_text("changed executable bytes")
    with pytest.raises(RuntimeDrift):
        verify_runtime(record)


@pytest.mark.parametrize("change", ["retarget", "create_target"])
def test_internal_dangling_runtime_link_is_bound_and_rechecked(tmp_path, change):
    """MON-13: omitted optional framework headers remain bound, never silently skipped."""
    from aisle.harness.matched_runtime import RuntimeDrift, capture_runtime, verify_runtime

    root = _runtime(tmp_path)
    target = root / "Versions/8.6/PrivateHeaders"
    target.parent.mkdir(parents=True)
    link = root / "PrivateHeaders"
    link.symlink_to("Versions/8.6/PrivateHeaders")
    record = capture_runtime([root])
    assert record["trees"][str(root)]["PrivateHeaders"]["target"] == str(target)
    verify_runtime(record)
    if change == "retarget":
        link.unlink()
        link.symlink_to("Versions/8.6/OtherHeaders")
    else:
        target.mkdir()
        (target / "header.h").write_text("new runtime content")
    with pytest.raises(RuntimeDrift):
        verify_runtime(record)


def test_missing_external_runtime_target_is_still_refused(tmp_path):
    """MON-6/MON-13: a nonexistent target cannot excuse a link outside the closed runtime."""
    from aisle.harness.matched_runtime import RuntimeDrift, capture_runtime

    root = _runtime(tmp_path)
    (root / "link").symlink_to(tmp_path / "outside-missing")
    with pytest.raises(RuntimeDrift, match="outside"):
        capture_runtime([root])
