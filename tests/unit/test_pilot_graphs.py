"""MON-4/BND-2/BND-3: the selected paired graphs bind public pilot inputs."""

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from aisle.harness.matched_surface import PILOT_L2_SURFACE, task_surface
from aisle.harness.validate import validate

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
def test_pilot_graph_routes_public_inputs_and_compiles_committed_plan(arm):
    """MON-4/BND-2/BND-3: both actual graphs use RGB and the same public boundary."""
    surface = task_surface(PILOT_L2_SURFACE)
    path = ROOT / getattr(surface, f"{arm}_graph")
    graph = yaml.safe_load(path.read_text())
    nodes = {node["id"]: node for node in graph["nodes"]}
    assert nodes["dora-genesis"]["env"]["AISLE_PERCEPTION"] == "L2"
    assert "seg_overhead" not in nodes["dora-genesis"]["outputs"]
    assert nodes["rollout-client"]["env"]["AISLE_EPISODE_LIFECYCLE"] == "fixed-horizon-t1-v1"
    projection = nodes["pilot-policy-surface"]
    assert projection["path"] == "../src/aisle/harness/pilot_policy_surface.py"
    assert set(projection["inputs"]) == {"bridge_info", "episode_goal", "reset_done", "turn"}
    policy = (
        ("detected-pose", "ik-trajectory", "task-state-machine")
        if arm == "typed"
        else ("monolith-broker",)
    )
    for name in policy:
        inputs = nodes[name]["inputs"]
        assert "episode_result" not in inputs
        for topic in ("bridge_info", "episode_goal", "reset_done"):
            if topic in inputs:
                assert inputs[topic]["source"] == f"pilot-policy-surface/{topic}"
    perception = nodes["detected-pose" if arm == "typed" else "monolith-broker"]
    assert perception["inputs"]["rgb_overhead"]["source"] == "dora-genesis/rgb_overhead"
    assert "seg_overhead" not in perception["inputs"]
    report = validate(path, ROOT, "franka", allow_unproven=True)
    assert report["ok"], report["errors"]


def test_pilot_module_requests_rgb_primitive_and_waits_for_public_lifecycle():
    """MON-4/BND-2/BND-3: the actual module uses RGB and ignores oracle verdicts."""
    path = ROOT / task_surface(PILOT_L2_SURFACE).monolithic_module
    spec = importlib.util.spec_from_file_location("pilot_candidate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    session = SimpleNamespace(
        on_rgb=lambda stamp, payload: calls.append(("rgb", stamp, payload)),
        on_target_request=lambda payload: True,
        on_reset_done=lambda: calls.append(("reset",)),
    )
    controller = module.Controller(SimpleNamespace(l2_pose_session=lambda: session), calls.append)
    goal = {"target_med": "ibuprofen", "tier": "T1", "timeout_s": 30}
    controller.on_event({"name": "episode_goal", "payload": goal, "sim_time_ns": 0})
    controller.on_event({"name": "rgb_overhead", "payload": "frame", "sim_time_ns": 1})
    assert calls == [("rgb", 1, "frame")]
    controller.on_event({"name": "episode_result", "payload": {"success": True}, "sim_time_ns": 2})
    assert controller.goal == goal
    controller.on_event({"name": "reset_done", "payload": None, "sim_time_ns": 30_000_000_000})
    assert calls[-1] == ("reset",)
    assert controller.goal is None


def test_selected_pilot_documents_match_graphs_without_claiming_expert_parity():
    """MON-1/MON-4/MON-9: document actual L2 fields and retain independent-review gaps."""
    from aisle.harness.monolith import interface_report, table_report

    interface = interface_report(ROOT, task_surface=PILOT_L2_SURFACE)
    assert interface["ok"], interface
    table = table_report(ROOT, write=False, task_surface=PILOT_L2_SURFACE)
    assert table["ok"], table
    documents = ROOT / task_surface(PILOT_L2_SURFACE).docs_directory
    provenance = json.loads((documents / "experts.json").read_text())
    assert provenance["frozen"] is False
    assert all(artifact["blind"] is False for artifact in provenance["artifacts"])
    allowlist = json.loads((documents / "allowlist.json").read_text())
    assert "src/aisle/harness/pilot_policy_surface.py" not in allowlist["typed"]["editable"]
    assert "registry/manifests/pilot-policy-surface.yaml" not in allowlist["typed"]["editable"]


