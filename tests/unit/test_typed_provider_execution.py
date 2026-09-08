"""MON-8/MON-12: execute generated typed stages with actual confined workers.

Validation uses the normal validator behind a synthetic adapter. Host events are
fixtures, not a Dora simulation or evidence of matched frontend parity.
"""

import hashlib
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import numpy
import pyarrow as pa
import pytest
from test_monolith_worker_launch import _worker_interpreter
from test_turn_node import Raw
from test_typed_validation_launch import _inputs
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


@pytest.mark.skipif(sys.platform != "darwin", reason="actual worker sandbox")
@pytest.mark.parametrize("renamed", [False, True])
def test_dynamic_provider_executes_baseline_hosts_and_audits_stage(tmp_path, renamed):
    """MON-12: all generated baseline workers consume host turns and retain bound evidence."""
    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.treatment_confinement import SANDBOX_EXEC
    from aisle.harness.typed_graph_audit import audit_graph_stage
    from aisle.harness.typed_node_host import run_configured_node
    from aisle.harness.typed_stage_provider import TypedStageProvider
    from aisle.harness.typed_validation import run_validation

    inputs = _inputs(tmp_path)
    if renamed:
        from test_treatment_confinement import _attestation

        from aisle.harness.treatment_confinement import compile_macos_profile
        from aisle.harness.typed_snapshot import build_typed_validation_snapshot

        view = Path(inputs["snapshot_record"]["participant_root"])
        for name in (
            "graphs/expert_t1.yaml",
            "graphs/turn_plans/expert_t1.json",
            "registry/manifests/segmented-pose.yaml",
        ):
            path = view / name
            path.write_text(path.read_text().replace("segmented-pose", "fresh-pose"))
        old_snapshot = inputs["snapshot"]
        inputs["snapshot"] = tmp_path / "renamed-snapshot"
        inputs["snapshot_record"] = build_typed_validation_snapshot(ROOT, view, inputs["snapshot"])
        inputs["policy"] = replace(
            inputs["policy"],
            visible_roots=tuple(
                inputs["snapshot"] if p == old_snapshot else p
                for p in inputs["policy"].visible_roots
            ),
        )
        compiled = compile_macos_profile(inputs["policy"])
        inputs["profile_path"].write_text(compiled.text)
        inputs["attestation"] = _attestation(
            compiled, inputs["profile_path"], tmp_path / "synthetic-adapter"
        )
    validation = run_validation(**inputs)
    assert validation["ok"], validation
    packages = tmp_path / "actual-codecs"
    packages.mkdir()
    for package in (numpy, pa):
        shutil.copytree(
            Path(package.__file__).parent,
            packages / package.__name__,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    python, runtime_root = _worker_interpreter()
    runtime = capture_runtime((runtime_root, packages))
    provider = TypedStageProvider(
        controller_root=ROOT,
        snapshot=inputs["snapshot"],
        snapshot_record=inputs["snapshot_record"],
        validation_output=inputs["output"],
        allocation_root=tmp_path / "allocations",
        evidence=tmp_path / "private/provider",
        hidden_roots=(tmp_path / "private",),
        runtime_record=runtime,
        python=python,
        python_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
        adapter_sha256=hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest(),
        timeout_s=10,
    )
    stage, record = provider(0)
    assert len(record["hosts"]) == 4
    assert ("fresh-pose" if renamed else "segmented-pose") in record["hosts"]
    for name, binding in record["hosts"].items():
        raw = Raw(
            [
                {
                    "type": "INPUT",
                    "id": "turn",
                    "value": pa.array([3], type=pa.uint64()),
                    "metadata": {
                        "turn_epoch": 2,
                        "turn_id": 3,
                        "sim_time_ns": 30,
                        "target_node": name,
                        "expected_inputs": [],
                        "expected_counts": [],
                    },
                }
            ]
        )
        result = run_configured_node(
            binding["config_path"], binding["config_sha256"], raw_node_factory=lambda raw=raw: raw
        )
        assert result["ok"], (name, result)
        assert result["worker"]["input_exhausted"]
        assert raw.sent[-1][0] == "turn_done"
        assert raw.sent[-1][2]["turn_id"] == 3
    audit = audit_graph_stage(stage, record)
    assert audit["ok"], audit
    assert set(audit["workers"]) == set(record["hosts"])
