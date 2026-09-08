"""MON-8/MON-12/MON-13: hash-bound worker selection through public launch interfaces."""

import hashlib
import json
import subprocess
import sys

import pytest
from test_monolith_worker_launch import _launch_inputs

pytestmark = pytest.mark.unit


def _config(tmp_path, module):
    import shutil
    from pathlib import Path

    import numpy
    import pyarrow

    from aisle.harness.matched_runtime import capture_runtime

    inputs = _launch_inputs(tmp_path, direct_python=True)
    packages = tmp_path / "bound-runtime-assets"
    for package in (numpy, pyarrow):
        source = Path(package.__file__).parent
        shutil.copytree(
            source,
            packages / package.__name__,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        native = source.with_name(package.__name__ + ".libs")
        if native.is_dir():
            shutil.copytree(native, packages / native.name)
    inputs["runtime_record"] = capture_runtime(inputs["policy"].runtime_read_roots)
    launch = {key: value for key, value in inputs.items() if key not in {"primitives", "output"}}
    launch["policy"] = inputs["policy"].canonical_dict()
    for name in ("bundle", "profile_path", "python"):
        launch[name] = str(launch[name])
    launch.update(max_primitive_calls=1000, max_handles=100)
    record = {
        "schema_version": "aisle.monolith.worker-config.v1",
        "purpose": "expert_parity",
        "embodiment": "franka",
        "module_sha256": hashlib.sha256(module.read_bytes()).hexdigest(),
        "output_root": str(inputs["output"]),
        "launch": launch,
    }
    path = tmp_path / "worker-config.json"
    path.write_text(json.dumps(record))
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), inputs["output"]


def test_worker_config_binds_direct_framework_interpreter(tmp_path, monkeypatch):
    """MON-8/MON-13: bind the direct interpreter and adjacent native dependencies."""
    import sysconfig
    from pathlib import Path

    import numpy

    package = tmp_path / "installed/numpy"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# fixture package\n")
    native = package.with_name("numpy.libs")
    native.mkdir()
    (native / "dependency.so").write_bytes(b"bound native dependency")
    monkeypatch.setattr(numpy, "__file__", str(package / "__init__.py"))
    prefix = tmp_path / "Python.framework/Versions/3.13"
    launcher = prefix / "bin/python3.13"
    interpreter = prefix / "Resources/Python.app/Contents/MacOS/Python"
    for path in (launcher, interpreter):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name)
        path.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(launcher))
    monkeypatch.setattr(sys, "base_prefix", str(prefix))
    original = sysconfig.get_config_var
    monkeypatch.setattr(
        sysconfig,
        "get_config_var",
        lambda name: "Python" if name == "PYTHONFRAMEWORK" else original(name),
    )
    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\n")
    config, _, _ = _config(tmp_path, module)
    launch = json.loads(config.read_text())["launch"]
    assert Path(launch["python"]) == interpreter
    assert launch["python_sha256"] == hashlib.sha256(interpreter.read_bytes()).hexdigest()
    assert launch["policy"]["allowed_executables"] == [str(interpreter)]
    assert (tmp_path / "bound-runtime-assets/numpy.libs/dependency.so").read_bytes() == (
        native / "dependency.so"
    ).read_bytes()


def test_cli_check_selects_bound_worker(tmp_path):
    """MON-8/MON-12: the CLI constructs code in a worker and retains its attempt."""
    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n")
    config, digest, output = _config(tmp_path, module)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.cli",
            "monolith",
            "check",
            "--module",
            str(module),
            "--worker-config",
            str(config),
            "--worker-config-sha256",
            digest,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 0 and report["ok"] is True, report
    assert report["execution"] == "worker"
    assert json.loads((output / "check/rpc/worker.json").read_text())["state"] == "closed"


@pytest.mark.parametrize("change", ["config", "module"])
def test_bound_config_and_source_drift_are_refused(tmp_path, change):
    """MON-8/MON-13: neither runtime configuration nor authored source can change after binding."""
    from aisle.harness.monolith import check_module
    from aisle.monolith.supervisor import WorkerFailure
    from aisle.monolith.worker_config import configured_worker_factory

    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n")
    config, digest, _ = _config(tmp_path, module)
    if change == "config":
        config.write_text(config.read_text() + " ")
        with pytest.raises(WorkerFailure, match="hash"):
            configured_worker_factory(config, digest, phase="check")
    else:
        module.write_text("raise AssertionError('changed code must never execute')")
        report = check_module(
            module, worker_factory=configured_worker_factory(config, digest, phase="check")
        )
        assert report["ok"] is False and report["infrastructure_invalid"] is True
        assert "source" in report["error"]


