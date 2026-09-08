"""MON-12/MON-13: actual prepared controller and worker feed the common journal."""

import hashlib
import json
from pathlib import Path

import pytest
from test_matched_run_launch import _real_controller
from test_monolith_worker_config import _config
from test_treatment_confinement import _attestation

pytestmark = pytest.mark.unit


def _worker_inputs(tmp_path, *, authored_failure=True):
    import copy

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.matched_session import admit_pair
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile

    controller, views, output = _real_controller(tmp_path, "monolithic")
    module = views["monolithic"] / "experts/monolithic/expert_t1.py"
    if authored_failure:
        module.write_text("API_VERSION='1.0'\nraise ValueError('authored failure')\n")
    worker_root = tmp_path / "worker"
    worker_root.mkdir()
    path, _, _ = _config(worker_root, module)
    worker = json.loads(path.read_text())
    launch = worker["launch"]
    candidates = copy.deepcopy(controller.plan["arms"])
    for candidate in candidates.values():
        candidate.pop("immutable_id")
        candidate["repository"].pop("visible_files")
    runtime = capture_runtime(
        (*controller.plan["tool_runtime"]["trees"], worker_root / "bound-runtime-assets")
    )
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
    launch["runtime_record"] = controller.plan["tool_runtime"]
    launch["source_roots"] = [str(controller.root), *(str(p) for p in views.values())]
    launch["policy"]["runtime_read_roots"] = list(controller.plan["tool_runtime"]["trees"])
    launch["policy"]["hidden_roots"].extend(
        [str(controller.root), *(str(p) for p in views.values()), str(output)]
    )
    policy = MacOSPolicy(
        **{
            key: value if key == "network_policy" else tuple(Path(p) for p in value)
            for key, value in launch["policy"].items()
        }
    )
    compiled = compile_macos_profile(policy)
    Path(launch["profile_path"]).write_text(compiled.text)
    launch["attestation"] = _attestation(
        compiled, Path(launch["profile_path"]), worker_root / "synthetic-adapter"
    )
    path.write_text(json.dumps(worker))
    return controller, views, output, path, worker


@pytest.mark.parametrize("fault", ["extra", "missing_index"])
def test_actual_worker_precheck_journal_rejects_unindexed_collection_file(tmp_path, fault):
    """MON-12/MON-13: normal precheck failure is auditable; undeclared retained files are not."""
    from aisle.harness.matched_evidence import audit_tool_journal

    controller, views, output, path, worker = _worker_inputs(tmp_path)
    result = controller.run_with_launch(
        {
            "worker_config": str(path),
            "worker_config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )
    assert result["classification"] == "tool_result", result
    assert not result["ok"]
    assert "authored failure" in result["result"]["error"]
    assert result["result"]["worker_evidence"]["ok"]
    assert result["run_evidence"] is None
    args = dict(
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm="monolithic",
        development=controller.plan["development"],
    )
    audit = audit_tool_journal(output, **args)
    assert audit["ok"], audit
    retained = output / "tool-000001/run-controller/monolithic-worker"
    if fault == "extra":
        (retained / "unindexed.log").write_text("unexpected evidence")
    else:
        from aisle.harness.matched_session import _digest

        del result["artifacts"]["run-controller/monolithic-worker/raw/check/rpc/worker.json"]
        result.pop("immutable_id")
        result["immutable_id"] = _digest(result)
        (output / "tool-000001/attempt.json").write_text(json.dumps(result))
        journal = output / "tool-events.jsonl"
        events = [json.loads(line) for line in journal.read_text().splitlines()]
        events[-1]["record"] = result
        journal.write_text("".join(json.dumps(event) + "\n" for event in events))
    audit = audit_tool_journal(output, **args)
    assert not audit["ok"], audit
