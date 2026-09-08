"""MON-8: controller environments match independently of private HOME locations."""

import copy

import pytest
from test_matched_run_launch import _inputs
from test_matched_tools import _controller

from aisle.harness.matched_session import AdmissionError, admit_pair
from aisle.harness.treatment_ambient import build_declared_environment

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("difference", [None, "PATH", "LANG"])
def test_controller_environments_match_outside_private_home(tmp_path, difference):
    """MON-8: private HOME paths may differ; controller environment settings must match."""
    fixture = tmp_path / "plan"
    fixture.mkdir()
    controller, views, _ = _controller(fixture, "typed")
    inputs_root = tmp_path / "inputs"
    inputs_root.mkdir()
    inputs = _inputs(inputs_root)
    bindings = {}
    for arm in ("typed", "monolithic"):
        source = {}
        if arm == "monolithic" and difference is not None:
            source[difference] = "/usr/bin:/bin:/sbin" if difference == "PATH" else "C"
        environment, record = build_declared_environment(
            tmp_path / (arm + "-controller-home"), source_env=source
        )
        bindings[arm] = {
            **copy.deepcopy(inputs["binding"]),
            "environment": environment,
            "environment_record": record,
        }
    candidates = copy.deepcopy(controller.plan["arms"])
    for candidate in candidates.values():
        candidate.pop("immutable_id")
        candidate["repository"].pop("visible_files")
    kwargs = dict(
        confinement=controller.plan["confinement_bindings"],
        ambient=controller.plan["ambient_bindings"],
        tool_runtime=inputs["runtime_record"],
        run_controller=bindings,
    )
    if difference is None:
        plan = admit_pair(controller.root, candidates, views, **kwargs)
        assert plan["run_controller"] == bindings
    else:
        with pytest.raises(AdmissionError, match="controller.*environment"):
            admit_pair(controller.root, candidates, views, **kwargs)
