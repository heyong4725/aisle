"""MON-8/MON-12/MON-13: trusted run children use bound identities and private ambient state."""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


def _inputs(tmp_path, arm="typed"):
    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.treatment_ambient import build_declared_environment

    view = tmp_path / "participant"
    view.mkdir()
    environment, environment_record = build_declared_environment(
        tmp_path / "controller-home", source_env={}
    )
    python = Path(sys.executable)
    binding = {
        "schema_version": "aisle.matched-run-controller.v1",
        "python": str(python),
        "python_sha256": hashlib.sha256(python.read_bytes()).hexdigest(),
        "environment": environment,
        "environment_record": environment_record,
    }
    runtime = capture_runtime([python.resolve().parent.parent])
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
        "timeout_s": 30,
    }
    expected = {
        "session_id": "fixture",
        "plan_id": "sha256:" + "a" * 64,
        "run_id": "matched-fixture",
        "arm": arm,
        "controller_root": str(ROOT),
        "participant_root": str(view),
        "development": development,
        "runtime_record": runtime,
        "worker_adapter_sha256": "b" * 64,
    }
    # The real child rejects absent prepared worker inputs, before graph execution.
    config = {
        **expected,
        "schema_version": "aisle.matched-run-config.v1",
        "purpose": "expert_parity",
        "launch": {},
    }
    path = tmp_path / "run.json"
    path.write_text(json.dumps(config))
    return dict(
        binding=binding,
        runtime_record=runtime,
        source_roots=[ROOT, view],
        participant_policies=[
            {"visible_roots": [str(view)], "output_roots": [str(view)], "runtime_read_roots": []}
        ],
        config_path=path,
        config_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        expected=expected,
        output=tmp_path / "evidence",
        timeout_s=20,
    )


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_actual_run_child_retains_json_refusal_and_exact_launch(tmp_path, arm):
    """MON-12: both actual entry paths retain child output and immutable launch inputs."""
    from aisle.harness.matched_run_launch import launch_configured_run

    inputs = _inputs(tmp_path, arm)
    result = launch_configured_run(**inputs)
    assert result["process"]["rc"] == 1, result
    assert result["result"]["infrastructure_invalid"]
    assert not result["ok"]
    invocation = json.loads((inputs["output"] / "invocation.json").read_text())
    assert invocation["argv"][1:3] == ["-I", "-B"]
    assert invocation["environment_record"] == inputs["binding"]["environment_record"]
    assert (inputs["output"] / "stdout.json").is_file()


@pytest.mark.parametrize("mutation", ["session", "arm", "adapter", "interpreter", "ambient"])
def test_run_binding_refuses_drift_before_child_creation(tmp_path, monkeypatch, mutation):
    """MON-13: configuration identity and controller authority cannot drift across the boundary."""
    from aisle.harness import matched_run_launch

    inputs = _inputs(tmp_path)
    if mutation == "session":
        inputs["expected"]["session_id"] = "another-session"
    elif mutation == "arm":
        inputs["expected"]["arm"] = "monolithic"
    elif mutation == "adapter":
        inputs["expected"]["worker_adapter_sha256"] = "c" * 64
    elif mutation == "interpreter":
        inputs["binding"]["python_sha256"] = "0" * 64
    else:
        inputs["binding"]["environment"]["UNDECLARED"] = "value"
    monkeypatch.setattr(
        matched_run_launch, "spawn_isolated_process", lambda *a, **kw: pytest.fail("child started")
    )
    result = matched_run_launch.launch_configured_run(**inputs)
    assert not result["ok"]
    assert result["process"] is None
    assert result["error"]


def test_controller_home_cannot_be_participant_readable(tmp_path):
    """MON-8/MON-13: trusted controller state is distinct from participant scratch and views."""
    from aisle.harness.matched_run_launch import verify_controller_binding

    inputs = _inputs(tmp_path)
    inputs["participant_policies"][0]["visible_roots"].append(
        inputs["binding"]["environment_record"]["home"]
    )
    with pytest.raises(ValueError, match="participant"):
        verify_controller_binding(
            **{
                k: inputs[k]
                for k in ("binding", "runtime_record", "source_roots", "participant_policies")
            }
        )


