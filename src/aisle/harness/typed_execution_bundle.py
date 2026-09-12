"""Closed source/configuration bundle for typed workers; not OS confinement."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

from aisle.harness.matched_surface import LEGACY_SURFACE, record_surface
from aisle.harness.matched_surface import task_surface as resolve_task_surface
from aisle.harness.typed_snapshot import SnapshotError, _digest, _read

PARTICIPANT_FILES = tuple(
    f"src/aisle/nodes/{name}.py"
    for name in ("segmented_pose", "grasp_topdown", "ik_trajectory", "task_state_machine")
)
# Reviewed baseline dependency closure, including lazy imports. Never derive
# this list from authored source or manifests. Model weights are runtime assets.
CONTROLLER_FILES = (
    "src/aisle/__init__.py",
    "src/aisle/harness/typed_node_worker.py",
    "src/aisle/harness/typed_node_requests.py",
    "src/aisle/monolith/wire.py",
    "src/aisle/embodiment.py",
    "src/aisle/kinematics.py",
    "src/aisle/mobility/guard.py",
    "src/aisle/nodes/budget_guard.py",
    "src/aisle/nodes/h6_fault.py",
    "src/aisle/nodes/label_reader.py",
    "src/aisle/nodes/perception_session.py",
    "src/aisle/scenes/pharmacy.py",
    "src/aisle/scenes/store.py",
    "src/aisle/topics.py",
    "src/aisle/verifier/calibration.py",
    "src/aisle/verifier/models.py",
    "src/aisle/verifier/realistic.py",
    "src/aisle/verifier/stages.py",
    "src/aisle/scenes/meds.toml",
    "src/aisle/scenes/physics.toml",
    "src/aisle/scenes/planogram.toml",
    "src/aisle/scenes/locations.toml",
    "src/aisle/verifier/placement.toml",
    "src/aisle/verifier/thresholds.toml",
    "src/aisle/verifier/models.lock",
    "env/limits.toml",
    "assets/so101/so101.urdf",
)


class ExecutionBundleError(ValueError):
    """Execution inputs differ from their closed receipt."""


def build_execution_bundle(
    controller_root, participant_root, output, *, task_surface=LEGACY_SURFACE
):
    """Copy trusted dependencies and all four authored implementations as data."""
    surface = resolve_task_surface(task_surface)
    controller, participant, output = map(
        lambda p: Path(p).absolute(), (controller_root, participant_root, output)
    )
    roots = (controller, participant, output)
    if any(p.resolve() != p for p in roots) or any(
        a.is_relative_to(b) for a in roots for b in roots if a is not b
    ):
        raise ExecutionBundleError("execution roots must be canonical and disjoint")
    try:
        allowlist_path = f"{surface.docs_directory}/allowlist.json"
        allowlist = _read(controller, allowlist_path)
        editable = json.loads(allowlist)["typed"]["editable"]
        if {p for p in editable if p.endswith(".py")} != set(surface.participant_files):
            raise ExecutionBundleError("typed Python allowlist differs")
        captured = {
            name: (origin, _read(root, name))
            for origin, root, names in (
                ("controller", controller, (*CONTROLLER_FILES, *surface.extra_controller_files)),
                ("participant", participant, surface.participant_files),
            )
            for name in names
        }
        output.mkdir(parents=True, exist_ok=False)
        files = {}
        for name, (origin, data) in sorted(captured.items()):
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            target.chmod(0o444)
            files[name] = {
                "origin": origin,
                "sha256": hashlib.sha256(data).hexdigest(),
                "mode": 0o444,
            }
        for name, (origin, data) in captured.items():
            if _read(participant if origin == "participant" else controller, name) != data:
                raise ExecutionBundleError("execution input changed during capture")
        if _read(controller, allowlist_path) != allowlist:
            raise ExecutionBundleError("execution allowlist changed during capture")
        record = {
            "schema_version": "aisle.typed-execution-bundle.v1",
            "bundle_root": str(output),
            "files": files,
            "allowlist_sha256": hashlib.sha256(allowlist).hexdigest(),
        }
        if surface.identity != LEGACY_SURFACE:
            record["task_surface"] = surface.identity
        record["immutable_id"] = _digest(record)
        verify_execution_bundle(output, record)
        return record
    except (
        OSError,
        SnapshotError,
        KeyError,
        TypeError,
        AttributeError,
        json.JSONDecodeError,
    ) as exc:
        raise ExecutionBundleError("invalid execution bundle inputs") from exc


def verify_execution_bundle(bundle, record):
    """Check exact inventory, content, modes, and externally retained receipt."""
    try:
        bundle = Path(bundle).absolute()
        expected = dict(record)
        identity = expected.pop("immutable_id")
        if (
            bundle.resolve() != bundle
            or record["bundle_root"] != str(bundle)
            or record["schema_version"] != "aisle.typed-execution-bundle.v1"
            or _digest(expected) != identity
        ):
            raise ExecutionBundleError("execution bundle identity differs")
        files = record["files"]
        surface = record_surface(record)
        origins = dict.fromkeys(
            (*CONTROLLER_FILES, *surface.extra_controller_files), "controller"
        ) | dict.fromkeys(surface.participant_files, "participant")
        if {name: row["origin"] for name, row in files.items()} != origins:
            raise ExecutionBundleError("execution bundle dependency set differs")
        directories = {
            parent.as_posix()
            for name in files
            for parent in Path(name).parents
            if parent != Path(".")
        }
        observed = set()
        for path in bundle.rglob("*"):
            name = path.relative_to(bundle).as_posix()
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode) and name in directories:
                continue
            if not stat.S_ISREG(mode) or name not in files:
                raise ExecutionBundleError("execution bundle has an unexpected entry")
            row = files[name]
            if (
                stat.S_IMODE(mode) != 0o444
                or row["mode"] != 0o444
                or hashlib.sha256(_read(bundle, name)).hexdigest() != row["sha256"]
            ):
                raise ExecutionBundleError("execution bundle file drift")
            observed.add(name)
        if observed != set(files):
            raise ExecutionBundleError("execution bundle is incomplete")
    except (OSError, SnapshotError, KeyError, TypeError, AttributeError) as exc:
        raise ExecutionBundleError("invalid execution bundle receipt") from exc
