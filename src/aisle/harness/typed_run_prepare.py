"""Construct typed run stages from validated current sources and supplied worker grants."""

from __future__ import annotations

import copy
from pathlib import Path

from aisle.harness.matched_surface import record_surface
from aisle.harness.typed_execution_bundle import build_execution_bundle
from aisle.harness.typed_graph_stage import _validation, preflight_graph_stage, stage_typed_graph


def prepare_typed_stages(
    *,
    controller_root,
    snapshot,
    snapshot_record,
    validation_output,
    declarations,
    output,
    runtime_record,
    adapter_sha256,
    protected_roots,
):
    """Populate fresh bundles and stages; never invent or alter capability receipts."""
    if (
        not isinstance(declarations, list)
        or not declarations
        or any(type(launches) is not dict or not launches for launches in declarations)
    ):
        raise ValueError("typed run requires per-launch worker declarations")
    _validation(validation_output, snapshot_record)
    declarations = copy.deepcopy(declarations)
    output = Path(output).absolute()
    bundles = set()
    protected = [Path(p).absolute() for p in (*protected_roots, *runtime_record["trees"], output)]
    for launches in declarations:
        for launch in launches.values():
            if "bundle_manifest" in launch or "source_roots" in launch:
                raise ValueError("worker bundle manifest and source roots are controller-computed")
            if (
                launch["runtime_record"] != runtime_record
                or launch["attestation"]["adapter"]["sha256"] != adapter_sha256
            ):
                raise ValueError("worker declaration differs from admitted runtime or adapter")
            bundle = Path(launch["bundle"]).absolute()
            if bundle.resolve() != bundle:
                raise ValueError("worker bundle reservation is redirected")
            bundles.add(bundle)
            protected.append(Path(launch["environment_record"]["home"]))
    output_protected = [
        Path(p).absolute()
        for p in (
            controller_root,
            snapshot,
            validation_output,
            snapshot_record["participant_root"],
            *runtime_record["trees"],
            *(
                launch["environment_record"]["home"]
                for launches in declarations
                for launch in launches.values()
            ),
        )
    ]
    if output.resolve() != output or any(
        output.is_relative_to(p) or p.is_relative_to(output) for p in output_protected
    ):
        raise ValueError("typed preparation output is redirected or overlaps protected state")
    for bundle in bundles:
        if any(bundle.is_relative_to(p) or p.is_relative_to(bundle) for p in protected):
            raise ValueError("worker bundle reservation overlaps protected state")
        if any(
            bundle != other and (bundle.is_relative_to(other) or other.is_relative_to(bundle))
            for other in bundles
        ):
            raise ValueError("worker bundle reservations overlap")
        if bundle.exists() and (not bundle.is_dir() or any(bundle.iterdir())):
            raise ValueError("worker bundle reservation is not empty; resume refused")
    output.mkdir(parents=True, exist_ok=False)
    manifests = {}
    for bundle in sorted(bundles):
        # Profiles bind paths, so a reserved empty directory may precede source
        # capture. rmdir refuses anything nonempty; no existing files are removed.
        if bundle.exists():
            bundle.rmdir()
        manifests[str(bundle)] = build_execution_bundle(
            controller_root,
            snapshot,
            bundle,
            task_surface=record_surface(snapshot_record).identity,
        )
    stages = []
    for index, launches in enumerate(declarations):
        for launch in launches.values():
            launch["bundle_manifest"] = manifests[str(Path(launch["bundle"]).absolute())]
            launch["source_roots"] = [
                str(controller_root),
                snapshot_record["participant_root"],
                str(snapshot),
            ]
        stage = output / f"stage-{index}"
        receipt = stage_typed_graph(
            controller_root, snapshot, snapshot_record, validation_output, launches, stage
        )
        preflight_graph_stage(stage, receipt)
        stages.append({"root": str(stage), "stage_id": receipt["immutable_id"]})
    return {"stages": stages}
