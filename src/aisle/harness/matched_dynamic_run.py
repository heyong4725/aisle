"""Bind dynamic worker preparation to one private matched run configuration."""

from __future__ import annotations

import copy
import hashlib
import threading
from pathlib import Path

from aisle.harness.matched_runtime import worker_interpreter
from aisle.harness.typed_node_host import load_host_config
from aisle.harness.typed_snapshot import _read
from aisle.harness.typed_stage_provider import TypedStageProvider


def configured_typed_provider(config, config_path):
    """Build a stage callback that rechecks run/source/worker identity on every call."""
    from aisle.harness.matched_run import _load, _worker_binding

    config = copy.deepcopy(config)
    path = Path(config_path).absolute()
    digest = hashlib.sha256(_read(path.parent, path.name)).hexdigest()
    declaration = config["launch"]["provider"]
    if type(declaration) is not dict or set(declaration) != {
        "snapshot",
        "snapshot_record",
        "validation_output",
        "allocation_root",
        "timeout_s",
        "max_calls",
    }:
        raise ValueError("invalid typed provider configuration fields")
    snapshot_record = declaration["snapshot_record"]
    if (
        snapshot_record["controller_root"] != config["controller_root"]
        or snapshot_record["participant_root"] != config["participant_root"]
        or snapshot_record["snapshot_root"] != declaration["snapshot"]
    ):
        raise ValueError("typed provider snapshot differs from run source bindings")

    def current():
        if _load(path, digest) != config:
            raise ValueError("typed provider run configuration differs")
        for name, expected in snapshot_record["inputs"]["participant"].items():
            if (
                hashlib.sha256(_read(Path(config["participant_root"]), name)).hexdigest()
                != expected
            ):
                raise ValueError("participant changed after typed snapshot construction")

    current()
    python = worker_interpreter()
    provider = TypedStageProvider(
        controller_root=config["controller_root"],
        snapshot=declaration["snapshot"],
        snapshot_record=snapshot_record,
        validation_output=declaration["validation_output"],
        allocation_root=declaration["allocation_root"],
        evidence=path.parent / "run-controller/typed-provider",
        hidden_roots=(path.parent,),
        runtime_record=config["runtime_record"],
        python=python,
        python_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
        adapter_sha256=config["worker_adapter_sha256"],
        timeout_s=declaration["timeout_s"],
        max_calls=declaration["max_calls"],
    )
    failed = False
    lock = threading.Lock()

    def factory(index):
        nonlocal failed
        with lock:
            if failed:
                raise ValueError("typed provider binding is terminal after failure")
            try:
                current()
                stage, record = provider(index)
                if record["snapshot_id"] != snapshot_record["immutable_id"]:
                    raise ValueError("typed provider changed its admitted snapshot")
                for binding in record["hosts"].values():
                    host = load_host_config(binding["config_path"], binding["config_sha256"])
                    _worker_binding(host["launch"], config, path)
                current()
                return stage, record
            except BaseException:
                failed = True
                raise

    return Path(declaration["snapshot"]) / "graphs/expert_t1.yaml", factory


