"""MON-2/MON-12/MON-13: prepare current typed inputs before run dispatch."""

import copy
import json
from pathlib import Path

import pytest
import yaml
from test_treatment_confinement import _attestation
from test_typed_validation_binding import _admitted_controller

pytestmark = pytest.mark.unit


def _controller(tmp_path):
    from test_matched_session import _development_protocol

    from aisle.harness.matched_session import admit_pair
    from aisle.harness.treatment_ambient import build_declared_environment

    controller, views, output, validation = _admitted_controller(tmp_path)
    candidates = copy.deepcopy(controller.plan["arms"])
    for candidate in candidates.values():
        candidate.pop("immutable_id")
        candidate["repository"].pop("visible_files")
        candidate["policy"]["allowed_external_tools"].append("harness.run")
        candidate["budget"]["tool_wall_ceiling_s"] = 120
    bindings = {}
    for arm in ("typed", "monolithic"):
        environment, record = build_declared_environment(
            tmp_path / (arm + "-controller"), source_env={}
        )
        bindings[arm] = {
            "schema_version": "aisle.matched-run-controller.v1",
            "python": validation["python"],
            "python_sha256": validation["python_sha256"],
            "environment": environment,
            "environment_record": record,
        }
    controller.plan = admit_pair(
        controller.root,
        candidates,
        views,
        confinement=controller.plan["confinement_bindings"],
        ambient=controller.plan["ambient_bindings"],
        tool_runtime=controller.plan["tool_runtime"],
        typed_validation=validation,
        run_controller=bindings,
        development=_development_protocol(),
    )
    return controller, views, output, validation


def _workers(tmp_path, controller, views, output, validation):
    from aisle.harness.treatment_ambient import build_declared_environment
    from aisle.harness.treatment_confinement import MacOSPolicy, compile_macos_profile

    bundle = tmp_path / "fresh-execution"
    bundle.mkdir()
    declarations = {}
    names = {"segmented-pose", "grasp-planner-topdown", "ik-trajectory", "task-state-machine"}
    graph = yaml.safe_load((views["typed"] / "graphs/expert_t1.yaml").read_text())
    for node in graph["nodes"]:
        if node["id"] not in names:
            continue
        environment, record = build_declared_environment(
            tmp_path / (node["id"] + "-home"), source_env={}
        )
        policy = MacOSPolicy(
            visible_roots=(bundle,),
            output_roots=(Path(record["home"]),),
            runtime_read_roots=tuple(Path(p) for p in controller.plan["tool_runtime"]["trees"]),
            allowed_executables=(Path(validation["python"]).resolve(),),
            hidden_roots=(
                controller.root,
                *views.values(),
                Path(validation["snapshot_storage"]),
                output,
            ),
            network_policy="deny-external",
        )
        compiled = compile_macos_profile(policy)
        profile = tmp_path / (node["id"] + ".sb")
        profile.write_text(compiled.text)
        declarations[node["id"]] = {
            "bundle": str(bundle),
            "policy": policy.canonical_dict(),
            "profile_path": str(profile),
            "attestation": _attestation(compiled, profile, tmp_path / "synthetic-adapter"),
            "python": validation["python"],
            "python_sha256": validation["python_sha256"],
            "environment": environment,
            "environment_record": record,
            "runtime_record": controller.plan["tool_runtime"],
            "timeout_s": 10,
        }
    return [declarations], bundle


@pytest.mark.parametrize("invalid", [False, True])
def test_typed_run_prepares_current_sources_or_retains_normal_validation_failure(
    tmp_path, monkeypatch, invalid
):
    """MON-2/MON-12: invalid manifests retain diagnostics; valid edits reach fresh staging."""
    controller, views, output, validation = _controller(tmp_path)
    workers, bundle = _workers(tmp_path, controller, views, output, validation)
    source = views["typed"] / "src/aisle/nodes/segmented_pose.py"
    source.write_text("# current authored edit\n" + source.read_text())
    if invalid:
        (views["typed"] / "registry/manifests/segmented-pose.yaml").write_text(
            "broken: declaration"
        )
    calls = []

    def dispatch(current, destination, record, started, wall, prepared):
        calls.append(json.loads(Path(prepared[0]).read_text()))
        record.update(
            ok=False,
            classification="infrastructure_exclusion",
            process=None,
            result={"ok": False, "error": "fixture stops before graph execution"},
        )

    monkeypatch.setattr(controller, "_prepared_run", dispatch)
    result = controller.run_with_workers(workers)
    archive = output / "tool-000001/source-snapshot/src/aisle/nodes/segmented_pose.py"
    assert archive.read_bytes() == source.read_bytes()
    assert result["artifacts"]["source-snapshot/snapshot.json"]
    if invalid:
        assert result["classification"] == "tool_result", result
        assert not result["ok"]
        assert result["result"]["errors"]
        assert not calls
        assert not list(bundle.iterdir())
    else:
        assert len(calls) == 1, result
        selected = calls[0]["launch"]["stages"][0]
        receipt = json.loads((Path(selected["root"]) / "stage.json").read_text())
        assert receipt["immutable_id"] == selected["stage_id"]
        assert len(receipt["hosts"]) == len(workers[0])
        assert (bundle / "src/aisle/nodes/segmented_pose.py").read_bytes() == source.read_bytes()
        assert result["preparation"]["validation"]["ok"]


def test_snapshot_archive_failure_does_not_claim_a_run_executed(tmp_path, monkeypatch):
    """MON-12/MON-13: failed preparation retains validation without inventing run evidence."""
    from aisle.harness import typed_snapshot

    controller, views, output, validation = _controller(tmp_path)
    workers, bundle = _workers(tmp_path, controller, views, output, validation)

    def fail_archive(*args):
        raise OSError("fixture archive failure")

    monkeypatch.setattr(typed_snapshot, "archive_typed_snapshot", fail_archive)
    result = controller.run_with_workers(workers)
    assert not result["ok"]
    assert result["classification"] == "infrastructure_exclusion"
    assert result["error"] == "fixture archive failure"
    assert result["preparation"]["validation"]["result"]["ok"]
    assert result["run_evidence"] is None
    assert not list(bundle.iterdir())
