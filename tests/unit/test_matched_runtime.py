"""MON-8/MON-13: session admission pins executable runtime trees, not just Python."""

import pytest
from test_matched_session import prepared_pair

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


def test_admission_rechecks_runtime_package_identity(tmp_path):
    """MON-8/MON-13: package drift invalidates fresh and active retained plans."""
    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.matched_session import (
        AdmissionError,
        admit_pair,
        verify_active_plan,
        verify_plan,
    )

    root, candidates, views = prepared_pair(tmp_path)
    runtime = _runtime(tmp_path)
    record = capture_runtime([runtime])
    plan = admit_pair(root, candidates, views, tool_runtime=record)
    assert plan["tool_runtime"] == record
    (runtime / "package.py").write_text("VALUE = 2\n")
    with pytest.raises(AdmissionError, match="runtime"):
        verify_plan(plan, root, views)
    with pytest.raises(AdmissionError, match="runtime"):
        verify_active_plan(plan, root, views, "typed")


def test_admission_refuses_runtime_inside_participant_scratch(tmp_path):
    """MON-6/MON-13: hashing a writable runtime does not authorize participant replacement."""
    from pathlib import Path

    from test_matched_session import _confinement_pair

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.matched_session import AdmissionError, admit_pair

    root, candidates, views = prepared_pair(tmp_path)
    confinement = _confinement_pair(tmp_path, root, candidates, views)
    scratch = Path(confinement["typed"]["scratch"])
    runtime = _runtime(scratch)
    record = capture_runtime([runtime])
    with pytest.raises(AdmissionError, match="runtime overlaps"):
        admit_pair(root, candidates, views, confinement=confinement, tool_runtime=record)
