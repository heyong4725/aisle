"""MON-8/MON-13: allocate fresh verified stages when rollout requests each launch."""

import hashlib
import sys
from pathlib import Path

import pytest
from test_typed_graph_stage import _validated
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


@pytest.mark.skipif(sys.platform != "darwin", reason="actual worker capabilities")
def test_provider_selects_fresh_stages_without_predeclared_count(tmp_path):
    """MON-8/MON-13: sequential rollout selection creates distinct stages of one snapshot."""
    from aisle.harness.treatment_confinement import SANDBOX_EXEC
    from aisle.harness.typed_graph_stage import select_rollout_stage
    from aisle.harness.typed_stage_provider import TypedStageProvider

    inputs = _validated(tmp_path, direct_python=True)
    provider = TypedStageProvider(
        controller_root=ROOT,
        snapshot=inputs["snapshot"],
        snapshot_record=inputs["snapshot_record"],
        validation_output=inputs["output"],
        allocation_root=tmp_path / "allocations",
        evidence=tmp_path / "private/provider",
        hidden_roots=(tmp_path / "private",),
        runtime_record=inputs["runtime_record"],
        python=inputs["python"],
        python_sha256=inputs["python_sha256"],
        adapter_sha256=hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest(),
        timeout_s=5,
    )
    graph = inputs["snapshot"] / "graphs/expert_t1.yaml"
    history = []
    roots = []
    for index in range(2):
        (stage, record), _ = select_rollout_stage(
            provider,
            index,
            history,
            authored_bytes=graph.read_bytes(),
            graph=graph,
            controller_root=ROOT,
            embodiment="franka",
        )
        roots.append(stage)
        assert record["snapshot_id"] == inputs["snapshot_record"]["immutable_id"]
    assert roots[0] != roots[1] and len(history) == 2
    assert all(Path(root).is_dir() for root in roots)
    with pytest.raises(ValueError, match="sequential"):
        provider(0)
    assert not (tmp_path / "allocations/launch-2").exists()


@pytest.mark.parametrize("failure", ["error", "cancel", "collision"])
def test_failed_preparation_cannot_skip_to_another_launch(tmp_path, monkeypatch, failure):
    """MON-13: failed/cancelled or collided reservations stop the provider without overwrite."""
    import json

    from aisle.harness import typed_stage_provider as provider_module

    inputs = _validated(tmp_path)
    provider = provider_module.TypedStageProvider(
        controller_root=ROOT,
        snapshot=inputs["snapshot"],
        snapshot_record=inputs["snapshot_record"],
        validation_output=inputs["output"],
        allocation_root=tmp_path / "allocations",
        evidence=tmp_path / "private/provider",
        hidden_roots=(tmp_path / "private",),
        runtime_record=inputs["runtime_record"],
        python=inputs["python"],
        python_sha256=inputs["python_sha256"],
        adapter_sha256="0" * 64,
        timeout_s=5,
    )
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        if failure == "cancel":
            raise KeyboardInterrupt()
        raise ValueError("injected preparation failure")

    monkeypatch.setattr(provider_module, "provision_typed_workers", fail)
    attempt = tmp_path / "private/provider/launch-0"
    if failure == "collision":
        attempt.mkdir()
        (attempt / "result.json").write_text("preserve prior evidence")
    expected = (
        FileExistsError
        if failure == "collision"
        else KeyboardInterrupt
        if failure == "cancel"
        else ValueError
    )
    with pytest.raises(expected):
        provider(0)
    with pytest.raises(ValueError, match="terminal"):
        provider(1)
    if failure == "collision":
        assert calls == []
        assert (attempt / "result.json").read_text() == "preserve prior evidence"
    else:
        receipt = json.loads((attempt / "result.json").read_text())
        assert not receipt["ok"] and receipt["error"]
        assert len(calls) == 1


def test_provider_evidence_cannot_mutate_snapshot(tmp_path):
    """MON-13: provider evidence must remain outside immutable candidate inputs."""
    from aisle.harness.typed_stage_provider import TypedStageProvider

    inputs = _validated(tmp_path)
    evidence = inputs["snapshot"] / "provider-evidence"
    with pytest.raises(ValueError, match="overlap"):
        TypedStageProvider(
            controller_root=ROOT,
            snapshot=inputs["snapshot"],
            snapshot_record=inputs["snapshot_record"],
            validation_output=inputs["output"],
            allocation_root=tmp_path / "allocations",
            evidence=evidence,
            hidden_roots=(tmp_path / "private",),
            runtime_record=inputs["runtime_record"],
            python=inputs["python"],
            python_sha256=inputs["python_sha256"],
            adapter_sha256="0" * 64,
            timeout_s=5,
        )
    assert not evidence.exists()
