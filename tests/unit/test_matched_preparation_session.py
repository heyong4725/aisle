"""MON-8/MON-12: provisioned preparation crosses the complete engineering session runner."""

import copy
import hashlib
import json
import shlex
import shutil
import sys
from pathlib import Path

import pytest
from test_matched_worker_journal import _worker_inputs
from test_treatment_confinement import _attestation

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("selected_arm", ["typed", "monolithic"])
def test_session_runner_provisions_real_arm_precheck(tmp_path, selected_arm):
    """MON-8/MON-12: session, frontend, preparation, real worker and final audit share identity.

    The fixture frontend and adapter prove process integration, not coding-agent
    performance, actual OS confinement, or independent study prerequisites.
    """
    from aisle.harness.matched_session import admit_pair
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    from matched_campaign import run_engineering_session

    if selected_arm == "monolithic":
        controller, views, output, _, worker = _worker_inputs(tmp_path)
        declaration = worker["launch"]
        declaration.pop("bundle_manifest")
        declaration.pop("source_roots")
        bundle = Path(declaration["bundle"])
        shutil.rmtree(bundle)
        bundle.mkdir()
    else:
        from test_typed_run_prepare import _controller, _workers

        controller, views, output, validation = _controller(tmp_path)
        declaration, _ = _workers(tmp_path, controller, views, output, validation)
    (output / "tool-events.jsonl").unlink()
    fixture = tmp_path / "fixture-agent"
    code = (
        "from pathlib import Path; import json; "
        "from aisle.harness.matched_tool_service import request_run; "
        + (
            "(Path.cwd()/'registry/manifests/segmented-pose.yaml')"
            ".write_text('broken: declaration'); "
            if selected_arm == "typed"
            else ""
        )
        + "response = request_run(Path.home()/'tool-channel',timeout_s=45); "
        + "print(json.dumps({'type':'item.completed','item':{'id':'fixture-response',"
        "'type':'agent_message','text':json.dumps(response)}}))"
    )
    fixture.write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -c " + shlex.quote(code) + "\n"
    )
    fixture.chmod(0o755)
    candidates = copy.deepcopy(controller.plan["arms"])
    bindings = copy.deepcopy(controller.plan["confinement_bindings"])
    ambient = controller.plan["ambient_bindings"]
    launches, compiled_by_arm = {}, {}
    for arm, candidate in candidates.items():
        candidate.pop("immutable_id")
        candidate["repository"].pop("visible_files")
        candidate["agent"]["cli_binary_sha256"] = hashlib.sha256(fixture.read_bytes()).hexdigest()
        candidate["budget"]["wall_ceiling_s"] = 60
        launches[arm] = {
            "argv": [str(fixture), "system prompt", "research contract"],
            "system_prompt_arg": 1,
            "research_contract_arg": 2,
            "tool_python": sys.executable,
        }
        (Path(ambient[arm]["environment"]["HOME"]) / "tool-channel").mkdir()
        bindings[arm]["policy"]["allowed_executables"].append(str(fixture))
        policy = MacOSPolicy(
            **{
                key: value if key == "network_policy" else tuple(Path(p) for p in value)
                for key, value in bindings[arm]["policy"].items()
            }
        )
        compiled = compile_macos_profile(policy)
        compiled_by_arm[arm] = compiled
        candidate["confinement"]["profile_sha256"] = compiled.sha256
        candidate["confinement"]["policy_sha256"] = compiled.policy_id
    plan = admit_pair(
        controller.root,
        candidates,
        views,
        confinement=bindings,
        ambient=ambient,
        launches=launches,
        development=controller.plan["development"],
        tool_runtime=controller.plan["tool_runtime"],
        run_controller=controller.plan["run_controller"],
        typed_validation=controller.plan.get("typed_validation"),
    )
    # Profile admission resolves hidden paths. Release the empty reservation
    # afterward so execute_session can create its fresh evidence directory.
    output.rmdir()
    profile = tmp_path / "live-profile.sb"
    profile.write_text(compiled_by_arm[selected_arm].text)
    attestation = _attestation(
        compiled_by_arm[selected_arm], profile, Path(controller.attestation["adapter"]["path"])
    )
    access = tmp_path / "access.json"
    access.write_text(
        json.dumps(
            {
                "schema_version": "aisle.hidden-access-log.v1",
                "adapter_active": True,
                "complete": True,
                "events": [],
            }
        )
    )
    result = run_engineering_session(
        plan,
        controller.root,
        views,
        selected_arm,
        output,
        session_id="prepared-session",
        profile_path=profile,
        attestation=attestation,
        hidden_access_log=access,
        worker_preparations=[declaration],
    )
    assert result["ok"], result
    event = json.loads((output / "session.jsonl").read_text())
    assert event["type"] == "item.completed"
    assert event["item"]["type"] == "agent_message"
    response = json.loads(event["item"]["text"])
    assert response["classification"] == "tool_result", response
    assert not response["ok"]
    if selected_arm == "monolithic":
        assert "authored failure" in response["result"]["error"]
        assert response["result"]["worker_evidence"]["ok"]
    else:
        assert response["result"]["errors"]
    assert result["tool_audit"]["ok"]
    assert result["tool_audit"]["service_verified"]
    assert result["tool_audit"]["reserved_runs"] == 1
    attempt = json.loads((output / "tool-000001/attempt.json").read_text())
    assert attempt["session_id"] == "prepared-session"
    assert attempt["plan_id"] == plan["immutable_id"]
    assert attempt["worker_preparation"]["index"] == 0