@pytest.mark.parametrize("command", ["table", "interface", "parity"])
def test_pilot_document_cli_passes_selected_surface(monkeypatch, capsys, command):
    """MON-1/MON-4: published regeneration and checking commands select pilot records."""
    from aisle.harness import cli, monolith

    calls = []
    monkeypatch.setattr(
        monolith, f"{command}_report", lambda *a, **kw: calls.append(kw) or {"ok": True}
    )
    argv = ["harness", "monolith", command, "--task-surface", PILOT_L2_SURFACE]
    if command == "parity":
        argv.extend(["--typed", "/tmp/typed.jsonl", "--monolithic", "/tmp/mono.jsonl"])
    monkeypatch.setattr("sys.argv", argv)
    assert cli.main() == 0
    assert calls[0]["task_surface"] == PILOT_L2_SURFACE
    assert json.loads(capsys.readouterr().out)["ok"]


def test_actual_pilot_snapshot_runs_closed_validator_bundle(tmp_path):
    """MON-8/MON-13: selected real graph validates from sealed data and closed code.

    This is a trusted fixture subprocess, not an external confinement attestation.
    """
    from aisle.harness.typed_snapshot import build_typed_validation_snapshot
    from aisle.harness.typed_validation import build_validation_bundle, validation_command

    surface = task_surface(PILOT_L2_SURFACE)
    participant = tmp_path / "participant"
    allowlist = json.loads((ROOT / surface.docs_directory / "allowlist.json").read_text())
    for name in allowlist["typed"]["editable"]:
        destination = participant / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    snapshot = tmp_path / "snapshot"
    record = build_typed_validation_snapshot(
        ROOT, participant, snapshot, task_surface=PILOT_L2_SURFACE
    )
    assert record["files"]["src/aisle/harness/pilot_policy_surface.py"]["origin"] == "controller"
    bundle = tmp_path / "validator"
    manifest = build_validation_bundle(bundle)
    command = validation_command(sys.executable, bundle, manifest, snapshot, record, "franka")
    result = subprocess.run(command, cwd=bundle, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["ok"]


def test_selected_l2_validation_stages_only_authored_workers(tmp_path, monkeypatch):
    """MON-6/MON-13: real validation gates L2 host staging; projection stays trusted.

    Synthetic adapter proves plumbing, not OS isolation or collection readiness.
    """
    import test_typed_validation_launch
    from test_typed_graph_stage import _declarations
    from test_typed_validation_launch import _inputs

    from aisle.harness.typed_graph_stage import stage_typed_graph, verify_graph_stage
    from aisle.harness.typed_node_host import load_host_config
    from aisle.harness.typed_validation import run_validation

    # Cache authority is supplied separately by the runtime declaration. Here
    # prove its prepared environment is captured before validation and carried
    # into the worker configuration, never borrowed from the host environment.
    cache_environment = {
        "HF_HOME": str(tmp_path / "public-model-cache"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    original_view = test_typed_validation_launch._view

    def prepared_view(*args, **kwargs):
        view = original_view(*args, **kwargs)
        graph_path = view / task_surface(PILOT_L2_SURFACE).typed_graph
        graph = yaml.safe_load(graph_path.read_text())
        node = next(n for n in graph["nodes"] if n["id"] == "detected-pose")
        node["env"].update(cache_environment)
        graph_path.write_text(yaml.safe_dump(graph, sort_keys=False))
        return view

    monkeypatch.setattr(test_typed_validation_launch, "_view", prepared_view)
    inputs = _inputs(tmp_path, worker_packages=True, task_surface=PILOT_L2_SURFACE)
    result = run_validation(**inputs)
    assert result["ok"], result
    output = tmp_path / "private/staged"
    record = stage_typed_graph(
        ROOT,
        inputs["snapshot"],
        inputs["snapshot_record"],
        inputs["output"],
        _declarations(inputs),
        output,
    )
    assert set(record["hosts"]) == {
        "detected-pose",
        "grasp-planner-topdown",
        "ik-trajectory",
        "task-state-machine",
    }
    graph = yaml.safe_load((output / "graph.yaml").read_text())
    projection = next(n for n in graph["nodes"] if n["id"] == "pilot-policy-surface")
    assert projection["path"] == str(ROOT / "src/aisle/harness/pilot_policy_surface.py")
    binding = record["hosts"]["detected-pose"]
    host = load_host_config(binding["config_path"], binding["config_sha256"])
    assert all(
        host["configuration"]["environment"][key] == value
        for key, value in cache_environment.items()
    )
    assert record["execution_authorized"] is False
    verify_graph_stage(output, record)


@pytest.mark.parametrize("mutation", ["oracle", "raw_goal", "raw_calibration", "reset", "judge"])
def test_pilot_host_replacement_refuses_private_route_changes(mutation):
    """BND-2/BND-3/MON-6: topology editing cannot reconnect private controller inputs."""
    from aisle.harness.typed_graph_hosts import GraphHostError, replace_authored_nodes

    surface = task_surface(PILOT_L2_SURFACE)
    baseline = yaml.safe_load((ROOT / surface.typed_graph).read_text())
    bindings = {}
    for node in baseline["nodes"]:
        source = node["path"].removeprefix("../")
        if source not in surface.participant_files:
            continue
        bindings[node["id"]] = {
            "config_path": f"/private/controller/{node['id']}.json",
            "config_sha256": "1" * 64,
            "module": source[4:-3].replace("/", "."),
            "outputs": [name for name in node["outputs"] if name != "turn_done"],
            "wall_outputs": [],
            "configuration": {"environment": dict(node["env"]), "arguments": []},
        }
    authored = copy.deepcopy(baseline)
    nodes = {node["id"]: node for node in authored["nodes"]}
    if mutation in {"oracle", "raw_goal", "raw_calibration"}:
        source = {
            "oracle": "verifier-oracle/episode_result",
            "raw_goal": "rollout-client/episode_goal",
            "raw_calibration": "dora-genesis/bridge_info",
        }[mutation]
        nodes["task-state-machine"]["inputs"]["episode_goal"]["source"] = source
    elif mutation == "reset":
        nodes["reset"]["inputs"]["reset"]["source"] = "task-state-machine/target_request"
    else:
        nodes["verifier-oracle"]["inputs"]["episode_goal"]["source"] = (
            "task-state-machine/target_request"
        )
    with pytest.raises(GraphHostError, match="pilot"):
        replace_authored_nodes(authored, baseline, bindings, ROOT, task_surface=PILOT_L2_SURFACE)


def test_pilot_admission_revalidates_selected_documents_and_candidate_bytes(tmp_path):
    """MON-8/MON-13: actual paired admission retains L2 identity without claiming readiness."""
    from test_matched_session import prepared_pair

    from aisle.harness.matched_session import AdmissionError, admit_pair, verify_plan

    control, candidates, roots = prepared_pair(tmp_path, task_surface=PILOT_L2_SURFACE)
    development = {
        "schema_version": "aisle.matched-development.v1",
        "purpose": "expert_parity",
        "tier": "T1",
        "embodiment": "franka",
        "verifier": "oracle",
        "reset": "teleport",
        "seeds": [0],
        "run_ceiling": 1,
        "episode_ceiling": 1,
        "timeout_s": 30,
        "task_surface": PILOT_L2_SURFACE,
    }
    record = admit_pair(control, candidates, roots, development=development)
    assert record["task_surface"] == PILOT_L2_SURFACE
    assert record["confirmatory_ready"] is False
    assert verify_plan(record, control, roots) == record
    module = roots["monolithic"] / task_surface(PILOT_L2_SURFACE).monolithic_module
    module.write_text(module.read_text() + "\n# changed after admission\n")
    with pytest.raises(AdmissionError, match="drift"):
        verify_plan(record, control, roots)
