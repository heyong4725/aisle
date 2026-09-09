"""MON-4/MON-12: ordinary typed staging reaches Genesis and retained rollout evidence.

This is an engineering integration test, not expert parity or agent-session
acceptance. The run's task outcome is retained rather than used as a statistical
or per-seed success guarantee. Run artifacts remain under the repository runs/.
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = [
    pytest.mark.graph,
    pytest.mark.sim,
    pytest.mark.skipif(sys.platform != "darwin", reason="actual macOS worker confinement"),
]
ROOT = Path(__file__).resolve().parents[2]


def test_typed_worker_rollout_retains_real_episode_and_worker_audits(tmp_path, monkeypatch):
    """MON-4/MON-12: real validation, confinement, Dora, Genesis and postflight compose."""
    pytest.importorskip("genesis")
    cli = shutil.which("dora")
    if cli is None:
        pytest.skip("source-pinned Dora CLI is unavailable")
    prefix = Path(cli).resolve().parents[1]
    if not (prefix / "aisle-dora-receipt.json").is_file():
        pytest.skip("Dora installation has no source-pin receipt")
    monkeypatch.syspath_prepend(str(ROOT / "tests/unit"))
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import numpy
    import pyarrow
    from dora_runtime import verify
    from test_monolith_worker_launch import _worker_interpreter
    from test_typed_validation_launch import _inputs

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.rollout import rollout
    from aisle.harness.treatment_confinement import SANDBOX_EXEC
    from aisle.harness.typed_stage_provider import TypedStageProvider
    from aisle.harness.typed_validation import run_validation
    from aisle.harness.worker_capability import audit_worker_capability

    identity = verify(ROOT / "dora-runtime.json", prefix)
    assert identity["acceptance_ready"], identity
    # Preserve the installed sim extra; ordinary rollout gates verify its identity.
    monkeypatch.setenv("UV_NO_SYNC", "1")
    base = tmp_path.resolve()
    run_id = "typed-worker-integration-" + uuid4().hex
    (base / "dora-runtime.json").write_text(json.dumps(identity, indent=2))

    inputs = _inputs(base, direct_python=True)
    inputs["attestation"] = audit_worker_capability(
        policy=inputs["policy"],
        profile_path=inputs["profile_path"],
        python=inputs["python"],
        environment=inputs["environment"],
        environment_record=inputs["environment_record"],
        output=base / "private/validator-capability",
    )
    assert inputs["attestation"]["capability_pass"], inputs["attestation"]
    inputs["timeout_s"] = 30
    validation = run_validation(**inputs)
    (base / "validation.json").write_text(json.dumps(validation, indent=2, default=str))
    assert validation["ok"], validation
    packages = base / "actual-codecs"
    packages.mkdir()
    for package in (numpy, pyarrow):
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
        allocation_root=base / "allocations",
        evidence=base / "private/provider",
        hidden_roots=(base / "private",),
        runtime_record=runtime,
        python=python,
        python_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
        adapter_sha256=hashlib.sha256(SANDBOX_EXEC.read_bytes()).hexdigest(),
        timeout_s=360,
        max_calls=100000,
    )
    result = rollout(
        root=ROOT,
        graph=inputs["snapshot"] / "graphs/expert_t1.yaml",
        tier="T1",
        episodes=1,
        seeds=[7],
        reset_mode="teleport",
        verifier="oracle",
        run_id=run_id,
        branch="feat/519-matched-session",
        no_idea_gate=True,
        timeout_s=300,
        embodiment="franka",
        env_baseline="local",
        perception="L1",
        sim_extra="sim",
        per_episode_wall_s=120,
        record_simulator_work=True,
        typed_stage_factory=provider,
    )
    (base / "result.json").write_text(json.dumps(result, indent=2, default=str))
    assert result["ok"], result
    assert result["campaign_purpose"] == "expert_parity"
    assert len(result["episodes"]) == 1
    assert result["episodes"][0]["verifier"] == "oracle"
    assert result["episodes"][0]["seed"] == 7
    assert not result["episodes"][0].get("synthetic", False)
    manifest = json.loads((ROOT / "runs" / run_id / "manifest.json").read_text())
    assert manifest["typed_stage_error"] is None
    assert len(manifest["typed_postflight"]) == 1
    postflight = manifest["typed_postflight"][0]
    assert postflight["ok"] and len(postflight["workers"]) == 4
    assert all(
        worker["module_ok"] and worker["rc"] == 0 for worker in postflight["workers"].values()
    )
    assert manifest["typed_artifacts"]

    from aisle.harness.matched_evidence import retain_run

    collection = retain_run(ROOT / "runs" / run_id, base / "retained-run", run_id=run_id)
    assert collection["ok"], collection["error"]
    accounting = collection["simulator_work"]
    assert accounting["status"] == "recomputed", accounting
    summary = accounting["summary"]
    assert len(summary["launches"]) == 1
    launch = summary["launches"][0]
    assert launch["attempted"]["build"] == 1
    assert launch["completed"]["reset"] >= 1
    assert launch["completed"]["step"] > 0
    assert launch["schema_version"] == "aisle.simulator-work-journal.v3"
    assert launch["completed"]["physics_step"] >= launch["completed"]["step"] + 1
    assert launch["completed_env_steps"] == launch["completed"]["physics_step"]
    assert summary["completed_env_sim_ns"] > 0
    assert summary["producer_coverage_complete"] is False
