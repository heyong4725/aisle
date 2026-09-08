"""Hash-bound, unscored worker configuration shared by checks and the Dora broker."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path

from aisle.harness.treatment_confinement import MacOSPolicy
from aisle.monolith.supervisor import WorkerFailure
from aisle.monolith.worker_launch import launch_worker

_LAUNCH_FIELDS = {
    "bundle",
    "bundle_manifest",
    "runtime_record",
    "source_roots",
    "policy",
    "profile_path",
    "attestation",
    "python",
    "python_sha256",
    "environment",
    "environment_record",
    "timeout_s",
    "max_primitive_calls",
    "max_handles",
}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorkerFailure("duplicate worker configuration field")
        result[key] = value
    return result


def _load(path, digest):
    if type(digest) is not str or re.fullmatch("[0-9a-f]{64}", digest) is None:
        raise WorkerFailure("worker configuration requires an exact SHA-256")
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_file():
        raise WorkerFailure("worker configuration path is missing or redirected")
    try:
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise WorkerFailure("worker configuration exceeds size limit")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise WorkerFailure("worker configuration hash has drifted")
        config = json.loads(raw, object_pairs_hook=_object)
        json.dumps(config, allow_nan=False)
        if type(config) is not dict or set(config) != {
            "schema_version",
            "purpose",
            "embodiment",
            "module_sha256",
            "output_root",
            "launch",
        }:
            raise WorkerFailure("invalid worker configuration schema")
        if (
            config["schema_version"] != "aisle.monolith.worker-config.v1"
            or config["purpose"] != "expert_parity"
        ):
            raise WorkerFailure("worker configuration is not unscored engineering")
        if config["embodiment"] not in ("franka", "so101"):
            raise WorkerFailure("unsupported worker embodiment")
        if type(config["module_sha256"]) is not str or not re.fullmatch(
            "[0-9a-f]{64}", config["module_sha256"]
        ):
            raise WorkerFailure("worker configuration requires a module hash")
        if type(config["output_root"]) is not str or not Path(config["output_root"]).is_absolute():
            raise WorkerFailure("worker output root must be absolute")
        launch = config["launch"]
        if type(launch) is not dict or set(launch) != _LAUNCH_FIELDS:
            raise WorkerFailure("invalid worker launch fields")
        return config
    except WorkerFailure:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise WorkerFailure(f"invalid worker configuration: {exc}") from exc


class _SourceBoundWorker:
    def __init__(self, worker, source_hash):
        self.worker, self.source_hash = worker, source_hash

    def initialize(self, source, filename):
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != self.source_hash:
            raise WorkerFailure("worker authored source differs from configuration")
        return self.worker.initialize(source, filename)

    def event(self, event):
        return self.worker.event(event)


def configured_worker_factory(path, digest, *, phase):
    """Select an exact worker configuration or the explicitly absent legacy option."""
    if phase not in {"check", "run"}:
        raise WorkerFailure("invalid worker phase")
    if path is None and digest is None:
        return None
    if path is None or digest is None:
        raise WorkerFailure("worker configuration path and hash must both be supplied")
    _load(path, digest)

    @contextmanager
    def factory(primitives):
        config = _load(path, digest)
        if primitives.embodiment != config["embodiment"]:
            raise WorkerFailure("worker embodiment differs from configuration")
        launch = dict(config["launch"])
        try:
            launch["policy"] = MacOSPolicy(
                **{
                    name: value if name == "network_policy" else tuple(Path(p) for p in value)
                    for name, value in launch["policy"].items()
                }
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise WorkerFailure("invalid worker policy fields") from exc
        with launch_worker(
            **launch, output=Path(config["output_root"]) / phase, primitives=primitives
        ) as worker:
            yield _SourceBoundWorker(worker, config["module_sha256"])

    return factory
