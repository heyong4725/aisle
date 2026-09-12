"""MON-8/MON-13: selected task files remain bound through private preparation."""

import copy
import json
import shutil
from pathlib import Path

import pytest

from aisle.harness.typed_snapshot import build_typed_validation_snapshot

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
PILOT = "t1-l2-pilot-v1"


def test_controller_documents_follow_explicit_task_selection(tmp_path):
    """MON-1/MON-13: selected task documents cannot be replaced by the legacy table."""
    from aisle.harness.monolith import load_json

    for folder, identity in (("docs/monolithic", "legacy"), ("docs/monolithic/pilot-l2", PILOT)):
        path = tmp_path / folder / "interface-map.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": identity}))
    assert load_json(tmp_path, "interface-map.json", task_surface=PILOT)["id"] == PILOT
    assert load_json(tmp_path, "interface-map.json")["id"] == "legacy"
    with pytest.raises(ValueError, match="surface"):
        load_json(tmp_path, "interface-map.json", task_surface="unknown")


def test_monolithic_cli_passes_the_named_surface_to_execution(monkeypatch, capsys):
    """MON-8: the public run parser cannot discard the selected task before execution."""
    from aisle.harness import cli, monolith

    observed = []
    monkeypatch.setattr(monolith, "run", lambda **kwargs: observed.append(kwargs) or {"ok": True})
    monkeypatch.setattr(
        "sys.argv",
        [
            "harness",
            "monolith",
            "run",
            "--module",
            "/tmp/pilot.py",
            "--episodes",
            "1",
            "--seeds",
            "0",
            "--task-surface",
            PILOT,
        ],
    )
    assert cli.main() == 0
    assert observed[0]["task_surface"] == PILOT
    assert json.loads(capsys.readouterr().out)["ok"]


