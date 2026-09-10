"""MON-8/MON-12/MON-13: controller-observed calls to real arm tools."""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from test_matched_session import _ambient_pair, _confinement_pair, prepared_pair
from test_treatment_confinement import _attestation

pytestmark = pytest.mark.unit


def _controller(tmp_path, arm, development=None, *, model=None):
    from aisle.harness.matched_session import admit_pair
    from aisle.harness.matched_tools import ToolController
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile

    root, candidates, views = prepared_pair(tmp_path)
    if model is not None:
        for candidate in candidates.values():
            candidate["model"]["requested_identity"] = model
    bindings = _confinement_pair(tmp_path, root, candidates, views)
    ambient = _ambient_pair(candidates, bindings)
    adapter = tmp_path / "synthetic-adapter"
    adapter.write_text('#!/bin/sh\nshift 2\nexec "$@"\n')
    adapter.chmod(0o755)
    for candidate in candidates.values():
        candidate["confinement"]["adapter_binary_sha256"] = hashlib.sha256(
            adapter.read_bytes()
        ).hexdigest()
        candidate["runtime_binaries"].append(
            {
                "name": "harness-python",
                "sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            }
        )
        candidate["policy"]["allowed_external_tools"].append("harness.check")
        candidate["budget"].update({"tool_ceiling": 1, "tool_wall_ceiling_s": 30})
        if development is not None:
            candidate["policy"]["allowed_external_tools"].append("harness.run")
            candidate["budget"]["tool_ceiling"] = 3
    declared = bindings[arm]["policy"]
    policy = MacOSPolicy(
        **{
            key: value if key == "network_policy" else tuple(Path(p) for p in value)
            for key, value in declared.items()
        }
    )
    compiled = compile_macos_profile(policy)
    profile = tmp_path / "tool.sb"
    profile.write_text(compiled.text)
    attestation = _attestation(compiled, profile, adapter)
    plan = admit_pair(
        root, candidates, views, confinement=bindings, ambient=ambient, development=development
    )
    output = tmp_path / "tool-evidence"
    output.mkdir()
    return (
        ToolController(
            plan,
            root,
            views,
            arm,
            output,
            session_id="tool-fixture",
            python=Path(sys.executable),
            profile_path=profile,
            attestation=attestation,
        ),
        views,
        output,
    )


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_actual_arm_check_has_retained_controller_attempt(tmp_path, arm):
    """MON-12: a real harness check process retains its verdict and controller journal.

    The adapter is explicitly synthetic; this proves tool integration, not
    actual agent confinement or expert parity.
    """
    controller, views, output = _controller(tmp_path, arm)
    if arm == "monolithic":
        (views[arm] / "experts/monolithic/expert_t1.py").write_text("invalid python : :")
    record = controller.check()
    assert record["classification"] == "tool_result", record
    assert type(record["result"]["ok"]) is bool
    if arm == "monolithic":
        assert record["result"]["ok"] is False
        assert "SyntaxError" in record["result"]["error"]
    assert record["session_id"] == "tool-fixture"
    assert record["operation"] == "check"
    assert record["arm"] == arm
    assert record["artifacts"]["stdout.json"]
    events = [json.loads(line) for line in (output / "tool-events.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == ["started", "finished"]
    assert events[-1]["record"] == record
    refused = controller.check()
    assert refused["classification"] == "infrastructure_exclusion"
    assert "budget" in refused["error"]
    assert refused["process"] is None


def test_check_cli_invokes_registered_arm_tool(tmp_path):
    """CON-8/MON-12: controller check CLI emits the real tool verdict with retained identity."""
    import subprocess

    controller, _, _ = _controller(tmp_path, "monolithic")
    request = tmp_path / "check-request.json"
    request.write_text(
        json.dumps(
            {
                "root": str(controller.root),
                "visible_roots": {arm: str(view) for arm, view in controller.views.items()},
                "plan": controller.plan,
                "python": str(controller.python),
                "profile_path": str(controller.profile_path),
                "attestation": controller.attestation,
            }
        )
    )
    output = tmp_path / "cli-tool-evidence"
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "tools/matched_campaign.py"),
            "check",
            "--request",
            str(request),
            "--output",
            str(output),
            "--arm",
            "monolithic",
            "--session-id",
            "cli-check-fixture",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    record = json.loads(result.stdout)
    assert record["classification"] == "tool_result", record
    assert record["result"]["ok"] is True
    assert record["ok"] is True and result.returncode == 0
    assert record["eligible_for_estimate"] is False
    assert (output / "tool-events.jsonl").is_file()


def test_expired_tool_budget_never_starts_a_child(tmp_path):
    """MON-8/MON-12: preflight time counts against the bound tool wall budget."""
    controller, _, _ = _controller(tmp_path, "monolithic")
    ticks = iter([0.0, 31.0, 31.0])
    controller.clock = lambda: next(ticks)
    result = controller.check()
    assert result["classification"] == "infrastructure_exclusion"
    assert result["process"] is None
    assert "wall budget" in result["error"]


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_registered_run_uses_bound_parameters_and_preserves_real_refusal(tmp_path, arm):
    """MON-8/MON-12: real arm launchers receive fixed development inputs and retain refusals."""
    from test_matched_session import _development_protocol

    from aisle.harness.matched_session import _digest

    development = _development_protocol()
    controller, views, output = _controller(tmp_path, arm, development=development)
    if arm == "typed":
        (views[arm] / "graphs/expert_t1.yaml").write_text("nodes: [broken YAML")
    else:
        (views[arm] / "experts/monolithic/expert_t1.py").write_text("invalid python : :")
    record = controller.run()
    assert record["classification"] == "tool_result", record
    assert record["result"]["ok"] is False
    assert record["operation"] == "run"
    assert record["development_id"] == _digest(development)
    assert record["run_id"].startswith("matched-")
    command = json.loads((output / "tool-000001/invocation.json").read_text())["argv"]
    assert command[command.index("--seeds") + 1] == "7"
    assert command[command.index("--episodes") + 1] == "1"
    assert "--no-idea-gate" not in command
    assert "--allow-unproven" not in command
    assert record["run_evidence"] is None
    refused = controller.run()
    assert refused["process"] is None
    assert "run budget" in refused["error"]


@pytest.mark.parametrize("failure", ["crash", "timeout", "wait_error", "cancelled"])
def test_failed_run_retains_partial_files_before_finalizing(tmp_path, monkeypatch, failure):
    """MON-12/MON-13: real failing fixture processes retain partial run evidence.

    CON-5: inject the wait outcome instead of racing a child sleep against
    the tool deadline. The child is real, so cleanup must still stop and reap it.
    This substitutes a controlled subprocess for simulation, testing failure
    retention only; it is not successful simulation or confinement evidence.
    """
    import subprocess

    from test_matched_session import _development_protocol

    from aisle.harness import matched_tools
    from aisle.harness.matched_evidence import audit_tool_journal

    development = _development_protocol()
    development["timeout_s"] = 1
    controller, _, output = _controller(tmp_path, "monolithic", development=development)

    children = []

    def spawn(command, **kwargs):
        run_id = command[command.index("--run-id") + 1]
        source = controller.root / "runs" / run_id
        source.mkdir(parents=True)
        (source / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "seeds": [7],
                    "tier": "T1",
                    "verifier": "oracle",
                }
            )
        )
        (source / "partial.log").write_text("fixture began episode\n")
        code = "raise SystemExit(3)" if failure == "crash" else "import time; time.sleep(30)"
        child = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.DEVNULL,
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            start_new_session=True,
        )
        children.append(child)
        if failure in {"timeout", "wait_error", "cancelled"}:
            real_wait = child.wait
            interrupted = False

            def wait(*args, **kwargs):
                nonlocal interrupted
                if not interrupted:
                    interrupted = True
                    assert child.poll() is None, "failure fixture must begin with a live child"
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(child.args, kwargs["timeout"])
                    if failure == "cancelled":
                        raise KeyboardInterrupt("fixture cancellation")
                    raise OSError("fixture wait failure")
                return real_wait(*args, **kwargs)

            child.wait = wait
        return child

    monkeypatch.setattr(matched_tools, "spawn_isolated_process", spawn)
    try:
        if failure == "cancelled":
            with pytest.raises(KeyboardInterrupt, match="fixture cancellation"):
                controller.run()
            record = json.loads((output / "tool-000001/attempt.json").read_text())
        else:
            record = controller.run()
        assert children[0].poll() is not None, "attempt finalized with a live child"
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()
    assert record["classification"] == "infrastructure_exclusion"
    assert record["process"]["timed_out"] is (failure == "timeout")
    assert record["run_evidence"] is not None
    assert record["run_evidence"]["ok"] is False  # Episode stream was never completed.
    assert (output / "tool-000001/run/raw/partial.log").read_text() == "fixture began episode\n"
    report = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm=controller.arm,
        development=development,
    )
    assert report["ok"] is True, report
    assert report["exclusions"]
    assert report["files"]["tool-000001/run/raw/partial.log"]


