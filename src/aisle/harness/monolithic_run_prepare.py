"""Prepare fresh monolithic worker inputs from the current authored module."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from aisle.harness.matched_surface import LEGACY_SURFACE
from aisle.harness.matched_surface import task_surface as resolve_surface
from aisle.harness.treatment_confinement import MacOSPolicy, wrap_verified_command
from aisle.harness.typed_snapshot import _read
from aisle.monolith.worker_config import _LAUNCH_FIELDS, MAX_CONFIG_BYTES, _load
from aisle.monolith.worker_launch import build_worker_bundle, verify_worker_launch


def prepare_monolithic_run(
    *,
    controller_root,
    views,
    output,
    declaration,
    runtime,
    adapter,
    embodiment,
    task_surface=LEGACY_SURFACE,
):
    """Build controller-owned inputs while preserving supplied capability identities."""
    surface = resolve_surface(task_surface)
    required = _LAUNCH_FIELDS - {"bundle_manifest", "source_roots"}
    if type(declaration) is not dict or set(declaration) != required:
        raise ValueError("monolithic preparation requires exact worker declarations")
    launch = copy.deepcopy(declaration)
    if launch["runtime_record"] != runtime or launch["attestation"]["adapter"]["sha256"] != adapter:
        raise ValueError("worker declaration differs from admitted runtime or adapter")
    bundle, output = Path(launch["bundle"]).absolute(), Path(output).absolute()
    policy = MacOSPolicy(
        **{
            key: value if key == "network_policy" else tuple(Path(p) for p in value)
            for key, value in launch["policy"].items()
        }
    )
    protected = [
        Path(controller_root),
        *(Path(p) for p in views.values()),
        *(Path(p) for p in runtime["trees"]),
        Path(launch["environment_record"]["home"]),
    ]
    if output.resolve() != output or any(
        output.is_relative_to(p) or p.is_relative_to(output) for p in protected
    ):
        raise ValueError("monolithic preparation output is redirected or overlaps protected state")
    protected.append(output)
    if bundle.resolve() != bundle or any(
        bundle.is_relative_to(p) or p.is_relative_to(bundle) for p in protected
    ):
        raise ValueError("worker bundle reservation is redirected or overlaps protected state")
    if bundle.exists() and (not bundle.is_dir() or any(bundle.iterdir())):
        raise ValueError("worker bundle reservation is not empty; resume refused")
    readable = (*policy.visible_roots, *policy.output_roots, *policy.runtime_read_roots)
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in readable) or not any(
        output.is_relative_to(p) for p in policy.hidden_roots
    ):
        raise ValueError("monolithic preparation output is not private from the worker")
    module = Path(views["monolithic"]) / surface.monolithic_module
    source = _read(module.parent, module.name)
    destination = output / "monolithic-input"
    destination.mkdir(parents=True, exist_ok=False)
    with (destination / "module.py").open("xb") as stream:
        stream.write(source)
    (destination / "module.py").chmod(0o444)
    if bundle.exists():
        bundle.rmdir()
    launch["bundle_manifest"] = build_worker_bundle(bundle)
    launch["source_roots"] = [str(controller_root), *(str(p) for p in views.values())]
    compiled = verify_worker_launch(
        **{
            key: policy if key == "policy" else launch[key]
            for key in (
                "bundle",
                "bundle_manifest",
                "runtime_record",
                "source_roots",
                "policy",
                "python",
                "python_sha256",
                "environment",
                "environment_record",
            )
        }
    )
    wrap_verified_command(
        [launch["python"], "-I", "-B", "-c", "pass"],
        compiled,
        launch["profile_path"],
        launch["attestation"],
    )
    config = {
        "schema_version": "aisle.monolith.worker-config.v1",
        "purpose": "expert_parity",
        "embodiment": embodiment,
        "module_sha256": hashlib.sha256(source).hexdigest(),
        "output_root": str(output / "monolithic-execution"),
        "launch": launch,
    }
    data = json.dumps(config, sort_keys=True, allow_nan=False).encode()
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError("worker configuration exceeds size limit")
    path = destination / "worker-config.json"
    with path.open("xb") as stream:
        stream.write(data)
    path.chmod(0o444)
    digest = hashlib.sha256(data).hexdigest()
    _load(path, digest)
    return {"worker_config": str(path), "worker_config_sha256": digest}