def test_monolithic_run_uses_the_selected_graph_template(tmp_path, monkeypatch):
    """MON-4/MON-13: actual graph stamping must select the pilot template, not L1."""
    import yaml

    from aisle.harness import cli, monolith, rollout

    module = tmp_path / "candidate.py"
    module.write_text("API_VERSION = '1.0'\n")
    graph = tmp_path / "graphs/pilot_t1_l2_monolithic.yaml"
    graph.parent.mkdir()
    graph.write_text(
        yaml.safe_dump(
            {
                "nodes": [
                    {
                        "id": "monolith-broker",
                        "path": "../src/aisle/nodes/monolith_broker.py",
                        "env": {
                            "AISLE_MONOLITH_MODULE": "candidate.py",
                            "PILOT_FIXTURE": "selected",
                        },
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(monolith, "check_module", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cli, "_branch", lambda root: "fixture")
    received = []
    monkeypatch.setattr(
        rollout, "rollout", lambda **kwargs: received.append(kwargs) or {"ok": True}
    )
    result = monolith.run(tmp_path, module, [0], 1, task_surface=PILOT, run_id="fixture")
    assert result["ok"]
    stamped = yaml.safe_load(received[0]["graph"].read_text())
    assert stamped["nodes"][0]["env"]["PILOT_FIXTURE"] == "selected"
    assert stamped["nodes"][0]["env"]["AISLE_MONOLITH_MODULE"] == str(module)


def test_development_declaration_accepts_only_named_task_surface():
    """MON-8/MON-13: select L2 explicitly without permitting arbitrary modes or paths."""
    from aisle.harness.matched_session import AdmissionError, _verify_development

    declaration = {
        "schema_version": "aisle.matched-development.v1",
        "purpose": "expert_parity",
        "tier": "T1",
        "embodiment": "franka",
        "verifier": "oracle",
        "reset": "teleport",
        "seeds": [0],
        "run_ceiling": 1,
        "episode_ceiling": 1,
        "timeout_s": 30.0,
        "task_surface": PILOT,
    }
    _verify_development(declaration)
    for value in (None, False, "../private", "unknown"):
        with pytest.raises(AdmissionError, match="surface"):
            _verify_development({**declaration, "task_surface": value})


def _inputs(tmp_path):
    controller = tmp_path / "controller"
    participant = tmp_path / "participant"
    shutil.copytree(ROOT / "registry", controller / "registry")
    shutil.copytree(ROOT / "src", controller / "src")
    for folder in ("env", "assets/so101"):
        shutil.copytree(ROOT / folder, controller / folder)
    graph = "graphs/pilot_t1_l2_typed.yaml"
    plan = "graphs/turn_plans/pilot_t1_l2_typed.json"
    sources = [
        f"src/aisle/nodes/{name}.py"
        for name in ("l2_pose", "grasp_topdown", "ik_trajectory", "task_state_machine")
    ]
    editable = [graph, plan, *sources]
    allowlist = controller / "docs/monolithic/pilot-l2/allowlist.json"
    allowlist.parent.mkdir(parents=True)
    allowlist.write_text(json.dumps({"typed": {"editable": editable}}))
    for name in sources:
        destination = participant / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    (participant / graph).parent.mkdir(parents=True)
    (participant / graph).write_text("nodes: []\n")
    (participant / plan).parent.mkdir(parents=True)
    (participant / plan).write_text("{}\n")
    return controller, participant, graph


def test_selected_surface_snapshot_and_validation_command_use_same_graph(tmp_path):
    """MON-8/MON-13: a pilot snapshot must not fall back to the L1 graph/allowlist."""
    from aisle.harness.typed_validation import build_validation_bundle, validation_command

    controller, participant, graph = _inputs(tmp_path)
    output = tmp_path / "snapshot"
    record = build_typed_validation_snapshot(controller, participant, output, task_surface=PILOT)
    assert record["task_surface"] == PILOT
    assert graph in record["files"]
    assert "graphs/expert_t1.yaml" not in record["files"]
    assert record["files"]["src/aisle/nodes/l2_pose.py"]["origin"] == "participant"
    bundle = tmp_path / "validator"
    manifest = build_validation_bundle(bundle)
    command = validation_command("/usr/bin/python3", bundle, manifest, output, record, "franka")
    assert command[command.index("validate") + 1] == str(output / graph)


def test_selected_bundle_binds_l2_authoring_and_trusted_geometry(tmp_path):
    """MON-4/MON-13: L2 code is authored, while its shared geometry stays controller-owned."""
    from aisle.harness.typed_execution_bundle import (
        ExecutionBundleError,
        build_execution_bundle,
        verify_execution_bundle,
    )
    from aisle.harness.typed_snapshot import _digest

    controller, participant, _ = _inputs(tmp_path)
    output = tmp_path / "bundle"
    record = build_execution_bundle(controller, participant, output, task_surface=PILOT)
    assert record["task_surface"] == PILOT
    assert record["files"]["src/aisle/nodes/l2_pose.py"]["origin"] == "participant"
    assert record["files"]["src/aisle/nodes/segmented_pose.py"]["origin"] == "controller"
    changed = copy.deepcopy(record)
    changed.pop("task_surface")
    changed.pop("immutable_id")
    changed["immutable_id"] = _digest(changed)
    with pytest.raises(ExecutionBundleError, match="dependency set"):
        verify_execution_bundle(output, changed)


@pytest.mark.parametrize("selection", [None, "../../private", "unknown", {}, False])
def test_unknown_surface_refuses_before_copying_inputs(tmp_path, selection):
    """MON-8: explicit invalid selections cannot silently select the default task."""
    with pytest.raises(ValueError, match="surface"):
        build_typed_validation_snapshot(
            tmp_path / "controller",
            tmp_path / "participant",
            tmp_path / "output",
            task_surface=selection,
        )
    assert not (tmp_path / "output").exists()


def test_worker_node_selection_uses_the_selected_authoring_surface():
    """MON-6/MON-13: L2 worker provisioning selects L2 code and leaves L1 geometry trusted."""
    from aisle.harness.typed_worker_provisioning import authored_worker_nodes

    graph = {
        "nodes": [
            {"id": "rgb-pose", "path": "../src/aisle/nodes/l2_pose.py"},
            {"id": "mask-pose", "path": "../src/aisle/nodes/segmented_pose.py"},
        ]
    }
    assert authored_worker_nodes(graph, task_surface=PILOT) == {
        "rgb-pose": "src/aisle/nodes/l2_pose.py"
    }
    assert authored_worker_nodes(graph) == {"mask-pose": "src/aisle/nodes/segmented_pose.py"}


@pytest.mark.parametrize("module", ["aisle.nodes.l2_pose", "aisle.nodes.segmented_pose"])
def test_l2_launch_uses_receipt_authority_even_when_l1_geometry_is_present(tmp_path, module):
    """MON-6/MON-13: launch L2 authored code; refuse execution of its trusted L1 dependency.

    The synthetic adapter exercises real child-process wiring, not OS confinement.
    The small source exercises launch/protocol identity, not perception inference.
    """
    from test_typed_worker_launch import _inputs as launch_inputs

    from aisle.harness.typed_execution_bundle import build_execution_bundle
    from aisle.harness.typed_worker_launch import launch_typed_worker

    inputs = launch_inputs(tmp_path)
    controller, participant, _ = _inputs(tmp_path / "l2")
    source = participant / "src/aisle/nodes/l2_pose.py"
    source.write_text("from aisle.turn_node import Node\nfor event in Node():\n    pass\n")
    shutil.rmtree(inputs["bundle"])
    inputs["bundle_manifest"] = build_execution_bundle(
        controller, participant, inputs["bundle"], task_surface=PILOT
    )
    inputs["module"] = module
    result = launch_typed_worker(**inputs)
    if module == "aisle.nodes.l2_pose":
        assert result["ok"], result
        assert result["worker"]["input_exhausted"]
    else:
        assert not result["ok"]
        assert result["stage"] == "preflight"
        assert "outside the authored Python surface" in result["error"]
