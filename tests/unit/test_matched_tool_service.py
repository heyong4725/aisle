"""MON-8/MON-12/MON-13: requests cross a process boundary into one controller."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_matched_tools import _controller

pytestmark = pytest.mark.unit


def _client(channel):
    code = (
        "from aisle.harness.matched_tool_service import request_check; import json,sys; "
        "print(json.dumps(request_check(sys.argv[1], timeout_s=15)))"
    )
    return subprocess.run(
        [sys.executable, "-c", code, str(channel)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_live_requests_share_one_controller_budget_and_journal(tmp_path, arm):
    """MON-8/MON-12: real client processes cannot reset the session budget between calls."""
    from aisle.harness.matched_tool_service import ToolService

    controller, _, output = _controller(tmp_path, arm)
    home = Path(controller.plan["ambient_bindings"][arm]["environment"]["HOME"])
    channel = home / "tool-channel"
    channel.mkdir()
    with ToolService(controller):
        first = _client(channel)
        assert first.returncode == 0, first.stdout + first.stderr
        assert json.loads(first.stdout)["classification"] == "tool_result"
        second = _client(channel)
        assert second.returncode == 0, second.stdout + second.stderr
        assert "budget" in json.loads(second.stdout)["error"]
    events = [json.loads(line) for line in (output / "tool-events.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["started", "finished", "started", "finished"]
    report = json.loads((output / "tool-service.json").read_text())
    assert report["ok"] is True
    assert report["processed_requests"] == 2
    assert report["session_id"] == controller.session_id


def test_request_symlink_cannot_make_controller_read_a_foreign_file(tmp_path):
    """MON-13: participant request entries must be regular files in the bound channel."""
    from aisle.harness.matched_tool_service import ToolService

    controller, _, output = _controller(tmp_path, "typed")
    home = Path(controller.plan["ambient_bindings"]["typed"]["environment"]["HOME"])
    channel = home / "tool-channel"
    channel.mkdir()
    foreign = tmp_path / "foreign.json"
    foreign.write_text('{"operation":"check"}')
    service = ToolService(controller)
    with service:
        (channel / ("a" * 32 + ".request.json")).symlink_to(foreign)
        assert service.failed.wait(5)
    report = json.loads((output / "tool-service.json").read_text())
    assert report["ok"] is False
    assert controller.attempts == 0
    assert report["error"]


@pytest.mark.parametrize("field", ["schema_version", "id", "operation"])
def test_duplicate_request_fields_are_refused_before_execution(tmp_path, field):
    """MON-12/MON-13: ambiguous request fields cannot select a controller operation."""
    from aisle.harness.matched_tool_service import ToolService

    controller, _, output = _controller(tmp_path, "typed")
    channel = (
        Path(controller.plan["ambient_bindings"]["typed"]["environment"]["HOME"]) / "tool-channel"
    )
    channel.mkdir()
    request_id = "c" * 32
    request = {
        "schema_version": "aisle.matched-tool-request.v1",
        "id": request_id,
        "operation": "check",
    }
    raw = ("{" + json.dumps(field) + ':"conflicting",' + json.dumps(request)[1:] + "\n").encode()
    service = ToolService(controller)
    with service:
        (channel / f"{request_id}.request.json").write_bytes(raw)
        assert service.failed.wait(5), "ambiguous request was not refused"
    assert controller.attempts == 0
    assert (output / f"request-{request_id}.json").read_bytes() == raw
    assert "duplicate" in service.report["error"]
    assert service.report["processed_requests"] == 0


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_session_runner_hosts_requests_for_the_live_child(tmp_path, arm):
    """MON-8/MON-12: the actual agent-process runner hosts one tool service for its lifetime.

    The child and confinement adapter are explicit engineering fixtures, not
    coding-agent results or external confinement evidence.
    """
    import copy
    import hashlib
    import shlex

    from test_treatment_confinement import _attestation

    from aisle.harness.matched_session import admit_pair
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    from matched_campaign import run_engineering_session

    controller, views, _ = _controller(tmp_path, arm)
    candidates = copy.deepcopy(controller.plan["arms"])
    bindings = copy.deepcopy(controller.plan["confinement_bindings"])
    ambient = controller.plan["ambient_bindings"]
    fixture = tmp_path / "fixture-agent"
    code = (
        "from pathlib import Path; from aisle.harness.matched_tool_service import request_check; "
        "import json; c=Path.home()/'tool-channel'; "
        "a=request_check(c,timeout_s=15); b=request_check(c,timeout_s=15); "
        "response={'first':a['classification'],'second':b['error']}; "
        "print(json.dumps({'type':'item.completed','item':{'id':'fixture-response',"
        "'type':'agent_message','text':json.dumps(response)}}))"
    )
    fixture.write_text(
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -c " + shlex.quote(code) + "\n"
    )
    fixture.chmod(0o755)
    launches = {}
    compiled_by_arm = {}
    for name, candidate in candidates.items():
        candidate.pop("immutable_id")
        candidate["repository"].pop("visible_files")
        candidate["agent"]["cli_binary_sha256"] = hashlib.sha256(fixture.read_bytes()).hexdigest()
        candidate["budget"]["wall_ceiling_s"] = 20
        launches[name] = {
            "argv": [str(fixture), "system prompt", "research contract"],
            "system_prompt_arg": 1,
            "research_contract_arg": 2,
            "tool_python": sys.executable,
        }
        (Path(ambient[name]["environment"]["HOME"]) / "tool-channel").mkdir()
        bindings[name]["policy"]["allowed_executables"].append(str(fixture))
        declared = bindings[name]["policy"]
        policy = MacOSPolicy(
            **{
                key: value if key == "network_policy" else tuple(Path(p) for p in value)
                for key, value in declared.items()
            }
        )
        compiled = compile_macos_profile(policy)
        compiled_by_arm[name] = compiled
        candidate["confinement"]["profile_sha256"] = compiled.sha256
        candidate["confinement"]["policy_sha256"] = compiled.policy_id
    plan = admit_pair(
        controller.root, candidates, views, confinement=bindings, ambient=ambient, launches=launches
    )
    profile = tmp_path / "live-profile.sb"
    profile.write_text(compiled_by_arm[arm].text)
    attestation = _attestation(
        compiled_by_arm[arm], profile, Path(controller.attestation["adapter"]["path"])
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
    output = tmp_path / "live-attempt"
    result = run_engineering_session(
        plan,
        controller.root,
        views,
        arm,
        output,
        session_id="live-fixture",
        profile_path=profile,
        attestation=attestation,
        hidden_access_log=access,
    )
    assert result["ok"] is True, result
    event = json.loads((output / "session.jsonl").read_text())
    assert event["type"] == "item.completed"
    assert event["item"]["type"] == "agent_message"
    response = json.loads(event["item"]["text"])
    assert response["first"] == "tool_result"
    assert "budget" in response["second"]
    assert result["artifacts"]["tool-events.jsonl"]
    assert result["artifacts"]["tool-service.json"]
    assert result["tool_audit"]["ok"] is True
    assert result["tool_audit"]["service_verified"] is True
    assert result["common_evidence"]["budgets"]["observed"]["tool_calls"] is None
    assert result["common_evidence"]["budgets"]["observed"]["controller_tool_requests"] == 2
    assert result["common_evidence"]["budgets"]["observed"]["controller_tool_processes"] == 1
    assert result["artifacts"]["tool-000001/attempt.json"]
    assert json.loads((output / "tool-service.json").read_text())["processed_requests"] == 2


def test_response_write_failure_keeps_request_to_attempt_link(tmp_path):
    """MON-12/MON-13: a participant cannot erase invocation provenance by blocking its response."""
    from aisle.harness.matched_tool_service import ToolService

    controller, _, output = _controller(tmp_path, "monolithic")
    home = Path(controller.plan["ambient_bindings"]["monolithic"]["environment"]["HOME"])
    channel = home / "tool-channel"
    channel.mkdir()
    foreign = tmp_path / "foreign.txt"
    foreign.write_text("unchanged")
    request_id = "b" * 32
    service = ToolService(controller)
    with service:
        (channel / f"{request_id}.response.json").symlink_to(foreign)
        (channel / f"{request_id}.request.json").write_text(
            json.dumps(
                {
                    "schema_version": "aisle.matched-tool-request.v1",
                    "id": request_id,
                    "operation": "check",
                }
            )
            + "\n"
        )
        assert service.failed.wait(10)
    assert foreign.read_text() == "unchanged"
    index = [
        json.loads(line) for line in (output / "tool-request-index.jsonl").read_text().splitlines()
    ]
    assert len(index) == 1
    assert index[0]["request_id"] == request_id
    assert (
        index[0]["attempt_id"]
        == json.loads((output / "tool-000001/attempt.json").read_text())["immutable_id"]
    )
    report = json.loads((output / "tool-service.json").read_text())
    assert report["processed_requests"] == 1
    assert report["ok"] is False


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_live_run_requests_retain_bound_launcher_refusals(tmp_path, arm):
    """MON-8/MON-12: a live run request supplies no seeds, paths, limits or gate overrides."""
    from test_matched_session import _development_protocol

    from aisle.harness.matched_evidence import audit_tool_journal
    from aisle.harness.matched_tool_service import ToolService

    controller, views, output = _controller(tmp_path, arm, development=_development_protocol())
    name = "graphs/expert_t1.yaml" if arm == "typed" else "experts/monolithic/expert_t1.py"
    (views[arm] / name).write_text(
        "nodes: [broken YAML" if arm == "typed" else "invalid python : :"
    )
    channel = Path(controller.plan["ambient_bindings"][arm]["environment"]["HOME"]) / "tool-channel"
    channel.mkdir()
    code = (
        "from aisle.harness.matched_tool_service import request_run; import json,sys; "
        "print(json.dumps(request_run(sys.argv[1],timeout_s=15)))"
    )
    with ToolService(controller):
        result = subprocess.run(
            [sys.executable, "-c", code, str(channel)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        response = json.loads(result.stdout)
        assert response["classification"] == "tool_result"
        assert response["result"]["ok"] is False
    audit = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm=arm,
        development=controller.plan["development"],
    )
    assert audit["ok"] is True, audit
    assert audit["service_verified"] is True


@pytest.mark.parametrize("drift", ["seen_count", "boolean_count", "error", "extra_request"])
def test_service_audit_rejects_unaccounted_or_inconsistent_requests(tmp_path, drift):
    """MON-12/MON-13: verified request coverage needs exact retained inventory and counts."""
    from aisle.harness.matched_evidence import audit_tool_journal
    from aisle.harness.matched_tool_service import ToolService

    controller, _, output = _controller(tmp_path, "typed")
    channel = (
        Path(controller.plan["ambient_bindings"]["typed"]["environment"]["HOME"]) / "tool-channel"
    )
    channel.mkdir()
    with ToolService(controller):
        result = _client(channel)
        assert result.returncode == 0, result.stderr

    def audit():
        return audit_tool_journal(
            output,
            session_id=controller.session_id,
            plan_id=controller.plan["immutable_id"],
            arm="typed",
        )

    assert audit()["ok"]
    path = output / "tool-service.json"
    record = json.loads(path.read_text())
    if drift == "seen_count":
        record["seen_requests"] += 1
    elif drift == "boolean_count":
        record["processed_requests"] = True
    elif drift == "error":
        record["error"] = "unaccounted service failure"
    else:
        (output / ("request-" + "f" * 32 + ".json")).write_text("{}\n")
    path.write_text(json.dumps(record))
    checked = audit()
    assert not checked["ok"], checked
    assert not checked["service_verified"]