@pytest.mark.parametrize("redirect", ["parent", "dangling_run"])
def test_run_refuses_redirected_output_before_launch(tmp_path, monkeypatch, redirect):
    """MON-8/MON-13: run outputs cannot redirect writes outside controller authority."""
    from test_matched_session import _development_protocol

    from aisle.harness.matched_session import _digest

    controller, _, _ = _controller(tmp_path, "typed", development=_development_protocol())
    runs = controller.root / "runs"
    target = tmp_path / "outside-runs"
    target.mkdir()
    if redirect == "parent":
        runs.symlink_to(target, target_is_directory=True)
    else:
        runs.mkdir()
        run_id = (
            "matched-"
            + _digest(
                {
                    "session": controller.session_id,
                    "plan": controller.plan["immutable_id"],
                    "arm": controller.arm,
                    "attempt": 1,
                }
            ).split(":")[1][:24]
        )
        (runs / run_id).symlink_to(target / "missing", target_is_directory=True)

    launched = []

    def forbidden_spawn(*args, **kwargs):
        launched.append(args)
        raise AssertionError("redirected output reached process launch")

    monkeypatch.setattr("aisle.harness.matched_tools.spawn_isolated_process", forbidden_spawn)
    record = controller.run()
    assert record["classification"] == "infrastructure_exclusion"
    assert record["process"] is None
    assert not launched
    assert record["reservation"] == {"runs": 0, "episodes": 0}
    assert "run" in record["error"]
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("rollout_timeout", [5, 100])
def test_direct_run_keeps_setup_and_collection_inside_tool_deadline(
    tmp_path, monkeypatch, arm, rollout_timeout
):
    """MON-8/MON-12: direct arm launches preserve the same two nested budget meanings."""
    from test_matched_session import _development_protocol

    from aisle.harness import matched_tools

    development = _development_protocol()
    development["timeout_s"] = rollout_timeout
    controller, _, _ = _controller(tmp_path, arm, development)
    controller.wall_spent = 3.0
    controller.clock = lambda: 12.0
    waits = []
    commands = []

    class Process:
        def wait(self, timeout):
            waits.append(timeout)
            return 1

    def spawn(command, **kwargs):
        commands.append(command)
        kwargs["stdout"].write(b'{"ok": false, "refused": {"gate": "fixture"}}')
        kwargs["stdout"].flush()
        return Process()

    monkeypatch.setattr(matched_tools, "spawn_isolated_process", spawn)
    result = controller.run()
    assert result["process"] == {"rc": 1, "timed_out": False}, result
    assert waits == [27.0]
    assert len(commands) == 1
    argv = commands[0]
    assert argv[argv.index("--timeout-s") + 1] == str(rollout_timeout)
