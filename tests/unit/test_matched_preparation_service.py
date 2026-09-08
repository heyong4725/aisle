"""MON-8/MON-12: ordinary frontend run requests use controller-owned preparation inputs."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_frontend_run_request_uses_private_current_source_preparation(tmp_path, arm):
    """MON-8/MON-12: a parameter-free child request reaches real arm preparation and audit."""
    from aisle.harness.matched_evidence import audit_tool_journal
    from aisle.harness.matched_tool_service import ToolService
    from aisle.harness.matched_tools import ToolController

    if arm == "monolithic":
        from test_matched_worker_journal import _worker_inputs

        old, views, output, _, worker = _worker_inputs(tmp_path, authored_failure=False)
        declaration = worker["launch"]
        declaration.pop("bundle_manifest")
        declaration.pop("source_roots")
        bundle = Path(declaration["bundle"])
        shutil.rmtree(bundle)
        bundle.mkdir()
    else:
        from test_typed_run_prepare import _controller, _workers

        old, views, output, validation = _controller(tmp_path)
        declaration, _ = _workers(tmp_path, old, views, output, validation)
    (output / "tool-events.jsonl").unlink()
    supplied = [declaration]
    controller = ToolController(
        old.plan,
        old.root,
        views,
        arm,
        output,
        session_id=old.session_id,
        python=old.python,
        profile_path=old.profile_path,
        attestation=old.attestation,
        worker_preparations=supplied,
    )
    supplied.clear()
    if arm == "typed":
        (views["typed"] / "registry/manifests/segmented-pose.yaml").write_text(
            "broken: declaration"
        )
    else:
        (views["monolithic"] / "experts/monolithic/expert_t1.py").write_text(
            "API_VERSION='1.0'\nraise ValueError('authored failure')\n"
        )
    channel = Path(controller.plan["ambient_bindings"][arm]["environment"]["HOME"]) / "tool-channel"
    channel.mkdir()
    code = (
        "from aisle.harness.matched_tool_service import request_run; import json,sys; "
        "print(json.dumps(request_run(sys.argv[1],timeout_s=45)))"
    )
    with ToolService(controller):
        child = subprocess.run(
            [sys.executable, "-c", code, str(channel)],
            capture_output=True,
            text=True,
            timeout=50,
        )
        assert child.returncode == 0, child.stderr
        response = json.loads(child.stdout)
        assert response["classification"] == "tool_result", response
        assert not response["ok"]
    attempt = json.loads((output / "tool-000001/attempt.json").read_text())
    assert attempt["worker_preparation"]["index"] == 0
    assert attempt["artifacts"]["worker-declaration.json"]
    assert json.loads((output / "tool-000001/worker-declaration.json").read_text()) == declaration
    audit = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm=arm,
        development=controller.plan["development"],
    )
    assert audit["ok"], audit
    assert audit["service_verified"]


def test_empty_preparation_list_refuses_without_legacy_execution(tmp_path):
    """MON-8/MON-13: explicit missing preparation cannot fall through to a legacy run."""
    from test_matched_session import _development_protocol
    from test_matched_tools import _controller

    from aisle.harness.matched_tools import ToolController

    old, views, output = _controller(tmp_path, "monolithic", _development_protocol())
    (output / "tool-events.jsonl").unlink()
    controller = ToolController(
        old.plan,
        old.root,
        views,
        old.arm,
        output,
        session_id=old.session_id,
        python=old.python,
        profile_path=old.profile_path,
        attestation=old.attestation,
        worker_preparations=[],
    )
    result = controller.run()
    assert result["classification"] == "infrastructure_exclusion"
    assert result["process"] is None
    assert "no worker preparation remains" in result["error"]
    assert result["reservation"]["runs"] == 1


def test_preparations_follow_reserved_run_order_and_cannot_be_reused(tmp_path, monkeypatch):
    """MON-11/MON-12: reservations consume distinct private inputs even when preparation fails."""
    from test_matched_session import _development_protocol
    from test_matched_tools import _controller

    from aisle.harness.matched_evidence import audit_tool_journal
    from aisle.harness.matched_tools import ToolController

    development = _development_protocol()
    development.update(run_ceiling=3, episode_ceiling=3)
    old, views, output = _controller(tmp_path, "monolithic", development)
    (output / "tool-events.jsonl").unlink()
    controller = ToolController(
        old.plan,
        old.root,
        views,
        old.arm,
        output,
        session_id=old.session_id,
        python=old.python,
        profile_path=old.profile_path,
        attestation=old.attestation,
        worker_preparations=[{"fixture": "first"}, {"fixture": "second"}],
    )
    selected = []

    def prepare(current, destination, record, declaration):
        selected.append(declaration)
        record["error"] = "fixture stops at preparation"
        return None

    monkeypatch.setattr(controller, "_prepare_monolithic_run", prepare)
    first, second, third = (controller.run() for _ in range(3))
    assert selected == [{"fixture": "first"}, {"fixture": "second"}]
    assert first["worker_preparation"]["index"] == 0
    assert second["worker_preparation"]["index"] == 1
    assert "no worker preparation remains" in third["error"]
    assert all(record["process"] is None for record in (first, second, third))
    audit = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm="monolithic",
        development=development,
    )
    assert audit["ok"], audit
    assert audit["reserved_runs"] == 3
