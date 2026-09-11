"""MON-12/MON-13: real dynamic worker execution feeds the parent tool journal.

Outer admission is an unscored fixture. Worker capabilities and confinement use
actual sandbox-exec; this does not prove frontend parity or controller closure.
"""

import copy
import hashlib
import shutil
import sys
from pathlib import Path

import numpy
import pyarrow
import pytest
from test_matched_run_launch import _real_controller

pytestmark = pytest.mark.unit


@pytest.mark.skipif(sys.platform != "darwin", reason="actual worker sandbox")
def test_dynamic_monolithic_child_retains_auditable_authored_failure(tmp_path):
    """MON-12: reservation, child provisioning, confined worker and journal bind one failure."""
    from aisle.harness.matched_evidence import audit_tool_journal
    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.matched_session import admit_pair
    from aisle.harness.treatment_confinement import SANDBOX_EXEC

    controller, views, output = _real_controller(tmp_path, "monolithic")
    packages = tmp_path / "worker-codecs"
    packages.mkdir()
    for package in (numpy, pyarrow):
        shutil.copytree(
            Path(package.__file__).parent,
            packages / package.__name__,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    runtime_roots = {Path(path) for path in controller.plan["tool_runtime"]["trees"]}
    runtime = capture_runtime(sorted(runtime_roots | {packages}))
    candidates = copy.deepcopy(controller.plan["arms"])
    for candidate in candidates.values():
        candidate.pop("immutable_id")
        candidate["repository"].pop("visible_files")
        candidate["budget"]["tool_wall_ceiling_s"] = 120
        candidate["confinement"]["adapter_binary_sha256"] = hashlib.sha256(
            SANDBOX_EXEC.read_bytes()
        ).hexdigest()
    controller.plan = admit_pair(
        controller.root,
        candidates,
        views,
        confinement=controller.plan["confinement_bindings"],
        ambient=controller.plan["ambient_bindings"],
        development=controller.plan["development"],
        tool_runtime=runtime,
        run_controller=controller.plan["run_controller"],
    )
    module = views["monolithic"] / "experts/monolithic/expert_t1.py"
    module.write_text("API_VERSION='1.0'\nraise ValueError('dynamic authored failure')\n")
    controller.worker_preparations = [
        {
            "provider": {
                "allocation_root": str(tmp_path / "dynamic-worker"),
                "timeout_s": 10,
                "max_primitive_calls": 1000,
                "max_handles": 100,
            }
        }
    ]
    result = controller.run()
    assert result["classification"] == "tool_result", result
    assert not result["ok"]
    assert "dynamic authored failure" in result["result"]["error"]
    assert result["result"]["worker_evidence"]["ok"]
    assert result["reservation"] == {"runs": 1, "episodes": 1}
    audit = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm="monolithic",
        development=controller.plan["development"],
    )
    assert audit["ok"], audit

    def reaudit():
        return audit_tool_journal(
            output,
            session_id=controller.session_id,
            plan_id=controller.plan["immutable_id"],
            arm="monolithic",
            development=controller.plan["development"],
        )

    provider = output / "tool-000001/run-controller/monolithic-provider"
    extra = provider / "undeclared.txt"
    extra.write_text("not in the retained inventory")
    assert "provider evidence inventory differs" in reaudit()["error"]
    extra.unlink()
    observation = provider / "capability/report.json"
    original = observation.read_bytes()
    observation.unlink()
    assert "provider evidence inventory differs" in reaudit()["error"]
    observation.write_bytes(original + b"\n")
    assert "artifact hash drift" in reaudit()["error"]
    observation.write_bytes(original)
    assert reaudit()["ok"]
