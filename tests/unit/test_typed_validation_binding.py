"""MON-8/MON-12/MON-13: admitted typed validation is a real controller tool path."""

import copy
import hashlib
import json
import shlex
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_matched_tools import _controller
from test_treatment_confinement import _attestation
from test_typed_validation_launch import _inputs
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


def _binding(tmp_path, hidden=()):
    from aisle.harness.treatment_confinement import compile_macos_profile

    tmp_path.mkdir()
    inputs = _inputs(tmp_path)
    storage = tmp_path / "snapshots"
    storage.mkdir()
    policy = replace(
        inputs["policy"],
        visible_roots=(inputs["bundle"], storage),
        hidden_roots=tuple(dict.fromkeys((*inputs["policy"].hidden_roots, *hidden))),
    )
    compiled = compile_macos_profile(policy)
    inputs["profile_path"].write_text(compiled.text)
    keys = (
        "bundle",
        "bundle_manifest",
        "profile_path",
        "python",
        "python_sha256",
        "environment",
        "environment_record",
    )
    binding = {
        key: str(inputs[key]) if isinstance(inputs[key], Path) else inputs[key] for key in keys
    }
    binding.update(
        schema_version="aisle.typed-validation-binding.v1",
        snapshot_storage=str(storage),
        policy=policy.canonical_dict(),
        attestation=_attestation(compiled, inputs["profile_path"], tmp_path / "synthetic-adapter"),
    )
    return binding, inputs["runtime_record"]


def _admitted_controller(tmp_path):
    from test_monolith_worker_launch import _worker_interpreter

    from aisle.harness.matched_session import admit_pair

    controller, views, output = _controller(tmp_path, "typed", python=_worker_interpreter()[0])
    shutil.copytree(ROOT / "registry", controller.root / "registry", dirs_exist_ok=True)
    shutil.copytree(
        ROOT / "src/aisle/nodes", controller.root / "src/aisle/nodes", dirs_exist_ok=True
    )
    binding, runtime = _binding(
        tmp_path / "validation", hidden=(controller.root, output, *views.values())
    )
    candidates = copy.deepcopy(controller.plan["arms"])
    for candidate in candidates.values():
        candidate.pop("immutable_id")
        candidate["repository"].pop("visible_files")
        candidate["budget"]["tool_ceiling"] = 2
    controller.plan = admit_pair(
        controller.root,
        candidates,
        views,
        confinement=controller.plan["confinement_bindings"],
        ambient=controller.plan["ambient_bindings"],
        tool_runtime=runtime,
        typed_validation=binding,
    )
    return controller, views, output, binding


def test_typed_fixture_admits_worker_binary_when_launcher_bytes_differ(tmp_path, monkeypatch):
    """MON-6/MON-13: framework launchers and worker binaries have distinct identities."""
    import test_monolith_worker_launch as worker_fixture

    python, runtime_root = worker_fixture._worker_interpreter()
    launcher = tmp_path / "launcher-runtime/bin/python"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(f'#!/bin/sh\nexec {shlex.quote(str(python))} "$@"\n')
    launcher.chmod(0o755)
    assert (
        hashlib.sha256(launcher.read_bytes()).digest()
        != hashlib.sha256(python.read_bytes()).digest()
    )
    monkeypatch.setattr(sys, "executable", str(launcher))
    monkeypatch.setattr(worker_fixture, "_worker_interpreter", lambda: (python, runtime_root))
    controller, _, _, binding = _admitted_controller(tmp_path / "case")
    expected = {"name": "harness-python", "sha256": binding["python_sha256"]}
    assert controller.python == python
    assert all(expected in arm["runtime_binaries"] for arm in controller.plan["arms"].values())