def test_run_controller_binding_is_in_session_identity(tmp_path):
    """MON-8/MON-13: private controller launches are admitted and rechecked for both arms."""
    import copy

    from test_matched_tools import _controller

    from aisle.harness.matched_session import AdmissionError, admit_pair, verify_plan
    from aisle.harness.treatment_ambient import build_declared_environment

    fixture = tmp_path / "plan"
    fixture.mkdir()
    controller, views, _ = _controller(fixture, "typed")
    inputs_root = tmp_path / "inputs"
    inputs_root.mkdir()
    inputs = _inputs(inputs_root)
    bindings = {}
    for arm in ("typed", "monolithic"):
        binding = copy.deepcopy(inputs["binding"])
        env, record = build_declared_environment(
            tmp_path / (arm + "-controller-home"), source_env={}
        )
        binding.update(environment=env, environment_record=record)
        bindings[arm] = binding
    candidates = copy.deepcopy(controller.plan["arms"])
    for candidate in candidates.values():
        candidate.pop("immutable_id")
        candidate["repository"].pop("visible_files")
    plan = admit_pair(
        controller.root,
        candidates,
        views,
        confinement=controller.plan["confinement_bindings"],
        ambient=controller.plan["ambient_bindings"],
        tool_runtime=inputs["runtime_record"],
        run_controller=bindings,
    )
    assert plan["run_controller"] == bindings
    assert verify_plan(plan, controller.root, views) == plan
    home = Path(plan["run_controller"]["typed"]["environment_record"]["home"])
    home.rename(home.with_name(home.name + "-moved"))
    with pytest.raises(AdmissionError):
        verify_plan(plan, controller.root, views)


@pytest.mark.parametrize("construct", [False, True])
def test_tool_controller_dispatches_prepared_run_through_bound_child_launcher(
    tmp_path, monkeypatch, construct
):
    """MON-12: prepared runs retain the normal attempt journal and reserved run identity."""
    from test_matched_session import _development_protocol
    from test_matched_tools import _controller

    from aisle.harness import matched_run_launch, matched_tools

    controller, views, output = _controller(tmp_path, "typed", _development_protocol())
    current = dict(controller.plan)
    current["run_controller"] = {"typed": {"fixture": True}}
    current["tool_runtime"] = {"fixture": True}
    monkeypatch.setattr(matched_tools, "verify_active_plan", lambda *a, **kw: current)
    calls = []

    def launch(**kwargs):
        calls.append(kwargs)
        assert kwargs["expected"]["session_id"] == controller.session_id
        assert kwargs["expected"]["plan_id"] == controller.plan["immutable_id"]
        assert kwargs["expected"]["arm"] == "typed"
        if construct:
            data = kwargs["config_path"].read_bytes()
            assert hashlib.sha256(data).hexdigest() == kwargs["config_sha256"]
            config = json.loads(data)
            for key, value in kwargs["expected"].items():
                assert config[key] == value
            assert config["purpose"] == "expert_parity"
            assert config["launch"] == {
                "stages": [{"root": "/prepared-stage", "stage_id": "0" * 64}]
            }
        destination = kwargs["output"]
        destination.mkdir()
        result = {
            "ok": False,
            "process": {"rc": 1, "timed_out": False},
            "result": {"ok": False, "infrastructure_invalid": True},
            "error": None,
        }
        (destination / "process.json").write_text(json.dumps(result))
        (destination / "stdout.json").write_text(json.dumps(result["result"]))
        (destination / "stderr.log").write_text("")
        (destination / "invocation.json").write_text("{}")
        return result

    monkeypatch.setattr(matched_run_launch, "launch_configured_run", launch)
    if construct:
        result = controller.run_with_launch(
            {"stages": [{"root": "/prepared-stage", "stage_id": "0" * 64}]}
        )
    else:
        prepared = tmp_path / "prepared.json"
        prepared.write_bytes(b"{}")
        result = controller.run_prepared(prepared, hashlib.sha256(b"{}").hexdigest())
        assert (output / "tool-000001/run-config.json").read_bytes() == b"{}"
    assert len(calls) == 1
    assert result["classification"] == "infrastructure_exclusion"
    assert result["reservation"] == {"runs": 1, "episodes": len(_development_protocol()["seeds"])}
    assert result["artifacts"]["run-controller/process.json"]
    assert (output / "tool-000001/attempt.json").exists()


