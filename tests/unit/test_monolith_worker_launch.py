"""MON-8/MON-13: exact worker assets and verified adapter launch integration."""

import hashlib
import json
import sys
from pathlib import Path

import pytest
from test_treatment_confinement import _attestation

pytestmark = pytest.mark.unit


def _launch_inputs(tmp_path):
    from aisle.harness.matched_runtime import capture_runtime
    from aisle.harness.treatment_ambient import build_declared_environment
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.worker_launch import build_worker_bundle

    bundle = tmp_path / "bundle"
    manifest = build_worker_bundle(bundle)
    private = tmp_path / "private"
    private.mkdir()
    home = tmp_path / "worker-home"
    environment, environment_record = build_declared_environment(
        home, source_env={"PATH": "/usr/bin:/bin"}
    )
    python = Path(sys.executable)
    source = tmp_path / "participant-source"
    source.mkdir()
    packages = tmp_path / "bound-runtime-assets"
    packages.mkdir()
    (packages / "runtime.py").write_text("VALUE = 1")
    policy = MacOSPolicy(
        visible_roots=(bundle,),
        output_roots=(home,),
        runtime_read_roots=(python.resolve().parent.parent, packages),
        allowed_executables=(python.resolve(),),
        hidden_roots=(private, source),
        network_policy="deny-external",
    )
    profile = tmp_path / "worker.sb"
    compiled = compile_macos_profile(policy)
    profile.write_text(compiled.text)
    adapter = tmp_path / "synthetic-adapter"
    adapter.write_text('#!/bin/sh\nshift 2\nexec "$@"\n')
    adapter.chmod(0o755)
    return dict(
        bundle=bundle,
        bundle_manifest=manifest,
        runtime_record=capture_runtime(policy.runtime_read_roots),
        source_roots=[str(source)],
        policy=policy,
        profile_path=profile,
        attestation=_attestation(compiled, profile, adapter),
        python=python,
        python_sha256=hashlib.sha256(python.read_bytes()).hexdigest(),
        environment=environment,
        environment_record=environment_record,
        output=private / "evidence",
        primitives=Primitives._load("franka"),
        timeout_s=5,
    )


def test_verified_launch_runs_curated_worker_and_retains_launch(tmp_path):
    """MON-8/MON-12: an explicitly synthetic adapter launches the exact worker bundle.

    This proves adapter integration, not actual OS confinement.
    """
    from aisle.monolith.worker_launch import launch_worker

    inputs = _launch_inputs(tmp_path)
    source = (
        "API_VERSION='1.0'\nclass Controller:\n"
        " def __init__(self,p,log): self.p=p\n def on_event(self,e): return []\n"
    )
    with launch_worker(**inputs) as worker:
        worker.initialize(source, "fixture.py")
        assert worker.event({"name": "tick", "payload": 1}) == []
    receipt = json.loads((inputs["output"] / "launch.json").read_text())
    assert receipt["bundle_id"] == inputs["bundle_manifest"]["immutable_id"]
    assert receipt["python_sha256"] == inputs["python_sha256"]
    assert receipt["runtime_id"] == inputs["runtime_record"]["immutable_id"]
    assert json.loads((inputs["output"] / "runtime.json").read_text()) == inputs["runtime_record"]
    assert receipt["argv"][1:3] == ["-I", "-B"]
    assert (inputs["output"] / "stderr.log").exists()
    assert json.loads((inputs["output"] / "rpc/worker.json").read_text())["state"] == "closed"


@pytest.mark.parametrize(
    "drift", ["bundle", "extra", "interpreter", "profile", "environment", "timeout"]
)
def test_worker_launch_refuses_drift_before_spawn(tmp_path, monkeypatch, drift):
    """MON-8/MON-13: stale assets, authority or ambient identities never reach process start."""
    from aisle.monolith.worker_launch import launch_worker

    inputs = _launch_inputs(tmp_path)
    if drift == "bundle":
        (inputs["bundle"] / "aisle/monolith/worker.py").chmod(0o644)
        (inputs["bundle"] / "aisle/monolith/worker.py").write_text("raise SystemExit(0)")
    elif drift == "extra":
        (inputs["bundle"] / "private-helper.py").write_text("pass")
    elif drift == "interpreter":
        inputs["python_sha256"] = "0" * 64
    elif drift == "profile":
        inputs["profile_path"].write_text("(allow default)")
    elif drift == "environment":
        inputs["environment"]["EXTRA"] = "undeclared"
    else:
        inputs["timeout_s"] = float("nan")
    calls = []
    monkeypatch.setattr(
        "aisle.monolith.worker_launch.spawn_isolated_process", lambda *a, **kw: calls.append(a)
    )
    from aisle.harness.treatment_ambient import AmbientIsolationError
    from aisle.harness.treatment_confinement import ConfinementError
    from aisle.monolith.supervisor import WorkerFailure

    with pytest.raises((WorkerFailure, ConfinementError, AmbientIsolationError)):
        with launch_worker(**inputs):
            pytest.fail("drifted worker entered")
    assert not calls


