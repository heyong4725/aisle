"""MON-2/MON-8/MON-13: generate workers from validated authored node identities."""

import hashlib
import sys
from pathlib import Path

import pytest
from test_typed_graph_stage import _validated
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


def test_actual_provisioning_fixture_selects_direct_worker_interpreter(tmp_path, monkeypatch):
    """MON-8/TRT-6: actual worker fixtures propagate the direct executable grant."""
    import test_typed_validation_launch as validation_fixture

    class SelectedDirectInterpreter(Exception):
        pass

    def selected(path, *, direct_python=False):
        assert path == tmp_path
        assert direct_python
        raise SelectedDirectInterpreter

    monkeypatch.setattr(validation_fixture, "_launch_inputs", selected)
    with pytest.raises(SelectedDirectInterpreter):
        _validated(tmp_path, direct_python=True)


def test_selection_preserves_renamed_and_repeated_authored_sources():
    """MON-2: worker allocation follows authored paths, not fixed baseline node IDs."""
    from aisle.harness.typed_worker_provisioning import authored_worker_nodes

    nodes = [
        {"id": name, "path": "../src/aisle/nodes/segmented_pose.py"}
        for name in ("fresh-pose", "pose-copy")
    ]
    nodes.append({"id": "trusted", "path": "../src/aisle/nodes/budget_guard.py"})
    assert set(authored_worker_nodes({"nodes": nodes})) == {"fresh-pose", "pose-copy"}


@pytest.mark.skipif(sys.platform != "darwin", reason="actual per-worker macOS capability")
def test_validated_snapshot_provisions_distinct_actual_worker_declarations(tmp_path):
    """MON-8/TRT-6: each selected node receives a fresh profile and observed capability."""
    from aisle.harness.treatment_confinement import SANDBOX_EXEC
    from aisle.harness.typed_run_prepare import prepare_typed_stages
    from aisle.harness.typed_worker_provisioning import provision_typed_workers

    inputs = _validated(tmp_path, direct_python=True)
    declarations = provision_typed_workers(
        snapshot=inputs["snapshot"],
        snapshot_record=inputs["snapshot_record"],
        validation_output=inputs["output"],
        allocation_root=tmp_path / "allocated-workers",
        evidence=tmp_path / "private/provisioned",
        hidden_roots=(tmp_path / "private",),
        runtime_record=inputs["runtime_record"],
        python=inputs["python"],
        python_sha256=inputs["python_sha256"],
        adapter_sha256=hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest(),
        timeout_s=5,
    )
    assert set(declarations) == {
        "segmented-pose",
        "grasp-planner-topdown",
        "ik-trajectory",
        "task-state-machine",
    }
    assert len({row["bundle"] for row in declarations.values()}) == 4
    assert len({row["environment_record"]["home"] for row in declarations.values()}) == 4
    for row in declarations.values():
        assert row["attestation"]["capability_pass"]
        assert row["attestation"]["adapter"]["path"] == str(SANDBOX_EXEC)
        assert not list(Path(row["bundle"]).iterdir())
        assert str(ROOT) in row["policy"]["hidden_roots"]
    stages = prepare_typed_stages(
        controller_root=ROOT,
        snapshot=inputs["snapshot"],
        snapshot_record=inputs["snapshot_record"],
        validation_output=inputs["output"],
        declarations=[declarations],
        output=tmp_path / "private/stages",
        runtime_record=inputs["runtime_record"],
        adapter_sha256=hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest(),
        protected_roots=(
            ROOT,
            inputs["snapshot"],
            Path(inputs["snapshot_record"]["participant_root"]),
        ),
    )
    assert len(stages["stages"]) == 1
    assert Path(stages["stages"][0]["root"]).is_dir()


@pytest.mark.parametrize("fault", ["validation", "snapshot"])
def test_invalid_inputs_never_allocate_workers(tmp_path, monkeypatch, fault):
    """MON-13: stale snapshot or failed validation cannot reach capability provisioning."""
    import json

    from aisle.harness import typed_worker_provisioning as provisioning

    inputs = _validated(tmp_path)
    if fault == "validation":
        path = inputs["output"] / "result.json"
        result = json.loads(path.read_text())
        result["ok"] = False
        path.write_text(json.dumps(result))
    else:
        (inputs["snapshot"] / "unexpected.py").write_text("pass")
    calls = []
    monkeypatch.setattr(provisioning, "provision_worker_declaration", lambda **kw: calls.append(kw))
    allocation, evidence = tmp_path / "allocated", tmp_path / "private/provisioned"
    with pytest.raises(ValueError):
        provisioning.provision_typed_workers(
            snapshot=inputs["snapshot"],
            snapshot_record=inputs["snapshot_record"],
            validation_output=inputs["output"],
            allocation_root=allocation,
            evidence=evidence,
            hidden_roots=(tmp_path / "private",),
            runtime_record=inputs["runtime_record"],
            python=inputs["python"],
            python_sha256=inputs["python_sha256"],
            adapter_sha256="0" * 64,
            timeout_s=5,
        )
    assert calls == []
    assert not allocation.exists() and not evidence.exists()


@pytest.mark.parametrize(
    "fault", ["bundle_manifest", "source_roots", "runtime", "adapter", "nonempty", "overlap"]
)
def test_preparation_refuses_unbound_or_occupied_reservations(tmp_path, fault):
    """MON-6/MON-13: refused preparation leaves source reservations and evidence untouched."""
    import copy

    from aisle.harness.typed_run_prepare import prepare_typed_stages

    inputs = _validated(tmp_path)
    reservation = tmp_path / "reserved-bundle"
    reservation.mkdir()
    declaration = {
        "bundle": str(reservation),
        "runtime_record": copy.deepcopy(inputs["runtime_record"]),
        "attestation": {"adapter": {"sha256": "1" * 64}},
        "environment_record": {"home": str(tmp_path / "worker-home")},
    }
    protected = [ROOT, inputs["snapshot"]]
    if fault in {"bundle_manifest", "source_roots"}:
        declaration[fault] = {}
    elif fault == "runtime":
        declaration["runtime_record"]["immutable_id"] = "wrong"
    elif fault == "adapter":
        declaration["attestation"]["adapter"]["sha256"] = "2" * 64
    elif fault == "nonempty":
        (reservation / "preserve").write_text("prior attempt")
    else:
        protected.append(reservation)
    output = tmp_path / "private/preparation"
    with pytest.raises(ValueError):
        prepare_typed_stages(
            controller_root=ROOT,
            snapshot=inputs["snapshot"],
            snapshot_record=inputs["snapshot_record"],
            validation_output=inputs["output"],
            declarations=[{"worker": declaration}],
            output=output,
            runtime_record=inputs["runtime_record"],
            adapter_sha256="1" * 64,
            protected_roots=protected,
        )
    assert not output.exists()
    assert reservation.is_dir()
    if fault == "nonempty":
        assert (reservation / "preserve").read_text() == "prior attempt"
    else:
        assert not list(reservation.iterdir())
