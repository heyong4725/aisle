"""Controller-owned validation snapshots of the complete typed deliverable.

Candidate implementations are copied as data. This module neither imports them
nor authorizes their execution; the caller must confine the validation process.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import yaml

from aisle.harness.matched_surface import LEGACY_SURFACE, record_surface
from aisle.harness.matched_surface import task_surface as resolve_task_surface


class SnapshotError(ValueError):
    """A typed validation snapshot cannot be bound to its declared inputs."""


def _digest(record):
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _read(root, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
        raise SnapshotError("validation input must be a canonical relative path")
    path = Path(root).absolute() / relative
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            os.close(directory)
            directory = child
        handle = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(handle, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise SnapshotError("validation input is not a regular file")
            return stream.read()
    except OSError as exc:
        raise SnapshotError("validation input is missing or redirected") from exc
    finally:
        os.close(directory)


def build_typed_validation_snapshot(
    controller_root, participant_root, output, *, task_surface=LEGACY_SURFACE
):
    """Overlay the exact typed allowlist on pinned registry validation inputs.

    Dependencies come only from controller manifests. Authored manifests cannot
    cause the snapshot builder to read or copy an arbitrary additional path.
    """
    surface = resolve_task_surface(task_surface)
    controller, participant, output = (
        Path(p).absolute() for p in (controller_root, participant_root, output)
    )
    if any(path.resolve() != path for path in (controller, participant, output)):
        raise SnapshotError("validation roots must be canonical")
    if (
        controller.is_relative_to(participant)
        or participant.is_relative_to(controller)
        or output.is_relative_to(participant)
        or participant.is_relative_to(output)
        or controller.is_relative_to(output)
    ):
        raise SnapshotError("validation roots overlap")
    captured = {}
    inputs = {}

    def capture(origin, name):
        root = participant if origin == "participant" else controller
        data = _read(root, name)
        inputs[(origin, name)] = data
        captured[name] = (origin, data)

    allowlist_name = f"{surface.docs_directory}/allowlist.json"
    capture("controller", allowlist_name)
    try:
        editable = json.loads(captured[allowlist_name][1])["typed"]["editable"]
        if (
            type(editable) is not list
            or not editable
            or any(type(p) is not str for p in editable)
            or len(set(editable)) != len(editable)
        ):
            raise SnapshotError("typed editable allowlist is invalid")
        for name in ("registry/schema/capability.schema.json", "registry/schema/schemas.toml"):
            capture("controller", name)
        manifests = sorted((controller / "registry/manifests").glob("*.yaml"))
        if not manifests:
            raise SnapshotError("controller registry has no manifests")
        for manifest in manifests:
            name = manifest.relative_to(controller).as_posix()
            capture("controller", name)
            declaration = yaml.safe_load(captured[name][1])
            if type(declaration) is not dict:
                raise SnapshotError("controller manifest is not a mapping")
            source = declaration.get("source")
            if type(source) is str and ":" not in source:
                # Keep the validator's normal missing-source diagnostics when a
                # controller registry contains an unresolved optional source.
                if (controller / source).exists() or (controller / source).is_symlink():
                    capture("controller", source)
        for name in editable:
            capture("participant", name)
    except (KeyError, TypeError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise SnapshotError("invalid controller validation inputs") from exc

    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, (origin, data) in sorted(captured.items()):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        target.chmod(0o444)
        files[name] = {"origin": origin, "sha256": hashlib.sha256(data).hexdigest(), "mode": 0o444}
    # A source changed during capture cannot be silently promoted to a sealed
    # validation input. Keep any partial snapshot for the caller's error record.
    for (origin, name), data in inputs.items():
        root = participant if origin == "participant" else controller
        if _read(root, name) != data:
            raise SnapshotError("validation input changed during capture")
    record = {
        "schema_version": "aisle.typed-validation-snapshot.v1",
        "files": files,
        "inputs": {
            origin: {
                name: hashlib.sha256(data).hexdigest()
                for (role, name), data in sorted(inputs.items())
                if role == origin
            }
            for origin in ("controller", "participant")
        },
        "controller_root": str(controller),
        "participant_root": str(participant),
        "snapshot_root": str(output),
        "executable": False,
    }
    if surface.identity != LEGACY_SURFACE:
        record["task_surface"] = surface.identity
    record["immutable_id"] = _digest(record)
    with (output / "snapshot.json").open("x") as stream:
        stream.write(json.dumps(record, indent=2, allow_nan=False) + "\n")
    verify_typed_validation_snapshot(output, record)
    return record


def verify_typed_validation_snapshot(output, record):
    """Verify the retained inventory and immutable data identity before validation."""
    record_surface(record)
    output = Path(output).absolute()
    expected = dict(record)
    identity = expected.pop("immutable_id")
    if (
        output.resolve() != output
        or record["snapshot_root"] != str(output)
        or _digest(expected) != identity
    ):
        raise SnapshotError("validation snapshot identity differs")
    if json.loads(_read(output, "snapshot.json")) != record:
        raise SnapshotError("validation snapshot receipt differs")
    observed = {}
    for path in output.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise SnapshotError("validation snapshot contains a redirected or special entry")
        if not stat.S_ISREG(mode) or path == output / "snapshot.json":
            continue
        name = path.relative_to(output).as_posix()
        observed[name] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mode": stat.S_IMODE(mode),
        }
    if observed != {
        name: {k: v for k, v in row.items() if k != "origin"}
        for name, row in record["files"].items()
    }:
        raise SnapshotError("validation snapshot files have drifted")


def archive_typed_snapshot(source, record, destination):
    """Retain the validated input bytes in attempt evidence, without relocating identity."""
    source, destination = (Path(p).absolute() for p in (source, destination))
    if (
        destination.resolve() != destination
        or destination.is_relative_to(source)
        or source.is_relative_to(destination)
    ):
        raise SnapshotError("snapshot archive is redirected or overlaps its source")
    verify_typed_validation_snapshot(source, record)
    destination.mkdir(parents=True, exist_ok=False)
    files = {}
    for name in sorted([*record["files"], "snapshot.json"]):
        data = _read(source, name)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(data)
        target.chmod(0o444)
        files[name] = hashlib.sha256(data).hexdigest()
    verify_typed_validation_snapshot(source, record)
    for name, digest in files.items():
        if hashlib.sha256(_read(destination, name)).hexdigest() != digest:
            raise SnapshotError("snapshot archive changed during collection")
    return {"snapshot_id": record["immutable_id"], "files": files}