def configured_monolithic_provider(config, config_path):
    """Provision one worker from a sealed module hash inside the run child."""
    import json

    from aisle.harness.matched_run import _load
    from aisle.harness.monolithic_run_prepare import prepare_monolithic_run
    from aisle.harness.worker_declaration import provision_worker_declaration
    from aisle.monolith.worker_config import _load as load_worker

    config = copy.deepcopy(config)
    path = Path(config_path).absolute()
    digest = hashlib.sha256(_read(path.parent, path.name)).hexdigest()
    declaration = config["launch"]["provider"]
    if type(declaration) is not dict or set(declaration) != {
        "allocation_root",
        "timeout_s",
        "max_primitive_calls",
        "max_handles",
        "module_sha256",
    }:
        raise ValueError("invalid monolithic provider configuration fields")
    if config["arm"] != "monolithic":
        raise ValueError("monolithic provider requires the monolithic arm")
    view = Path(config["participant_root"])
    module = view / "experts/monolithic/expert_t1.py"

    def current():
        if _load(path, digest) != config:
            raise ValueError("monolithic provider run configuration differs")
        if (
            hashlib.sha256(_read(module.parent, module.name)).hexdigest()
            != declaration["module_sha256"]
        ):
            raise ValueError("monolithic provider source differs from sealed module")

    current()
    if type(declaration["allocation_root"]) is not str:
        raise ValueError("monolithic allocation root must be a path string")
    allocation = Path(declaration["allocation_root"])
    if (
        not allocation.is_absolute()
        or allocation.resolve() != allocation
        or allocation.exists()
        or allocation.is_symlink()
    ):
        raise ValueError("monolithic provider requires fresh canonical allocation")
    evidence = path.parent / "run-controller/monolithic-provider"
    python = worker_interpreter()
    launch = provision_worker_declaration(
        arm="monolithic",
        bundle=allocation / "bundle",
        home=allocation / "home",
        evidence=evidence,
        hidden_roots=(Path(config["controller_root"]), view, path.parent),
        runtime_record=config["runtime_record"],
        python=python,
        python_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
        adapter_sha256=config["worker_adapter_sha256"],
        timeout_s=declaration["timeout_s"],
        max_calls=declaration["max_primitive_calls"],
        max_handles=declaration["max_handles"],
    )
    current()
    binding = prepare_monolithic_run(
        controller_root=config["controller_root"],
        views={"monolithic": view},
        output=path.parent / "run-controller/monolithic-prepared",
        declaration=launch,
        runtime=config["runtime_record"],
        adapter=config["worker_adapter_sha256"],
        embodiment=config["development"]["embodiment"],
    )
    worker = load_worker(binding["worker_config"], binding["worker_config_sha256"])
    if worker["module_sha256"] != declaration["module_sha256"]:
        raise ValueError("prepared worker differs from sealed module")
    current()
    receipt = {
        "schema_version": "aisle.monolithic-provider-binding.v1",
        "run_config_sha256": digest,
        "module_sha256": declaration["module_sha256"],
        **binding,
    }
    destination = evidence / "binding.json"
    with destination.open("x") as stream:
        json.dump(receipt, stream, allow_nan=False)
    destination.chmod(0o444)
    return binding


def verify_monolithic_provider_binding(config, config_digest, receipt, worker, worker_digest):
    """Check retained generated-worker evidence against the sealed controller request."""
    if type(receipt) is not dict or set(receipt) != {
        "schema_version",
        "run_config_sha256",
        "module_sha256",
        "worker_config",
        "worker_config_sha256",
    }:
        raise ValueError("invalid retained monolithic provider binding")
    if (
        receipt["schema_version"] != "aisle.monolithic-provider-binding.v1"
        or receipt["run_config_sha256"] != config_digest
        or receipt["worker_config_sha256"] != worker_digest
    ):
        raise ValueError("monolithic provider binding differs from retained configuration")
    declaration = config["launch"]["provider"]
    launch = worker["launch"]
    expected_path = Path(worker["output_root"]).parent / "monolithic-input/worker-config.json"
    if receipt["worker_config"] != str(expected_path):
        raise ValueError("monolithic provider configuration path differs from prepared inputs")
    allocation = Path(declaration["allocation_root"])
    if (
        receipt["module_sha256"] != declaration["module_sha256"]
        or worker["module_sha256"] != declaration["module_sha256"]
        or worker["purpose"] != "expert_parity"
        or worker["embodiment"] != config["development"]["embodiment"]
        or launch["runtime_record"] != config["runtime_record"]
        or launch["attestation"]["adapter"]["sha256"] != config["worker_adapter_sha256"]
        or launch["bundle"] != str(allocation / "bundle")
        or launch["environment_record"]["home"] != str(allocation / "home")
        or any(
            launch[key] != declaration[key]
            for key in ("timeout_s", "max_primitive_calls", "max_handles")
        )
    ):
        raise ValueError("generated monolithic worker differs from sealed provider request")
