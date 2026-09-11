"""MON-12/MON-13: run fixtures retain real Git provenance and current controller bytes."""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "accept"))
pytestmark = pytest.mark.unit


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.parametrize("detached", [False, True])
def test_run_idea_uses_controller_branch(tmp_path, detached):
    """HAR-7/HAR-8: the fixture logs an open idea on rollout's actual branch."""
    from conformance_run_fixture import prepare_run_idea

    from aisle.harness.cli import _branch
    from aisle.harness.ideas import open_ideas

    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Fixture")
    git(tmp_path, "config", "user.email", "fixture@example.invalid")
    git(tmp_path, "commit", "--allow-empty", "-qm", "fixture")
    git(tmp_path, "checkout", "-qb", "fixture/run")
    if detached:
        git(tmp_path, "checkout", "--detach", "-q")
    receipt = prepare_run_idea(tmp_path, timestamp="2026-09-10T00:00:00Z")
    branch = "detached" if detached else "fixture/run"
    assert receipt["branch"] == branch == _branch(tmp_path)
    assert open_ideas(tmp_path, branch) == [receipt["entry"]]
    assert receipt["entry"]["git_sha"] == git(tmp_path, "rev-parse", "HEAD")
    assert not open_ideas(tmp_path, "HEAD")


def test_run_checkout_keeps_baseline_and_uncommitted_controller(tmp_path):
    """MON-12/MON-13: no fixture may substitute its local source checkout for the trusted remote."""
    from conformance_run_fixture import prepare_run_checkout

    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "Fixture")
    git(source, "config", "user.email", "fixture@example.invalid")
    (source / "controller.py").write_text("BASE = 1\n")
    (source / ".gitignore").write_text("private-token\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "fixture baseline")
    git(source, "remote", "add", "origin", "https://github.com/example/fixture.git")
    head = git(source, "rev-parse", "HEAD")
    (source / "controller.py").write_text("CURRENT = 2\n")
    (source / "new_controller.py").write_text("NEW = 3\n")
    (source / "private-token").write_text("must not copy ignored state")
    status = git(source, "status", "--porcelain")
    destination = tmp_path / "controller"
    record = prepare_run_checkout(
        source, destination, controller_files=("controller.py", "new_controller.py")
    )
    assert record["base_oid"] == head == git(destination, "rev-parse", "HEAD")
    assert (
        git(destination, "remote", "get-url", "origin") == "https://github.com/example/fixture.git"
    )
    for name in ("controller.py", "new_controller.py"):
        assert (destination / name).read_bytes() == (source / name).read_bytes()
        assert (
            record["source_files"][name] == hashlib.sha256((source / name).read_bytes()).hexdigest()
        )
    assert not (destination / "private-token").exists()
    assert git(source, "status", "--porcelain") == status
    with pytest.raises(ValueError, match="fresh"):
        prepare_run_checkout(source, destination, controller_files=("controller.py",))


def test_worker_declarations_prepare_real_monolithic_config(tmp_path):
    """MON-8/MON-13: fixture declarations produce a real private prepared worker configuration."""
    import json

    from conformance_run_fixture import prepare_worker_declarations

    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.monolithic_run_prepare import prepare_monolithic_run

    controller = tmp_path / "controller"
    controller.mkdir()
    views = {arm: tmp_path / arm for arm in ("typed", "monolithic")}
    for view in views.values():
        view.mkdir()
    module = views["monolithic"] / "experts/monolithic/expert_t1.py"
    module.parent.mkdir(parents=True)
    module.write_text('API_VERSION="1.0"\n')
    evidence = tmp_path / "retained"
    evidence.mkdir()
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    python = runtime_root / "python"
    python.write_text("#!/bin/sh\nexit 1\n")
    python.chmod(0o755)
    invocation = runtime_root / "venv-python"
    invocation.symlink_to(python)
    runtime = capture_runtime([runtime_root])
    adapter = tmp_path / "synthetic-adapter"
    adapter.write_text('#!/bin/sh\nshift 2\nexec "$@"\n')
    adapter.chmod(0o755)
    declarations = prepare_worker_declarations(
        tmp_path / "workers",
        controller=controller,
        views=views,
        evidence_root=evidence,
        snapshot_storage=evidence,
        runtime=runtime,
        python=invocation,
        adapter=adapter,
        development={"tier": "T1", "verifier": "oracle", "seeds": [7]},
    )
    prepared = prepare_monolithic_run(
        controller_root=controller,
        views=views,
        output=evidence / "attempt",
        declaration=declarations["monolithic"][0],
        runtime=runtime,
        adapter=hashlib.sha256(adapter.read_bytes()).hexdigest(),
        embodiment="franka",
    )
    config = json.loads(Path(prepared["worker_config"]).read_bytes())
    assert config["module_sha256"] == hashlib.sha256(module.read_bytes()).hexdigest()
    assert config["launch"]["runtime_record"] == runtime
    assert config["launch"]["max_primitive_calls"] == 100000
    assert config["launch"]["max_handles"] == 1024
    assert config["launch"]["timeout_s"] == 570
    assert config["launch"]["python"] == str(invocation)
    assert Path(config["output_root"]).is_relative_to(evidence)
    assert set(declarations["typed"][0]) == {
        "segmented-pose",
        "grasp-planner-topdown",
        "ik-trajectory",
        "task-state-machine",
    }


def test_controller_bindings_verify_with_shared_runtime(tmp_path):
    """MON-8/MON-13: validation and both run controllers use declared runtime and private homes."""
    from conformance_run_fixture import prepare_controller_bindings

    from aisle.harness.matched_run_launch import verify_controller_binding
    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.typed_validation import verify_validation_binding

    root = tmp_path / "controller"
    root.mkdir()
    views = {arm: tmp_path / arm for arm in ("typed", "monolithic")}
    for view in views.values():
        view.mkdir()
    evidence = tmp_path / "retained"
    evidence.mkdir()
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    python = runtime_root / "python"
    python.write_text("#!/bin/sh\nexit 1\n")
    python.chmod(0o755)
    (runtime_root / "binding_probe.py").write_text('VALUE = "declared"\n')
    (runtime_root / "bin").mkdir()
    python.rename(runtime_root / "bin/python")
    python = runtime_root / "bin/python"
    invocation = runtime_root / "bin/venv-python"
    invocation.symlink_to(python)
    runtime = capture_runtime([runtime_root])
    adapter = tmp_path / "adapter"
    adapter.write_text('#!/bin/sh\nshift 2\nexec "$@"\n')
    adapter.chmod(0o755)
    path = "/usr/bin:/bin:/usr/sbin:/sbin"
    bindings = prepare_controller_bindings(
        tmp_path / "bindings",
        controller=root,
        views=views,
        evidence_root=evidence,
        runtime=runtime,
        python=invocation,
        adapter=adapter,
        path=path,
    )
    assert bindings["typed_validation"]["python"] == str(invocation)
    assert all(row["python"] == str(invocation) for row in bindings["run_controller"].values())
    policies = [
        {"visible_roots": [str(view)], "output_roots": [], "runtime_read_roots": []}
        for view in views.values()
    ]
    verify_validation_binding(
        bindings["typed_validation"], runtime, [root, *views.values()], policies
    )
    for binding in bindings["run_controller"].values():
        verify_controller_binding(binding, runtime, [root, *views.values()], policies)
    homes = [row["environment"]["HOME"] for row in bindings["run_controller"].values()]
    assert len(set(homes)) == 2
    assert bindings["typed_validation"]["environment"]["HOME"] not in homes
    assert all(row["environment"]["PATH"] == path for row in bindings["run_controller"].values())

    # Trusted child checks use a fresh Python process and must see the same
    # declared dependency roots as the controller, rather than an empty site.
    child = subprocess.run(
        [sys.executable, "-B", "-c", "import binding_probe; print(binding_probe.VALUE)"],
        env=bindings["run_controller"]["typed"]["environment"],
        capture_output=True,
        text=True,
    )
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == "declared"


@pytest.mark.skipif(sys.platform != "darwin", reason="MuJoCo needs sysctl on macOS")
def test_controller_fixture_rejects_path_without_sysctl(tmp_path):
    """MON-13: reject an unusable simulator PATH before reserving private state."""
    from conformance_run_fixture import prepare_controller_bindings

    with pytest.raises(ValueError, match="sysctl"):
        prepare_controller_bindings(
            tmp_path / "bindings",
            controller=None,
            views=None,
            evidence_root=None,
            runtime=None,
            python=None,
            adapter=None,
            path=str(tmp_path),
        )
    assert not (tmp_path / "bindings").exists()


def test_run_reset_preserves_receipts_and_recreates_only_declared_state(tmp_path):
    """MON-13: fresh run state retains previous bytes and excludes controller/runtime trees."""
    from conformance_run_fixture import archive_run_state

    from aisle.harness.treatment_ambient import build_declared_environment

    root = tmp_path / "controller"
    root.mkdir()
    retained = tmp_path / "retained"
    (retained / "session").mkdir(parents=True)
    (retained / "session/receipt").write_text("original evidence")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    views = {"typed": tmp_path / "typed", "monolithic": tmp_path / "monolithic"}
    for view in views.values():
        view.mkdir()
    bindings = []
    for role in ("participant", "validator", "controller", "worker"):
        env, record = build_declared_environment(tmp_path / (role + "-home"), source_env={})
        (Path(env["HOME"]) / "consumed").write_text(role)
        bindings.append({"environment": env, "environment_record": record})
    bundle, storage = tmp_path / "bundle", tmp_path / "snapshots"
    for directory in (bundle, storage):
        directory.mkdir()
        (directory / "consumed").write_text(directory.name)
    setup = {
        "root": root,
        "views": views,
        "tool_runtime": {"trees": {str(runtime): {}}},
        "ambient": {
            "typed": {
                "environment": bindings[0]["environment"],
                "record": bindings[0]["environment_record"],
            }
        },
        "typed_validation": {**bindings[1], "snapshot_storage": str(storage)},
        "run_controller": {"typed": bindings[2]},
        "worker_preparations": {
            "typed": [],
            "monolithic": [{**bindings[3], "bundle": str(bundle)}],
        },
    }
    archive = retained / "state-0001"
    archive_run_state(tmp_path, setup, archive)
    assert (retained / "session/receipt").read_text() == "original evidence"
    for binding in bindings:
        home = Path(binding["environment"]["HOME"])
        assert not (home / "consumed").exists()
        assert (archive / home.relative_to(tmp_path) / "consumed").is_file()
        assert all(Path(p).is_dir() for p in binding["environment_record"]["state_directories"])
    assert not list(bundle.iterdir()) and not list(storage.iterdir())
    assert (archive / "bundle/consumed").read_text() == "bundle"
    with pytest.raises(ValueError, match="fresh"):
        archive_run_state(tmp_path, setup, archive)
    setup["worker_preparations"]["monolithic"][0]["bundle"] = str(root)
    with pytest.raises(ValueError, match="protected"):
        archive_run_state(tmp_path, setup, retained / "state-0002")
    assert not (retained / "state-0002").exists()


def test_prepared_run_rejects_stale_surface_before_loading_runtime(tmp_path):
    """MON-13: reject stale generated bindings before expensive runtime admission."""
    from conformance_run_fixture import load_run_setup
    from test_matched_session import prepared_pair

    controller, _, _ = prepared_pair(tmp_path)
    rollout = controller / "src/aisle/harness/rollout.py"
    rollout.write_bytes(rollout.read_bytes() + b"\n# changed controller\n")
    with pytest.raises(ValueError, match="controller surface"):
        load_run_setup(tmp_path)


@pytest.mark.parametrize("seeds", [[7], [7, 8]])
def test_run_limits_include_build_each_episode_and_audit(seeds):
    """MON-8: actual conformance runs retain the standard full episode budget."""
    from conformance_run_fixture import run_budget_limits

    from aisle.harness.rollout import GENESIS_BUILD_BUDGET_S, resolve_budgets

    limits = run_budget_limits({"tier": "T1", "verifier": "oracle", "seeds": seeds})
    expected = GENESIS_BUILD_BUDGET_S + len(seeds) * resolve_budgets("T1", "oracle")[1]
    assert limits["timeout_s"] == expected
    assert limits["tool_wall_ceiling_s"] >= expected + 600
    assert limits["wall_ceiling_s"] >= limits["tool_wall_ceiling_s"] + 600


@pytest.mark.parametrize("change", [None, "admission", "receipt", "provider"])
def test_reference_run_retains_authenticated_listener(tmp_path, change):
    """MON-13: paired runs reuse the prior admitted listener only through its intact receipt."""
    import json

    from conformance_run_fixture import reference_run_admission

    from aisle.harness.matched_session import _digest

    admission = {
        "schema_version": "aisle.matched-session-plan.v1",
        "launch_bindings": {
            arm: {"provider": {"base_url": "http://127.0.0.1:50896/v1"}}
            for arm in ("typed", "monolithic")
        },
    }
    if change == "provider":
        admission["launch_bindings"]["typed"]["provider"]["base_url"] = "http://example.com/v1"
    admission["immutable_id"] = _digest(admission)
    raw = json.dumps(admission).encode()
    record = {
        "schema_version": "aisle.matched-session-evidence.v1",
        "ok": True,
        "plan_id": admission["immutable_id"],
        "artifacts": {"admission.json": hashlib.sha256(raw).hexdigest()},
    }
    record["immutable_id"] = _digest(record)
    if change == "admission":
        raw += b" "
    if change == "receipt":
        record["ok"] = False
    (tmp_path / "admission.json").write_bytes(raw)
    (tmp_path / "matched-session.json").write_text(json.dumps(record))
    if change is None:
        bound, address = reference_run_admission(tmp_path)
        assert bound == admission
        assert address == ("127.0.0.1", 50896)
    else:
        with pytest.raises(ValueError):
            reference_run_admission(tmp_path)


@pytest.mark.parametrize("change", [None, "budget", "provider", "execution", "controller"])
@pytest.mark.parametrize("fault", [None, "hook_absent", "hook_changed"])
def test_paired_run_refuses_configuration_changes(tmp_path, change, fault):
    """MON-13: a paired run cannot silently alter budgets, listeners, runtime or controller code."""
    import copy

    from conformance_run_fixture import require_same_run_configuration
    from test_conformance_profile_binding import bound_profile

    from aisle.harness.frontend_conformance import fault_launch
    from aisle.harness.matched_session import CONTROLLER_FILES

    _, candidates, launches, _, _ = bound_profile(tmp_path)
    reference = {
        "arms": candidates,
        "launch_bindings": launches,
        "tool_runtime": {"immutable_id": "runtime"},
        "surface": {"artifact_hashes": {name: "a" * 64 for name in CONTROLLER_FILES}},
    }
    plan = copy.deepcopy(reference)
    if fault is not None:
        plan["launch_bindings"] = {
            arm: fault_launch(launch, fault) for arm, launch in plan["launch_bindings"].items()
        }
    if change == "budget":
        plan["arms"]["typed"]["budget"]["wall_ceiling_s"] += 1
    elif change == "provider":
        plan["launch_bindings"]["typed"]["provider"] = {"base_url": "http://127.0.0.1:9/v1"}
    elif change == "execution":
        plan["tool_runtime"]["immutable_id"] = "changed"
    elif change == "controller":
        plan["surface"]["artifact_hashes"][CONTROLLER_FILES[0]] = "b" * 64
    if change is None:
        require_same_run_configuration(plan, reference, fault=fault)
        assert plan["arms"] == reference["arms"]
        if fault is None:
            assert plan == reference
    else:
        with pytest.raises(ValueError, match="paired run"):
            require_same_run_configuration(plan, reference, fault=fault)


@pytest.mark.parametrize("preloaded", [False, True])
def test_prepared_controller_imports_cannot_mix_checkouts(tmp_path, preloaded):
    """MON-13: actual fixture imports select the declared checkout before controller code loads."""
    import json

    controller, foreign = tmp_path / "controller", tmp_path / "foreign"
    for root, value in ((controller, "declared"), (foreign, "foreign")):
        (root / "src/aisle").mkdir(parents=True)
        (root / "src/aisle/__init__.py").write_text(f"IDENTITY = {value!r}\n")
        (root / "tools").mkdir()
        (root / "tools/campaign.py").write_text(f"IDENTITY = {value!r}\n")
    code = """import json,sys
sys.path[:0]=[sys.argv[1],sys.argv[2]+"/src",sys.argv[2]+"/tools"]
from conformance_run_fixture import activate_prepared_controller
if sys.argv[4]=="yes":
    import aisle
try:
    activate_prepared_controller(sys.argv[3])
except ValueError as exc:
    print(json.dumps({"refused":str(exc)}))
else:
    import aisle,campaign
    print(json.dumps({"package":aisle.IDENTITY,"tools":campaign.IDENTITY}))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            code,
            str(Path(__file__).resolve().parents[1] / "accept"),
            str(foreign),
            str(controller),
            "yes" if preloaded else "no",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    report = json.loads(result.stdout)
    if preloaded:
        assert "refused" in report
    else:
        assert report == {"package": "declared", "tools": "declared"}


def test_fixture_workers_reach_typed_graph_preflight(tmp_path, monkeypatch):
    """MON-6/MON-13: fixture worker grants hide the actual validation snapshots before staging."""
    import json

    from conformance_run_fixture import prepare_worker_declarations, run_budget_limits
    from test_typed_run_prepare import _controller

    controller, views, output, validation = _controller(tmp_path)
    declarations = prepare_worker_declarations(
        tmp_path / "fixture-workers",
        controller=controller.root,
        views=views,
        evidence_root=output,
        snapshot_storage=Path(validation["snapshot_storage"]),
        runtime=controller.plan["tool_runtime"],
        python=Path(validation["python"]),
        adapter=tmp_path / "synthetic-adapter",
        development=controller.plan["development"],
    )
    dispatched = []

    def stop_before_rollout(current, destination, record, started, wall, prepared):
        dispatched.append(prepared)
        record.update(
            ok=False,
            classification="infrastructure_exclusion",
            process=None,
            result={"ok": False, "error": "test stops after real graph preflight"},
        )

    monkeypatch.setattr(controller, "_prepared_run", stop_before_rollout)
    record = controller.run_with_workers(declarations["typed"])
    assert len(dispatched) == 1, record
    assert record["preparation"]["validation"]["ok"] is True
    assert all(
        validation["snapshot_storage"] in row["policy"]["hidden_roots"]
        for row in declarations["typed"][0].values()
    )
    hosts = list(output.glob("tool-*/typed-run/stage-*/hosts/*.json"))
    assert len(hosts) == 4
    timeout = run_budget_limits(controller.plan["development"])["timeout_s"]
    assert all(json.loads(host.read_bytes())["launch"]["timeout_s"] == timeout for host in hosts)