@pytest.mark.parametrize("cancel", [False, True])
def test_run_controller_reaps_child_on_wait_failure(tmp_path, monkeypatch, cancel):
    """MON-12/MON-13: timeout and cancellation stop a real child and retain its terminal record."""
    import subprocess

    from aisle.harness import matched_run_launch

    inputs = _inputs(tmp_path)
    original = matched_run_launch.spawn_isolated_process
    children = []

    def spawn(command, **kwargs):
        child = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        wait = child.wait
        first = True

        def interrupted(timeout=None):
            nonlocal first
            if first:
                first = False
                if cancel:
                    raise KeyboardInterrupt("fixture cancellation")
                raise subprocess.TimeoutExpired(command, timeout)
            return wait(timeout=timeout)

        monkeypatch.setattr(child, "wait", interrupted)
        return child

    monkeypatch.setattr(matched_run_launch, "spawn_isolated_process", spawn)
    if cancel:
        with pytest.raises(KeyboardInterrupt):
            matched_run_launch.launch_configured_run(**inputs)
    else:
        result = matched_run_launch.launch_configured_run(**inputs)
        assert not result["ok"]
        assert result["process"]["timed_out"]
    assert len(children) == 1
    assert children[0].poll() is not None
    retained = json.loads((inputs["output"] / "process.json").read_text())
    assert retained["process"]["rc"] == children[0].returncode
    assert retained["error"]


def _real_controller(tmp_path, arm):
    import copy
    import shutil

    from test_matched_session import _development_protocol
    from test_matched_tools import _controller

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.matched_session import admit_pair
    from aisle.harness.treatment_ambient import build_declared_environment

    controller, views, output = _controller(tmp_path, arm, _development_protocol())
    # Execute an isolated copy of the real controller package. This is not a
    # test of source-dependency closure; admission still checks its pinned files.
    shutil.copytree(
        ROOT / "src/aisle",
        controller.root / "src/aisle",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    runtime = capture_runtime([Path(sys.executable).resolve().parent.parent])
    bindings = {}
    for selected in ("typed", "monolithic"):
        env, env_record = build_declared_environment(
            tmp_path / (selected + "-run-home"), source_env={}
        )
        bindings[selected] = {
            "schema_version": "aisle.matched-run-controller.v1",
            "python": sys.executable,
            "python_sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            "environment": env,
            "environment_record": env_record,
        }
    candidates = copy.deepcopy(controller.plan["arms"])
    for candidate in candidates.values():
        candidate.pop("immutable_id")
        candidate["repository"].pop("visible_files")
    controller.plan = admit_pair(
        controller.root,
        candidates,
        views,
        confinement=controller.plan["confinement_bindings"],
        ambient=controller.plan["ambient_bindings"],
        development=_development_protocol(),
        tool_runtime=runtime,
        run_controller=bindings,
    )
    return controller, views, output


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_constructed_tool_run_reaches_real_child_preflight(tmp_path, arm):
    """MON-8/MON-12: admitted construction, reservation, child and journal form a real boundary."""
    controller, views, output = _real_controller(tmp_path, arm)
    missing = tmp_path / "missing-worker-input"
    selection = (
        {"stages": [{"root": str(missing), "stage_id": "0" * 64}]}
        if arm == "typed"
        else {"worker_config": str(missing), "worker_config_sha256": "0" * 64}
    )
    result = controller.run_with_launch(selection)
    assert result["process"]["rc"] == 1, result
    assert result["classification"] == "infrastructure_exclusion"
    assert result["result"]["infrastructure_invalid"]
    expected_error = (
        "validation input is missing or redirected"
        if arm == "typed"
        else "worker configuration path is missing or redirected"
    )
    assert result["result"]["error"] == expected_error
    config = json.loads((output / "tool-000001/run-config.json").read_text())
    assert config["run_id"] == result["run_id"]
    assert config["plan_id"] == controller.plan["immutable_id"]
    assert (
        config["worker_adapter_sha256"]
        == controller.plan["arms"][arm]["confinement"]["adapter_binary_sha256"]
    )
    assert result["artifacts"]["run-config.json"]
    from aisle.harness.matched_evidence import audit_tool_journal

    audit = audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm=arm,
        development=controller.plan["development"],
    )
    assert audit["ok"], audit
    retained = output / "tool-000001/run-controller/process.json"
    retained.write_text("{}")
    assert not audit_tool_journal(
        output,
        session_id=controller.session_id,
        plan_id=controller.plan["immutable_id"],
        arm=arm,
        development=controller.plan["development"],
    )["ok"]
    assert result["artifacts"]["run-controller/process.json"]


