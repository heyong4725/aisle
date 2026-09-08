"""MON-8/MON-13: serialized dynamic stages remain bound to the matched run."""

import hashlib
import sys
from pathlib import Path

import pytest
from test_matched_run_config import _config, _write

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_dynamic_provider_selects_framework_interpreter(tmp_path, monkeypatch, arm):
    """MON-8/MON-13: bind the real framework interpreter under the single-exec policy."""
    import sysconfig

    from aisle.harness import matched_dynamic_run, worker_declaration
    from aisle.harness.matched_runtime import capture_runtime

    config, _, receipt = _config(tmp_path)
    config["arm"] = arm
    prefix = tmp_path / "Python.framework/Versions/3.13"
    launcher = prefix / "bin/python3.13"
    interpreter = prefix / "Resources/Python.app/Contents/MacOS/Python"
    for path in (launcher, interpreter):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name)
        path.chmod(0o755)
    config["runtime_record"] = capture_runtime((*config["runtime_record"]["trees"], prefix))
    declaration = {"allocation_root": str(tmp_path / "allocated"), "timeout_s": 5}
    if arm == "typed":
        snapshot = receipt["snapshot_record"]
        declaration.update(
            snapshot=snapshot["snapshot_root"],
            snapshot_record=snapshot,
            validation_output=str(tmp_path / "private/evidence"),
            max_calls=1000,
        )
    else:
        module = Path(config["participant_root"]) / "experts/monolithic/expert_t1.py"
        module.parent.mkdir(parents=True)
        module.write_text("API_VERSION='1.0'\n")
        declaration.update(
            module_sha256=hashlib.sha256(module.read_bytes()).hexdigest(),
            max_primitive_calls=1000,
            max_handles=100,
        )
    config["launch"] = {"provider": declaration}
    path, _ = _write(tmp_path, config)
    monkeypatch.setattr(sys, "executable", str(launcher))
    monkeypatch.setattr(sys, "base_prefix", str(prefix))
    original = sysconfig.get_config_var
    monkeypatch.setattr(
        sysconfig,
        "get_config_var",
        lambda name: "Python" if name == "PYTHONFRAMEWORK" else original(name),
    )

    def observe(**kwargs):
        assert Path(kwargs["python"]) == interpreter
        assert kwargs["python_sha256"] == hashlib.sha256(interpreter.read_bytes()).hexdigest()
        assert kwargs["runtime_record"] == config["runtime_record"]
        raise RuntimeError("observed direct interpreter binding")

    if arm == "typed":
        monkeypatch.setattr(matched_dynamic_run, "TypedStageProvider", observe)
        provider = matched_dynamic_run.configured_typed_provider
    else:
        monkeypatch.setattr(worker_declaration, "provision_worker_declaration", observe)
        provider = matched_dynamic_run.configured_monolithic_provider
    with pytest.raises(RuntimeError, match="observed direct interpreter binding"):
        provider(config, path)


@pytest.mark.skipif(sys.platform != "darwin", reason="actual worker capability adapter")
def test_serialized_provider_creates_bound_stages_on_demand(tmp_path):
    """MON-8: run configuration selects dynamically generated, identity-bound stages."""
    from aisle.harness.matched_run import _typed
    from aisle.harness.treatment_confinement import SANDBOX_EXEC

    config, _, receipt = _config(tmp_path)
    snapshot = receipt["snapshot_record"]
    config["worker_adapter_sha256"] = hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest()
    config["launch"] = {
        "provider": {
            "snapshot": snapshot["snapshot_root"],
            "snapshot_record": snapshot,
            "validation_output": str(tmp_path / "private/evidence"),
            "allocation_root": str(tmp_path / "dynamic-allocations"),
            "timeout_s": 5,
            "max_calls": 1000,
        }
    }
    path, _ = _write(tmp_path, config)
    graph, factory = _typed(config, path)
    assert graph == Path(snapshot["snapshot_root"]) / "graphs/expert_t1.yaml"
    assert not (tmp_path / "dynamic-allocations/launch-0").exists()
    stage, generated = factory(0)
    assert generated["snapshot_id"] == snapshot["immutable_id"]
    assert stage.is_relative_to(path.parent / "run-controller/typed-provider")
    assert len(generated["hosts"]) == 4
    # A changed private run configuration cannot authorize a subsequent launch.
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="configuration"):
        factory(1)
    assert not (tmp_path / "dynamic-allocations/launch-1").exists()


