"""MON-6/MON-13: typed preparation must preserve protected and redirected inputs."""

import shutil
from pathlib import Path

import pytest
from test_typed_graph_stage import _declarations, _validated
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("fault", ["snapshot", "redirect"])
def test_typed_preparation_refuses_invalid_output_before_mutation(tmp_path, fault):
    """MON-13: invalid preparation output cannot mutate a validated snapshot or fill bundles."""
    from aisle.harness.typed_run_prepare import prepare_typed_stages

    inputs = _validated(tmp_path)
    declarations = _declarations(inputs)
    bundle = Path(next(iter(declarations.values()))["bundle"])
    shutil.rmtree(bundle)
    bundle.mkdir()
    for launch in declarations.values():
        launch.pop("bundle_manifest")
        launch.pop("source_roots")
    target = inputs["snapshot"] / "preparation"
    output = target
    if fault == "redirect":
        target = tmp_path / "redirect-target"
        target.mkdir()
        output = tmp_path / "private/preparation"
        output.symlink_to(target, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        prepare_typed_stages(
            controller_root=ROOT,
            snapshot=inputs["snapshot"],
            snapshot_record=inputs["snapshot_record"],
            validation_output=inputs["output"],
            declarations=[declarations],
            output=output,
            runtime_record=inputs["runtime_record"],
            adapter_sha256=next(iter(declarations.values()))["attestation"]["adapter"]["sha256"],
            protected_roots=(ROOT, inputs["snapshot"], inputs["output"]),
        )
    if fault == "snapshot":
        assert not target.exists()
    else:
        assert not list(target.iterdir())
    assert not list(bundle.iterdir())