def test_absent_constructed_launch_never_falls_back_to_legacy(tmp_path, monkeypatch):
    """MON-8/MON-13: an explicitly incomplete prepared request stays infrastructure-invalid."""
    from test_matched_session import _development_protocol
    from test_matched_tools import _controller

    from aisle.harness import matched_tools

    controller, _, _ = _controller(tmp_path, "typed", _development_protocol())
    calls = []
    monkeypatch.setattr(matched_tools, "spawn_isolated_process", lambda *a, **kw: calls.append(a))
    result = controller.run_with_launch(None)
    assert not result["ok"]
    assert result["classification"] == "infrastructure_exclusion"
    assert not calls


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("rollout_timeout", [5, 100])
def test_prepared_run_reserves_tool_time_for_setup_and_evidence(
    tmp_path, monkeypatch, rollout_timeout, arm
):
    """MON-8/MON-12: a rollout deadline must not truncate its enclosing evidence transaction."""
    from test_matched_session import _development_protocol
    from test_matched_tools import _controller

    from aisle.harness import matched_run_launch, matched_tools

    development = _development_protocol()
    development["timeout_s"] = rollout_timeout
    controller, _, output = _controller(tmp_path, arm, development)
    current = dict(controller.plan)
    current["run_controller"] = {arm: {"fixture": True}}
    current["tool_runtime"] = {"fixture": True}
    monkeypatch.setattr(matched_tools, "verify_active_plan", lambda *a, **kw: current)
    controller.clock = lambda: 12.0
    controller.wall_spent = 3.0
    config = tmp_path / "prepared-run.json"
    config.write_bytes(b"{}")
    destination = output / "tool-000001"
    destination.mkdir()
    record = {"run_id": "fixture-run", "artifacts": {}}
    received = []

    def launch(**kwargs):
        received.append(kwargs["timeout_s"])
        assert kwargs["expected"]["development"]["timeout_s"] == rollout_timeout
        # Model a short rollout plus setup/collection: still inside the admitted
        # tool budget, but beyond a five-second simulation execution budget.
        completed = kwargs["timeout_s"] >= 10.0
        return {
            "process": {"rc": 0 if completed else -9, "timed_out": not completed},
            "result": {"ok": True} if completed else None,
            "error": None if completed else "outer deadline cut off collection",
            "ok": completed,
        }

    monkeypatch.setattr(matched_run_launch, "launch_configured_run", launch)
    controller._prepared_run(
        current,
        destination,
        record,
        started=10.0,
        wall=30.0,
        prepared=(config, hashlib.sha256(config.read_bytes()).hexdigest()),
    )
    assert record["ok"], record
    assert record["classification"] == "tool_result"
    # The enclosing deadline includes only unspent tool time, even when the
    # requested rollout execution timeout is longer than that budget.
    assert received == [25.0]


