"""MON-8/MON-13: fresh sessions cannot inherit private mutable state."""

from pathlib import Path

import pytest
from test_typed_run_prepare import _controller

from aisle.harness.matched_session import AdmissionError, verify_active_plan, verify_plan

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("location", ["typed", "monolithic", "validator", "snapshots"])
@pytest.mark.parametrize("entry", ["file", "directory", "symlink"])
def test_fresh_private_state_rejects_prior_session_entries(tmp_path, location, entry):
    """MON-8/MON-13: refuse prior state, retaining active owning-arm mutations only."""
    controller, views, output, validation = _controller(tmp_path)
    plan = controller.plan
    if location in {"typed", "monolithic"}:
        home = Path(plan["run_controller"][location]["environment_record"]["home"])
        owner = location
    else:
        home = Path(
            validation["snapshot_storage"]
            if location == "snapshots"
            else validation["environment_record"]["home"]
        )
        owner = "typed"
    verify_plan(plan, controller.root, views)
    sentinel = home / "prior-session"
    if entry == "file":
        sentinel.write_text("previous session state")
    elif entry == "directory":
        sentinel.mkdir()
    else:
        sentinel.symlink_to(home / "missing-target")
    with pytest.raises(AdmissionError, match="private state|snapshot storage"):
        verify_plan(plan, controller.root, views)
    with pytest.raises(AdmissionError, match="private state|snapshot storage"):
        verify_active_plan(
            plan, controller.root, views, "monolithic" if owner == "typed" else "typed"
        )
    verify_active_plan(plan, controller.root, views, owner)


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("contaminated", [False, True])
def test_new_tool_controller_requires_fresh_private_state(tmp_path, arm, contaminated):
    """MON-8/MON-13: neither arm may adopt a prior run-controller HOME on construction."""
    from aisle.harness.matched_tools import ToolController

    prior, views, output, validation = _controller(tmp_path)
    if contaminated:
        home = Path(prior.plan["run_controller"][arm]["environment_record"]["home"])
        (home / "prior-session").write_text("old controller state")
    kwargs = dict(
        session_id="new-controller",
        python=prior.python,
        profile_path=prior.profile_path,
        attestation=prior.attestation,
        create_output=True,
    )
    if contaminated:
        with pytest.raises(AdmissionError, match="private state"):
            ToolController(prior.plan, prior.root, views, arm, output / "new", **kwargs)
    else:
        controller = ToolController(prior.plan, prior.root, views, arm, output / "new", **kwargs)
        assert controller.attempts == 0
