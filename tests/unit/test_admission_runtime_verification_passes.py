"""MON-13: each admission boundary inventories its shared runtime exactly once."""

import pytest
from test_typed_run_prepare import _controller

pytestmark = pytest.mark.unit


def test_admission_scans_once_without_caching_across_rechecks(tmp_path, monkeypatch):
    """MON-13: fresh rechecks and public bindings still detect changed runtime contents."""
    from aisle.harness import matched_runtime
    from aisle.harness.matched_run_launch import verify_controller_binding
    from aisle.harness.matched_session import AdmissionError, verify_active_plan, verify_plan
    from aisle.harness.typed_validation import verify_validation_binding

    controller, views, _, validation = _controller(tmp_path)
    original = matched_runtime.capture_runtime
    scans = []

    def capture(roots):
        scans.append(sorted(map(str, roots)))
        return original(roots)

    monkeypatch.setattr(matched_runtime, "capture_runtime", capture)
    plan = controller.plan
    verify_plan(plan, controller.root, views)
    assert len(scans) == 1
    verify_active_plan(plan, controller.root, views, "typed")
    assert len(scans) == 2

    source_roots = [controller.root, *views.values()]
    policies = [binding["policy"] for binding in plan["confinement_bindings"].values()]
    for arm in ("typed", "monolithic"):
        verify_controller_binding(
            plan["run_controller"][arm], plan["tool_runtime"], source_roots, policies
        )
    verify_validation_binding(validation, plan["tool_runtime"], source_roots, policies)
    assert len(scans) == 5
    assert all(roots == scans[0] for roots in scans)

    dependency = tmp_path / "validation/runtime-packages/package.py"
    assert dependency.is_file()
    dependency.write_text(dependency.read_text() + "\nCHANGED = True\n")
    with pytest.raises(AdmissionError, match="runtime inventory has drifted"):
        verify_plan(plan, controller.root, views)
    assert len(scans) == 6
