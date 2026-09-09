"""MON-8/MON-12: ordinary session requests reach both real simulator paths.

Opt-in engineering integration charges the ordinary development run ledger and
requires an open branch idea. Incomplete access observations MUST still exclude
the outer session; neither the fixture frontend nor episodes are study evidence.
"""

import hashlib
import json
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [
    pytest.mark.graph,
    pytest.mark.sim,
    pytest.mark.skipif(
        sys.platform != "darwin" or os.environ.get("AISLE_MATCHED_SESSION_SIM") != "1",
        reason="requires explicitly selected actual macOS session simulation",
    ),
]

FRONTEND = """
import json, time, uuid
from pathlib import Path
channel = Path.home() / 'tool-channel'
identity = uuid.uuid4().hex
request = {'schema_version':'aisle.matched-tool-request.v1',
           'id':identity, 'operation':'run'}
with (channel / (identity + '.request.json')).open('x') as stream:
    stream.write(json.dumps(request) + '\\n')
response = channel / (identity + '.response.json')
deadline = time.monotonic() + 1200
while time.monotonic() < deadline:
    if response.exists():
        raw = response.read_text()
        if raw.endswith('\\n'):
            result = json.loads(raw)
            assert result['request_id'] == identity
            print(json.dumps({'type':'item.completed','item':{
                'id':'fixture-response','type':'agent_message','text':json.dumps(result)}}))
            break
    time.sleep(.05)
else:
    raise TimeoutError('ordinary controller request did not finish')
"""


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_session_request_retains_real_simulation_without_claiming_confinement(
    tmp_path, monkeypatch, arm
):
    """MON-8/MON-11/MON-12/MON-13: run success cannot override missing access evidence."""
    pytest.importorskip("genesis")
    monkeypatch.syspath_prepend(str(ROOT / "tests/unit"))
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import numpy
    import pyarrow
    from matched_campaign import run_engineering_session
    from test_matched_session import _ambient_pair, _confinement_pair, prepared_pair
    from test_typed_validation_launch import _inputs

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.matched_session import admit_pair
    from aisle.harness.treatment_ambient import build_declared_environment
    from aisle.harness.treatment_confinement import SANDBOX_EXEC, compile_macos_profile
    from aisle.harness.worker_capability import audit_worker_capability

    base = tmp_path.resolve()
    _, candidates, views = prepared_pair(base)
    private = base / "private"
    private.mkdir()
    output = private / "session"
    output.mkdir()
    validator_root = private / "validator-inputs"
    validator_root.mkdir()
    validation = _inputs(validator_root, direct_python=True)
    packages = validator_root / "runtime-packages"
    for package in (numpy, pyarrow):
        shutil.copytree(
            Path(package.__file__).parent,
            packages / package.__name__,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    python = validation["python"]
    runtime = capture_runtime(validation["policy"].runtime_read_roots)
    storage = private / "snapshots"
    storage.mkdir()
    policy = replace(
        validation["policy"],
        visible_roots=(validation["bundle"], storage),
        hidden_roots=(ROOT, *views.values(), private / "evidence", private / "session"),
    )
    (private / "evidence").mkdir()
    compiled = compile_macos_profile(policy)
    validation["profile_path"].write_text(compiled.text)
    validator_capability = audit_worker_capability(
        policy=policy,
        profile_path=validation["profile_path"],
        python=python,
        environment=validation["environment"],
        environment_record=validation["environment_record"],
        output=private / "evidence/validator-capability",
    )
    assert validator_capability["capability_pass"], validator_capability
    binding = {
        key: str(validation[key]) if isinstance(validation[key], Path) else validation[key]
        for key in (
            "bundle",
            "bundle_manifest",
            "profile_path",
            "python",
            "python_sha256",
            "environment",
            "environment_record",
        )
    }
    binding.update(
        schema_version="aisle.typed-validation-binding.v1",
        snapshot_storage=str(storage),
        policy=policy.canonical_dict(),
        attestation=validator_capability,
    )
    confinement = _confinement_pair(base, ROOT, candidates, views)
    profiles, policies = {}, {}
    for selected in candidates:
        declared = confinement[selected]["policy"]
        from aisle.harness.treatment_confinement import MacOSPolicy

        selected_policy = MacOSPolicy(
            **{
                key: value if key == "network_policy" else tuple(Path(p) for p in value)
                for key, value in declared.items()
            }
        )
        selected_policy = replace(
            selected_policy,
            allowed_executables=(Path(python),),
            output_roots=(
                Path(confinement[selected]["scratch"]),
                *selected_policy.output_roots[:-1],
            ),
            hidden_roots=(*selected_policy.hidden_roots, private),
        )
        profiles[selected] = private / f"{selected}.sb"
        compiled = compile_macos_profile(selected_policy)
        profiles[selected].write_text(compiled.text)
        policies[selected] = selected_policy
        confinement[selected]["policy"] = selected_policy.canonical_dict()
        candidate = candidates[selected]
        candidate["agent"].update(
            cli_revision="stdlib-engineering-fixture", cli_binary_sha256=_sha(python)
        )
        candidate["confinement"].update(
            adapter_binary_sha256=_sha(SANDBOX_EXEC),
            profile_sha256=compiled.sha256,
            policy_sha256=compiled.policy_id,
        )
        candidate["policy"]["allowed_external_tools"] = ["harness.run"]
        candidate["runtime_binaries"].append(
            {"name": "harness-python", "sha256": _sha(sys.executable)}
        )
        # Actual typed collection took 891 seconds; reserve bounded headroom
        # for the same evidence checks under varying local filesystem load.
        candidate["budget"].update(tool_ceiling=1, tool_wall_ceiling_s=1200, wall_ceiling_s=1300)
    ambient = _ambient_pair(candidates, confinement)
    launches, controllers = {}, {}
    for selected in candidates:
        (Path(ambient[selected]["environment"]["HOME"]) / "tool-channel").mkdir()
        launches[selected] = {
            "argv": [str(python), "-I", "-B", "-c", FRONTEND, "system prompt", "research contract"],
            "system_prompt_arg": 5,
            "research_contract_arg": 6,
            "tool_python": sys.executable,
        }
        environment, record = build_declared_environment(
            private / f"{selected}-controller", source_env={"PATH": os.environ["PATH"]}
        )
        controllers[selected] = {
            "schema_version": "aisle.matched-run-controller.v1",
            "python": sys.executable,
            "python_sha256": _sha(sys.executable),
            "environment": environment,
            "environment_record": record,
        }
    development = {
        "schema_version": "aisle.matched-development.v1",
        "purpose": "expert_parity",
        "tier": "T1",
        "embodiment": "franka",
        "verifier": "oracle",
        "reset": "teleport",
        "seeds": [7],
        "run_ceiling": 1,
        "episode_ceiling": 1,
        "timeout_s": 600,
    }
    capability = audit_worker_capability(
        policy=policies[arm],
        profile_path=profiles[arm],
        python=python,
        environment=ambient[arm]["environment"],
        environment_record=ambient[arm]["record"],
        output=private / "evidence/frontend-capability",
    )
    assert capability["capability_pass"], capability
    plan = admit_pair(
        ROOT,
        candidates,
        views,
        confinement=confinement,
        ambient=ambient,
        launches=launches,
        development=development,
        tool_runtime=runtime,
        typed_validation=binding,
        run_controller=controllers,
    )
    access = private / "access.json"
    access.write_text(
        json.dumps(
            {
                "schema_version": "aisle.hidden-access-log.v1",
                "adapter_active": True,
                "complete": False,
                "events": [],
            }
        )
    )
    template = {"allocation_root": str(private / "allocation"), "timeout_s": 360}
    template.update(
        {"max_calls": 100000}
        if arm == "typed"
        else {"max_primitive_calls": 100000, "max_handles": 1000}
    )
    # Admission resolves the private path; the runner owns its fresh creation.
    output.rmdir()
    result = run_engineering_session(
        plan,
        ROOT,
        views,
        arm,
        output,
        session_id="sim-session-" + uuid4().hex,
        profile_path=profiles[arm],
        attestation=capability,
        hidden_access_log=access,
        worker_preparations=[{"provider": template}],
    )
    assert result["tool_audit"]["ok"], result
    assert result["tool_audit"]["reserved_runs"] == 1
    common = result["common_evidence"]
    assert len(common["runs"]["entries"]) == 1, result
    run = common["runs"]["entries"][0]
    assert run["ok"] and run["classification"] == "tool_result", run
    assert run["result"]["ok"], run
    assert run["result"]["campaign_purpose"] == "expert_parity", run
    assert len(run["collection"]["episodes"]) == 1, run
    work = run["collection"]["simulator_work"]
    assert work["status"] == "recomputed", work
    assert work["summary"]["completed_env_sim_ns"] > 0, work
    assert work["summary"]["producer_coverage_complete"] is False
    assert all(
        launch["schema_version"] == "aisle.simulator-work-journal.v3"
        and launch["completed"]["physics_step"] > 0
        for launch in work["summary"]["launches"]
    ), work
    assert result["ok"] is False
    assert result["error"] == "postflight integrity checks failed", result
    assert result["postflight"]["exclusion_reasons"] == ["hidden_access_log_incomplete"]
    assert result["eligible_for_estimate"] is False
    assert common["complete"] is False