@pytest.mark.parametrize("fault", ["fields", "participant", "source"])
def test_invalid_dynamic_binding_refuses_before_provider_creation(tmp_path, monkeypatch, fault):
    """MON-13: malformed or stale dynamic inputs never allocate a stage provider."""
    from aisle.harness import matched_dynamic_run
    from aisle.harness.matched_run import _typed

    config, _, receipt = _config(tmp_path)
    snapshot = receipt["snapshot_record"]
    declaration = {
        "snapshot": snapshot["snapshot_root"],
        "snapshot_record": snapshot,
        "validation_output": str(tmp_path / "private/evidence"),
        "allocation_root": str(tmp_path / "allocations"),
        "timeout_s": 5,
        "max_calls": 1000,
    }
    config["launch"] = {"provider": declaration}
    if fault == "fields":
        declaration["unbound_runtime"] = {}
    elif fault == "participant":
        snapshot["participant_root"] = str(tmp_path / "wrong-participant")
    else:
        source = Path(config["participant_root"]) / "src/aisle/nodes/segmented_pose.py"
        source.write_text(source.read_text() + "\n# edited after validation\n")
    path, _ = _write(tmp_path, config)
    monkeypatch.setattr(
        matched_dynamic_run, "TypedStageProvider", lambda **kw: pytest.fail("provider allocated")
    )
    with pytest.raises(ValueError):
        _typed(config, path)
    assert not (path.parent / "run-controller").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="actual worker capability adapter")
def test_dynamic_monolithic_preparation_binds_sealed_source(tmp_path):
    """MON-8/MON-13: actual worker provisioning cannot change the sealed module identity."""
    import json

    from aisle.harness.matched_run import _monolithic, _worker_binding
    from aisle.harness.treatment_confinement import SANDBOX_EXEC
    from aisle.monolith.worker_config import _load

    config, _, _ = _config(tmp_path)
    config["arm"] = "monolithic"
    module = Path(config["participant_root"]) / "experts/monolithic/expert_t1.py"
    module.parent.mkdir(parents=True)
    module.write_text("API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n")
    config["worker_adapter_sha256"] = hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest()
    config["launch"] = {
        "provider": {
            "allocation_root": str(tmp_path / "monolithic-allocated"),
            "timeout_s": 5,
            "max_primitive_calls": 1000,
            "max_handles": 100,
            "module_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
        }
    }
    path, digest = _write(tmp_path, config)
    selected_module, binding = _monolithic(config, path)
    assert selected_module == module
    worker = _load(binding["worker_config"], binding["worker_config_sha256"])
    _worker_binding(worker["launch"], config, path)
    assert worker["module_sha256"] == config["launch"]["provider"]["module_sha256"]
    receipt = json.loads(
        (path.parent / "run-controller/monolithic-provider/binding.json").read_text()
    )
    assert receipt["run_config_sha256"] == digest
    assert receipt["worker_config_sha256"] == binding["worker_config_sha256"]
    assert receipt["module_sha256"] == worker["module_sha256"]
    import copy

    from aisle.harness.matched_dynamic_run import verify_monolithic_provider_binding

    verify_monolithic_provider_binding(
        config, digest, receipt, worker, binding["worker_config_sha256"]
    )
    redirected = {**receipt, "worker_config": str(tmp_path / "unrelated-config.json")}
    with pytest.raises(ValueError, match="configuration path"):
        verify_monolithic_provider_binding(
            config, digest, redirected, worker, binding["worker_config_sha256"]
        )
    for field, value in (("max_handles", 999), ("bundle", str(tmp_path / "wrong-bundle"))):
        changed = copy.deepcopy(worker)
        changed["launch"][field] = value
        with pytest.raises(ValueError, match="sealed provider"):
            verify_monolithic_provider_binding(
                config, digest, receipt, changed, binding["worker_config_sha256"]
            )