def test_partial_worker_binding_never_selects_legacy_execution():
    """MON-8: incomplete worker selection is refused, never silently downgraded."""
    from aisle.monolith.supervisor import WorkerFailure
    from aisle.monolith.worker_config import configured_worker_factory

    with pytest.raises(WorkerFailure):
        configured_worker_factory(None, "0" * 64, phase="run")
    with pytest.raises(WorkerFailure):
        configured_worker_factory("missing.json", None, phase="check")
    assert configured_worker_factory(None, None, phase="check") is None


def test_worker_configuration_accepts_large_serialized_record(tmp_path):
    """MON-8/MON-13: exact configuration bytes may exceed the old 1 MiB ceiling."""
    from aisle.monolith.worker_config import _load

    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\n")
    path, _, _ = _config(tmp_path, module)
    original = json.loads(path.read_bytes())
    # Whitespace grows serialization without changing the launch declaration.
    data = path.read_bytes() + b" " * (1024 * 1024)
    path.write_bytes(data)
    assert _load(path, hashlib.sha256(data).hexdigest()) == original


def test_worker_configuration_retains_bounded_read(tmp_path):
    """MON-13: oversized configuration inputs are refused before JSON parsing."""
    from aisle.monolith.supervisor import WorkerFailure
    from aisle.monolith.worker_config import _load

    path = tmp_path / "oversized.json"
    data = b" " * (16 * 1024 * 1024 + 1)
    path.write_bytes(data)
    with pytest.raises(WorkerFailure, match="size limit"):
        _load(path, hashlib.sha256(data).hexdigest())


def test_stamped_graph_binds_worker_configuration_to_broker_only(tmp_path):
    """MON-8/MON-13: the runtime graph carries the exact configuration hash to its broker."""
    from pathlib import Path

    import yaml

    from aisle.harness.monolith import stamp_graph

    module = tmp_path / "controller.py"
    module.write_text("API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n")
    config, digest, _ = _config(tmp_path, module)
    graph = stamp_graph(
        Path(__file__).resolve().parents[2],
        module,
        tmp_path / "graphs",
        worker_config=config,
        worker_config_sha256=digest,
    )
    nodes = yaml.safe_load(graph.read_text())["nodes"]
    selected = [node for node in nodes if "AISLE_MONOLITH_WORKER_CONFIG" in node.get("env", {})]
    assert len(selected) == 1
    assert selected[0]["env"]["AISLE_MONOLITH_MODULE"] == str(module)
    assert selected[0]["env"]["AISLE_MONOLITH_WORKER_CONFIG"] == str(config)
    assert selected[0]["env"]["AISLE_MONOLITH_WORKER_CONFIG_SHA256"] == digest


def test_dora_broker_construction_selects_configured_worker(tmp_path):
    """MON-4/MON-12: the broker constructor used by the Dora entry point selects the run phase."""
    from aisle.nodes.monolith_broker import broker_from_environment

    module = tmp_path / "controller.py"
    module.write_text(
        "API_VERSION='1.0'\nclass Controller:\n"
        " def __init__(self,p,log): pass\n def on_event(self,e): return []\n"
    )
    config, digest, output = _config(tmp_path, module)
    with broker_from_environment(
        {
            "AISLE_MONOLITH_MODULE": str(module),
            "AISLE_MONOLITH_WORKER_CONFIG": str(config),
            "AISLE_MONOLITH_WORKER_CONFIG_SHA256": digest,
        }
    ) as broker:
        assert broker.record["execution"] == "worker"
        assert broker.deliver("reset_done", None, 0) == []
    assert json.loads((output / "run/rpc/worker.json").read_text())["state"] == "closed"


def test_cli_partial_worker_configuration_returns_json_refusal(tmp_path):
    """CON-8/MON-8: missing worker identity stays a structured infrastructure refusal."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.cli",
            "monolith",
            "check",
            "--module",
            str(tmp_path / "unused.py"),
            "--worker-config",
            str(tmp_path / "missing.json"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    report = json.loads(result.stdout)
    assert result.returncode == 1 and report["ok"] is False
    assert report["infrastructure_invalid"] is True
