"""Retain a fresh monolithic worker attempt separately from simulation manifests."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

from aisle.harness.typed_snapshot import _read
from aisle.monolith.worker_config import _load


@contextmanager
def retain_worker_attempt(config_path, config_sha256, module, destination):
    """Capture inputs before execution and preserve raw worker bytes after cleanup.

    Collection integrity does not establish worker protocol validity, confinement,
    episode success, or study eligibility. The caller owns process cleanup.
    """
    config = _load(config_path, config_sha256)
    config_path, module, destination = (
        Path(p).absolute() for p in (config_path, module, destination)
    )
    source = Path(config["output_root"])
    policy = config["launch"]["policy"]
    readable = [
        Path(p)
        for key in ("visible_roots", "output_roots", "runtime_read_roots")
        for p in policy[key]
    ]
    for path in (source, destination):
        if path.resolve() != path:
            raise ValueError("worker evidence path is redirected")
        if any(path.is_relative_to(p) or p.is_relative_to(path) for p in readable):
            raise ValueError("worker evidence overlaps worker authority")
        if not any(path.is_relative_to(Path(p)) for p in policy["hidden_roots"]):
            raise ValueError("worker evidence is not hidden from worker authority")
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("worker evidence collection overlaps its source")
    if source.exists() or source.is_symlink():
        raise ValueError("worker evidence already exists; resume refused")
    module_bytes = _read(module.parent, module.name)
    if hashlib.sha256(module_bytes).hexdigest() != config["module_sha256"]:
        raise ValueError("worker evidence source differs from configuration")
    config_bytes = _read(config_path.parent, config_path.name)
    if hashlib.sha256(config_bytes).hexdigest() != config_sha256:
        raise ValueError("worker configuration changed before capture")
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in (("module.py", module_bytes), ("worker-config.json", config_bytes)):
        with (destination / name).open("xb") as stream:
            stream.write(data)
        (destination / name).chmod(0o444)
    report = {
        "schema_version": "aisle.monolithic-worker-retention.v1",
        "source_root": str(source),
        "config_sha256": config_sha256,
        "module_sha256": config["module_sha256"],
        "ok": False,
        "worker_evidence_present": False,
        "eligible_for_estimate": False,
        "files": {},
        "errors": [],
    }
    try:
        yield report
    finally:
        raw = destination / "raw"
        raw.mkdir()
        try:
            if source.exists() or source.is_symlink():
                report["worker_evidence_present"] = True
                if source.resolve() != source or not source.is_dir():
                    raise ValueError("worker evidence source is redirected or invalid")
                paths = sorted(source.rglob("*"))
                for path in paths:
                    name = path.relative_to(source).as_posix()
                    try:
                        if path.is_dir() and not path.is_symlink():
                            continue
                        data = _read(source, name)
                        target = raw / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with target.open("xb") as stream:
                            stream.write(data)
                        target.chmod(0o444)
                        report["files"][name] = hashlib.sha256(data).hexdigest()
                    except Exception as exc:
                        report["errors"].append(f"{name}: {exc}")
                if sorted(source.rglob("*")) != paths:
                    raise ValueError("worker evidence inventory changed during collection")
                for name, digest in report["files"].items():
                    if any(
                        hashlib.sha256(_read(root, name)).hexdigest() != digest
                        for root in (source, raw)
                    ):
                        raise ValueError("worker evidence changed during collection")
            _load(config_path, config_sha256)
            if _read(module.parent, module.name) != module_bytes:
                raise ValueError("worker source changed during execution")
        except Exception as exc:
            report["errors"].append(str(exc))
        report["ok"] = not report["errors"]
        with (destination / "collection.json").open("x") as stream:
            json.dump(report, stream, allow_nan=False)
