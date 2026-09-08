"""MON-8/MON-12/MON-13: ordinary run requests seal dynamic typed provider inputs."""

import json
from pathlib import Path

import pytest
from test_typed_run_prepare import _controller

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("invalid", [False, True])
def test_ordinary_run_validates_current_sources_before_sealing_provider(
    tmp_path, monkeypatch, invalid
):
    """MON-12: current source validation precedes dynamic stage allocation under one reservation."""
    controller, views, output, _ = _controller(tmp_path)
    allocation = tmp_path / "dynamic-allocation"
    controller.worker_preparations = [
        {
            "provider": {
                "allocation_root": str(allocation),
                "timeout_s": 5,
                "max_calls": 1000,
            }
        }
    ]
    source = views["typed"] / "src/aisle/nodes/segmented_pose.py"
    source.write_text("# current edit\n" + source.read_text())
    if invalid:
        (views["typed"] / "registry/manifests/segmented-pose.yaml").write_text(
            "broken: declaration"
        )
    calls = []

    def dispatch(current, destination, record, started, wall, prepared):
        calls.append(json.loads(Path(prepared[0]).read_text()))
        record.update(
            ok=False,
            classification="infrastructure_exclusion",
            process=None,
            result={"ok": False, "error": "fixture stops before graph execution"},
        )

    monkeypatch.setattr(controller, "_prepared_run", dispatch)
    result = controller.run()
    assert result["reservation"] == {"runs": 1, "episodes": 1}
    assert result["worker_preparation"]["index"] == 0
    assert not allocation.exists()
    assert (
        output / "tool-000001/source-snapshot/src/aisle/nodes/segmented_pose.py"
    ).read_bytes() == source.read_bytes()
    if invalid:
        assert result["classification"] == "tool_result", result
        assert not calls
        assert not (output / "tool-000001/run-config.json").exists()
    else:
        assert len(calls) == 1, result
        provider = calls[0]["launch"]["provider"]
        assert provider["allocation_root"] == str(allocation)
        assert provider["snapshot_record"]["immutable_id"] == result["preparation"]["snapshot_id"]
        assert provider["validation_output"] == str(output / "tool-000001/validation")
        assert result["preparation"]["validation"]["ok"]


@pytest.mark.parametrize("fault", ["peer_home", "timeout", "calls", "reuse"])
@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_invalid_provider_template_refuses_before_validation(tmp_path, monkeypatch, fault, arm):
    """MON-13: worker allocation cannot reuse state or overlap participant write authority."""
    if arm == "typed":
        controller, _, _, _ = _controller(tmp_path)
    else:
        from test_matched_worker_journal import _worker_inputs

        controller, _, _, _, _ = _worker_inputs(tmp_path)
    allocation = tmp_path / "allocation"
    calls_key = "max_calls" if arm == "typed" else "max_primitive_calls"
    template = {"allocation_root": str(allocation), "timeout_s": 5, calls_key: 1000}
    if arm == "monolithic":
        template["max_handles"] = 100
    if fault == "peer_home":
        peer = "monolithic" if arm == "typed" else "typed"
        home = controller.plan["ambient_bindings"][peer]["environment"]["HOME"]
        template["allocation_root"] = str(Path(home) / "worker-allocation")
    elif fault == "timeout":
        template["timeout_s"] = -1
    elif fault == "calls":
        template[calls_key] = True
    else:
        allocation.mkdir()
        (allocation / "keep").write_text("existing state")
    controller.worker_preparations = [{"provider": template}]
    monkeypatch.setattr(controller, "_typed_check", lambda *args: pytest.fail("validation started"))
    monkeypatch.setattr(controller, "_prepared_run", lambda *args: pytest.fail("child started"))
    result = controller.run()
    assert result["classification"] == "infrastructure_exclusion", result
    assert not result["ok"] and result["process"] is None
    assert result["reservation"]["runs"] == 1
    assert f"{arm} provider" in result["error"]
    if fault == "reuse":
        assert (allocation / "keep").read_text() == "existing state"


def test_ordinary_monolithic_run_seals_current_module_without_executing_it(tmp_path, monkeypatch):
    """MON-3/MON-12: capture the current module before child provisioning."""
    import hashlib

    from test_matched_worker_journal import _worker_inputs

    controller, views, output, _, _ = _worker_inputs(tmp_path)
    allocation = tmp_path / "dynamic-monolithic"
    controller.worker_preparations = [
        {
            "provider": {
                "allocation_root": str(allocation),
                "timeout_s": 5,
                "max_primitive_calls": 1000,
                "max_handles": 100,
            }
        }
    ]
    module = views["monolithic"] / "experts/monolithic/expert_t1.py"
    module.write_text("raise AssertionError('parent must not import authored source')\n")
    calls = []

    def dispatch(current, destination, record, started, wall, prepared):
        calls.append(json.loads(Path(prepared[0]).read_text()))
        record.update(
            ok=False,
            classification="infrastructure_exclusion",
            process=None,
            result={"ok": False, "error": "fixture stops before child execution"},
        )

    monkeypatch.setattr(controller, "_prepared_run", dispatch)
    result = controller.run()
    assert len(calls) == 1, result
    assert result["reservation"] == {"runs": 1, "episodes": 1}
    assert result["worker_preparation"]["index"] == 0
    provider = calls[0]["launch"]["provider"]
    assert provider["module_sha256"] == hashlib.sha256(module.read_bytes()).hexdigest()
    archived = output / "tool-000001/monolithic-input/module.py"
    assert archived.read_bytes() == module.read_bytes()
    assert result["artifacts"]["monolithic-input/module.py"] == provider["module_sha256"]
    assert not allocation.exists()
    assert not (output / "tool-000001/monolithic-input/worker-config.json").exists()
