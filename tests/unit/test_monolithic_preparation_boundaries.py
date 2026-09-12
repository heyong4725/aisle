"""MON-6/MON-13: monolithic preparation cannot write through source or redirected roots."""

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_monolith_worker_launch import _launch_inputs

pytestmark = pytest.mark.unit


def _inputs(tmp_path):
    launch = _launch_inputs(tmp_path)
    source = tmp_path / "participant-source"
    controller = source / "controller"
    controller.mkdir()
    views = {arm: source / arm for arm in ("typed", "monolithic")}
    for view in views.values():
        view.mkdir()
    module = views["monolithic"] / "experts/monolithic/expert_t1.py"
    module.parent.mkdir(parents=True)
    module.write_text("API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n")
    bundle = Path(launch["bundle"])
    shutil.rmtree(bundle)
    bundle.mkdir()
    declaration = {
        key: value
        for key, value in launch.items()
        if key not in {"primitives", "output", "bundle_manifest", "source_roots"}
    }
    declaration["policy"] = launch["policy"].canonical_dict()
    declaration.update(max_primitive_calls=1000, max_handles=100)
    declaration = json.loads(json.dumps(declaration, default=str))
    return {
        "controller_root": controller,
        "views": views,
        "output": tmp_path / "private/preparation",
        "declaration": declaration,
        "runtime": declaration["runtime_record"],
        "adapter": declaration["attestation"]["adapter"]["sha256"],
        "embodiment": "franka",
    }


@pytest.mark.parametrize("fault", ["source", "redirect"])
def test_preparation_refuses_source_or_redirected_output_before_writing(tmp_path, fault):
    """MON-6/MON-13: invalid output authority cannot mutate source or consume reservations."""
    from aisle.harness.monolithic_run_prepare import prepare_monolithic_run
    from aisle.monolith.supervisor import WorkerFailure

    inputs = _inputs(tmp_path)
    target = inputs["views"]["monolithic"] / "prepared"
    if fault == "source":
        inputs["output"] = target
    else:
        target.mkdir()
        inputs["output"].symlink_to(target, target_is_directory=True)
    with pytest.raises((ValueError, WorkerFailure)):
        prepare_monolithic_run(**inputs)
    assert not (target / "monolithic-input").exists()
    assert not list(Path(inputs["declaration"]["bundle"]).iterdir())


def test_preparation_accepts_disjoint_private_output(tmp_path):
    """MON-13: the valid private output still binds the unchanged authored source."""
    from aisle.harness.monolithic_run_prepare import prepare_monolithic_run
    from aisle.monolith.worker_config import _load

    inputs = _inputs(tmp_path)
    result = prepare_monolithic_run(**inputs)
    config = _load(result["worker_config"], result["worker_config_sha256"])
    assert config["purpose"] == "expert_parity"
    assert Path(result["worker_config"]).parent == inputs["output"] / "monolithic-input"


@pytest.mark.parametrize("legacy_present", [True, False])
@pytest.mark.parametrize("through_controller", [True, False])
def test_l2_preparation_binds_selected_module(tmp_path, legacy_present, through_controller):
    """MON-3/MON-13: L2 preparation cannot substitute the legacy L1 deliverable."""
    from aisle.harness.monolithic_run_prepare import prepare_monolithic_run

    inputs = _inputs(tmp_path)
    directory = inputs["views"]["monolithic"] / "experts/monolithic"
    selected = directory / "pilot_t1_l2.py"
    selected.write_text("API_VERSION='1.0'\n# selected L2 policy\n")
    if not legacy_present:
        (directory / "expert_t1.py").unlink()
    if through_controller:
        from aisle.harness.matched_tools import ToolController

        controller = SimpleNamespace(
            root=inputs["controller_root"], views=inputs["views"], arm="monolithic"
        )
        current = {
            "task_surface": "t1-l2-pilot-v1",
            "run_controller": {},
            "tool_runtime": inputs["runtime"],
            "arms": {"monolithic": {"confinement": {"adapter_binary_sha256": inputs["adapter"]}}},
            "development": {"embodiment": inputs["embodiment"]},
        }
        result = ToolController._prepare_monolithic_run(
            controller, current, inputs["output"], {"artifacts": {}}, inputs["declaration"]
        )
    else:
        result = prepare_monolithic_run(**inputs, task_surface="t1-l2-pilot-v1")
    archived = Path(result["worker_config"]).parent / "module.py"
    assert archived.read_bytes() == selected.read_bytes()


def test_preparation_refuses_unknown_surface_before_writing(tmp_path):
    """MON-13: an unknown selection cannot fall back to a different task."""
    from aisle.harness.monolithic_run_prepare import prepare_monolithic_run

    inputs = _inputs(tmp_path)
    with pytest.raises(ValueError, match="unsupported matched task surface"):
        prepare_monolithic_run(**inputs, task_surface="unknown")
    assert not inputs["output"].exists()