def test_worker_bundle_cannot_have_write_authority(tmp_path, monkeypatch):
    """MON-8/MON-13: policy grants cannot let the worker replace its own implementation."""
    from dataclasses import replace

    from aisle.monolith.supervisor import WorkerFailure
    from aisle.monolith.worker_launch import launch_worker

    inputs = _launch_inputs(tmp_path)
    inputs["policy"] = replace(
        inputs["policy"], output_roots=(*inputs["policy"].output_roots, inputs["bundle"])
    )
    calls = []
    monkeypatch.setattr(
        "aisle.monolith.worker_launch.spawn_isolated_process",
        lambda *args, **kwargs: calls.append(args),
    )
    with pytest.raises(WorkerFailure, match="bundle has write authority"):
        with launch_worker(**inputs):
            pytest.fail("writable worker bundle entered")
    assert not calls


@pytest.mark.parametrize(
    "mutation", ["runtime", "runtime_grant", "visible", "write", "execute", "source"]
)
def test_matched_worker_refuses_runtime_drift_and_excess_authority(tmp_path, monkeypatch, mutation):
    """MON-8/MON-13: matched monolithic workers bind runtime contents and exact grants."""
    from dataclasses import replace

    from aisle.harness.treatment_confinement import compile_macos_profile
    from aisle.monolith import worker_launch
    from aisle.monolith.supervisor import WorkerFailure

    inputs = _launch_inputs(tmp_path)
    policy = inputs["policy"]
    if mutation == "runtime":
        (tmp_path / "bound-runtime-assets/runtime.py").write_text("VALUE = 2")
    elif mutation == "runtime_grant":
        extra = tmp_path / "unbound-runtime"
        extra.mkdir()
        policy = replace(policy, runtime_read_roots=(*policy.runtime_read_roots, extra))
    elif mutation == "visible":
        extra = tmp_path / "undeclared-read"
        extra.mkdir()
        policy = replace(policy, visible_roots=(*policy.visible_roots, extra))
    elif mutation == "write":
        extra = tmp_path / "undeclared-write"
        extra.mkdir()
        policy = replace(policy, output_roots=(*policy.output_roots, extra))
    elif mutation == "execute":
        policy = replace(
            policy, allowed_executables=(*policy.allowed_executables, Path("/bin/sh").resolve())
        )
    else:
        policy = replace(policy, hidden_roots=(tmp_path / "private",))
    inputs["policy"] = policy
    compiled = compile_macos_profile(policy)
    inputs["profile_path"].write_text(compiled.text)
    inputs["attestation"] = _attestation(
        compiled, inputs["profile_path"], tmp_path / "synthetic-adapter"
    )
    calls = []
    monkeypatch.setattr(worker_launch, "spawn_isolated_process", lambda *a, **kw: calls.append(a))
    with pytest.raises(WorkerFailure):
        with worker_launch.launch_worker(**inputs):
            pytest.fail("unbound worker entered")
    assert not calls


def test_worker_runtime_is_rechecked_after_child_teardown(tmp_path):
    """MON-13: a completed worker cannot conceal runtime drift during execution."""
    from aisle.monolith.supervisor import WorkerFailure
    from aisle.monolith.worker_launch import launch_worker

    inputs = _launch_inputs(tmp_path)
    with pytest.raises(WorkerFailure, match="runtime"):
        with launch_worker(**inputs) as worker:
            worker.initialize(
                "API_VERSION='1.0'\nclass Controller:\n def __init__(self,p,log): pass\n",
                "fixture.py",
            )
            (tmp_path / "bound-runtime-assets/runtime.py").write_text("VALUE = 2")
    assert json.loads((inputs["output"] / "rpc/worker.json").read_text())["state"] == "closed"
    assert (inputs["output"] / "failure.json").is_file()