@pytest.mark.parametrize("launch_kind", ["configured", "typed", "monolithic"])
@pytest.mark.parametrize("cancel", [False, True])
def test_run_controller_wait_failure_stops_detached_descendant(
    tmp_path, monkeypatch, cancel, launch_kind
):
    """MON-12/MON-13: observed child-created process sessions are cleaned up on run timeout."""
    import os
    import signal
    import subprocess
    import time

    from aisle.harness import matched_run_launch

    if launch_kind == "configured":
        inputs = _inputs(tmp_path)
        module = matched_run_launch

        def invoke():
            return matched_run_launch.launch_configured_run(**inputs)

        retained_path = inputs["output"] / "process.json"
    else:
        from test_matched_session import _development_protocol
        from test_matched_tools import _controller

        from aisle.harness import matched_tools

        controller, _, output = _controller(tmp_path, launch_kind, _development_protocol())
        module = matched_tools
        invoke = controller.run
        retained_path = output / "tool-000001/attempt.json"
    original = module.spawn_isolated_process
    pid_file = tmp_path / "descendant.pid"
    descendant = None
    decoy = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    script = (
        "import subprocess, sys, time; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, "
        "start_new_session=True); Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(30)"
    )

    def spawn(command, **kwargs):
        nonlocal descendant
        child = original([sys.executable, "-c", script, str(pid_file)], **kwargs)
        wait = child.wait
        first = True

        def interrupted(timeout=None):
            nonlocal first, descendant
            if first:
                first = False
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    value = pid_file.read_text() if pid_file.exists() else ""
                    if value.isdecimal():
                        descendant = int(value)
                        break
                    time.sleep(0.01)
                assert descendant is not None, "fixture descendant did not start"
                if cancel:
                    raise KeyboardInterrupt("fixture cancellation")
                raise subprocess.TimeoutExpired(command, timeout)
            return wait(timeout=timeout)

        monkeypatch.setattr(child, "wait", interrupted)
        return child

    monkeypatch.setattr(module, "spawn_isolated_process", spawn)
    try:
        if cancel:
            with pytest.raises(KeyboardInterrupt):
                invoke()
        else:
            result = invoke()
            assert result["process"]["timed_out"], result
        assert decoy.poll() is None, "cleanup touched an unrelated process"
        assert descendant is not None
        # A zombie is no longer running. The independent OS reaper may still
        # need to collect an orphan's process-table entry after termination.
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(descendant)], capture_output=True, text=True
        ).stdout.strip()
        assert not state or state.startswith("Z"), f"detached descendant still runs: {state}"
        retained = json.loads(retained_path.read_text())
        assert retained["cleanup"]["ok"], retained
        assert retained["cleanup"]["exhaustive"] is False
        assert descendant in retained["cleanup"]["observed_descendants"]
    finally:
        if descendant is not None:
            try:
                os.kill(descendant, signal.SIGKILL)
            except ProcessLookupError:
                pass
        decoy.kill()
        decoy.wait()


def test_cleanup_never_signals_a_reaped_controller_pid(tmp_path, monkeypatch):
    """MON-13: a reaped controller PID cannot authorize a newly observed process tree."""
    from types import SimpleNamespace

    from aisle.harness import matched_run_launch

    signals = []
    process = SimpleNamespace(
        pid=12345,
        returncode=0,
        kill=lambda: signals.append("kill"),
        wait=lambda timeout=None: 0,
    )
    row = {"parent": 1, "state": "S", "identity": ("fixture start", "fixture executable")}
    monkeypatch.setattr(matched_run_launch, "_process_table", lambda **kw: {12345: row})
    monkeypatch.setattr(matched_run_launch.os, "killpg", lambda *args: signals.append(args))
    report = matched_run_launch._terminate_owned_run(process)
    assert not signals
    assert not report["ok"]
    assert report["errors"]


@pytest.mark.parametrize("disappears", [True, False])
def test_cleanup_waits_for_changed_identity_to_disappear(monkeypatch, disappears):
    """MON-13: exiting descendants may lose executable names without permitting signals."""
    from types import SimpleNamespace

    from aisle.harness import matched_run_launch as module

    original = {"parent": 123, "state": "S", "identity": ("start", "/bin/dora")}
    exiting = {"parent": 123, "state": "?E", "identity": ("start", "(dora)")}
    tables = iter(
        [
            {123: {**original, "parent": 1}, 456: original},
            {456: exiting},
            {} if disappears else {456: exiting},
        ]
    )
    from itertools import count

    ticks = count(step=2)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(module, "_process_table", lambda **kw: next(tables))
    signals = []
    monkeypatch.setattr(module.os, "kill", lambda *args: signals.append(args))
    monkeypatch.setattr(module.os, "killpg", lambda *args: None)
    process = SimpleNamespace(pid=123, returncode=None, wait=lambda **kw: 0)
    report = module._terminate_owned_run(process)
    assert signals == []
    assert report["ok"] is disappears, report
    assert report["exhaustive"] is False
    if not disappears:
        assert report["remaining"] == [456]
        assert report["errors"]