def test_admitted_typed_tool_uses_complete_snapshot_and_retains_result(tmp_path):
    """MON-2/MON-12: successive checks retain distinct snapshots and normal diagnostics."""
    from aisle.harness.matched_evidence import audit_tool_journal

    controller, views, output, binding = _admitted_controller(tmp_path)
    (views["typed"] / "src/aisle/nodes/segmented_pose.py").write_text(
        "raise AssertionError('authored')"
    )
    result = controller.check()
    assert result["classification"] == "tool_result", result
    assert result["ok"] is True, result
    assert result["process"]["rc"] == 0
    assert (output / "tool-000001/validation/runtime.json").exists()
    launch = json.loads((output / "tool-000001/validation/launch.json").read_text())
    assert Path(launch["cwd"]).parent == Path(binding["snapshot_storage"])
    assert launch["argv"][1:3] == ["-I", "-B"]
    assert result["artifacts"]["stdout.json"]
    (views["typed"] / "registry/manifests/segmented-pose.yaml").write_text("broken: declaration")
    second = controller.check()
    assert second["classification"] == "tool_result", second
    assert second["ok"] is False
    second_launch = json.loads((output / "tool-000002/validation/launch.json").read_text())
    assert second_launch["cwd"] != launch["cwd"]
    assert second_launch["profile_sha256"] == launch["profile_sha256"]
    audit_args = dict(
        session_id=controller.session_id, plan_id=controller.plan["immutable_id"], arm="typed"
    )
    audit = audit_tool_journal(output, **audit_args)
    assert audit["ok"], audit
    (output / "tool-000001/validation/runtime.json").write_text("{}")
    assert not audit_tool_journal(output, **audit_args)["ok"]


@pytest.mark.parametrize("drift", ["profile", "bundle", "runtime", "binding"])
def test_bound_typed_tool_refuses_drift_before_creating_snapshot(tmp_path, drift):
    """MON-13: altered validator inputs are refused before snapshot creation or launch."""
    controller, views, output, binding = _admitted_controller(tmp_path)
    if drift == "profile":
        Path(binding["profile_path"]).write_text("(allow default)")
    elif drift == "bundle":
        (Path(binding["bundle"]) / "injected.py").write_text("pass")
    elif drift == "runtime":
        (tmp_path / "validation/runtime-packages/package.py").write_text("VALUE = 2")
    else:
        controller.plan["typed_validation"]["python_sha256"] = "0" * 64
    result = controller.check()
    assert result["classification"] == "infrastructure_exclusion"
    assert result["process"] is None
    assert not list(Path(binding["snapshot_storage"]).iterdir())


def test_typed_check_refuses_preexisting_snapshot_storage(tmp_path):
    """MON-13: a fresh controller cannot adopt another session's readable snapshots."""
    controller, views, output, binding = _admitted_controller(tmp_path)
    (Path(binding["snapshot_storage"]) / "old-session.txt").write_text("old candidate data")
    result = controller.check()
    assert result["classification"] == "infrastructure_exclusion", result
    assert result["process"] is None
    assert "snapshot storage" in result["error"]


def test_typed_controller_claims_storage_before_any_tool_or_agent_can_start(tmp_path):
    """MON-8/MON-13: startup reserves fresh storage and rejects reuse before execution."""
    from aisle.harness.matched_session import AdmissionError
    from aisle.harness.matched_tools import ToolController

    prior, views, output, binding = _admitted_controller(tmp_path)
    kwargs = dict(
        session_id=prior.session_id,
        python=prior.python,
        profile_path=prior.profile_path,
        attestation=prior.attestation,
        create_output=True,
    )
    controller = ToolController(prior.plan, prior.root, views, "typed", output / "fresh", **kwargs)
    owner = Path(binding["snapshot_storage"]) / "session.json"
    assert owner.is_file()
    assert controller.attempts == 0
    with pytest.raises(AdmissionError, match="snapshot storage"):
        ToolController(prior.plan, prior.root, views, "typed", output / "reuse", **kwargs)


def test_validation_launch_refuses_file_instead_of_snapshot_storage(tmp_path):
    """MON-8/MON-13: a non-directory storage grant is rejected during static preflight."""
    from aisle.harness.typed_validation import ValidationError, verify_validation_launch

    inputs = _inputs(tmp_path)
    storage = tmp_path / "not-a-directory"
    storage.write_text("invalid storage")
    policy = replace(inputs["policy"], visible_roots=(inputs["bundle"], storage))
    keys = (
        "bundle",
        "bundle_manifest",
        "python",
        "python_sha256",
        "runtime_record",
        "environment",
        "environment_record",
    )
    with pytest.raises(ValidationError, match="storage"):
        verify_validation_launch(
            **{key: inputs[key] for key in keys},
            policy=policy,
            snapshot_storage=storage,
            source_roots=(ROOT, tmp_path / "typed"),
        )
