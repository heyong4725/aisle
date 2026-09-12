"""Private graph/config staging after normal validation; execution requires admission."""

from __future__ import annotations

import copy
import hashlib
import json
import stat
from pathlib import Path

import yaml

from aisle.harness.matched_surface import record_surface
from aisle.harness.typed_execution_bundle import verify_execution_bundle
from aisle.harness.typed_graph_hosts import (
    _json_equal,
    _source,
    node_configuration,
    replace_authored_nodes,
)
from aisle.harness.typed_node_host import load_host_config, preflight_host_config
from aisle.harness.typed_snapshot import _digest, _read, verify_typed_validation_snapshot


class StageError(ValueError):
    """Validated source and graph staging inputs cannot be bound together."""


def _validation(output, snapshot_record):
    result = json.loads(_read(output, "result.json"))
    if (
        result.get("schema_version") != "aisle.typed-validation-result.v1"
        or result.get("snapshot_id") != snapshot_record["immutable_id"]
        or result.get("classification") != "tool_result"
        or result.get("ok") is not True
        or result.get("stage") != "done"
        or not _json_equal(result.get("process"), {"rc": 0, "timed_out": False})
        or result.get("result", {}).get("ok") is not True
        or not _json_equal(json.loads(_read(output, "snapshot.json")), snapshot_record)
    ):
        raise StageError("staging requires successful validation of this exact snapshot")
    return result


def stage_typed_graph(
    controller_root, snapshot, snapshot_record, validation_output, launches, output
):
    """Build reviewable private artifacts; do not attach Dora or execute authored code.

    Launch declarations are serialized controller inputs. Each generated host
    must still pass its independent policy/runtime/adapter preflight at execution.
    """
    controller, snapshot, validation_output, output = (
        Path(p).absolute() for p in (controller_root, snapshot, validation_output, output)
    )
    try:
        if any(p.resolve() != p for p in (controller, snapshot, validation_output, output)):
            raise StageError("staging roots must be canonical")
        if any(
            output.is_relative_to(p) or p.is_relative_to(output)
            for p in (
                controller,
                snapshot,
                validation_output,
                Path(snapshot_record["participant_root"]),
            )
        ):
            raise StageError("staging overlaps source or validation evidence")
        if snapshot_record["controller_root"] != str(controller):
            raise StageError("validation snapshot belongs to another controller root")
        verify_typed_validation_snapshot(snapshot, snapshot_record)
        surface = record_surface(snapshot_record)
        validation = _validation(validation_output, snapshot_record)
        baseline_bytes = _read(controller, surface.typed_graph)
        baseline = yaml.safe_load(baseline_bytes)
        authored_bytes = _read(snapshot, surface.typed_graph)
        authored = yaml.safe_load(authored_bytes)
        if type(launches) is not dict or not launches:
            raise StageError("worker launch declarations are missing")
        for launch in launches.values():
            verify_execution_bundle(launch["bundle"], launch["bundle_manifest"])
            if record_surface(launch["bundle_manifest"]) != surface:
                raise StageError("worker bundle task surface differs from snapshot")
            for name in surface.participant_files:
                if (
                    launch["bundle_manifest"]["files"][name]["sha256"]
                    != snapshot_record["files"][name]["sha256"]
                ):
                    raise StageError("worker source differs from validated snapshot")
            policy = launch["policy"]
            for asset in (output, validation_output):
                if any(
                    asset.is_relative_to(Path(p)) or Path(p).is_relative_to(asset)
                    for key in ("visible_roots", "runtime_read_roots", "output_roots")
                    for p in policy[key]
                ) or not any(asset.is_relative_to(Path(p)) for p in policy["hidden_roots"]):
                    raise StageError("staging/validation evidence is not private from workers")
        bindings, configs, expansions = {}, {}, {}
        for node in authored["nodes"]:
            node_id = node["id"]
            if node_id not in launches:
                continue
            source = _source(node, task_surface=surface.identity)
            if source is None:
                raise StageError("worker implementation is outside the typed source surface")
            expansion = launches[node_id]["environment"]
            settings = node_configuration(node, expansion_environment=expansion)
            name = hashlib.sha256(node_id.encode()).hexdigest()
            path = output / "hosts" / f"{name}.json"
            config = {
                "schema_version": "aisle.typed-node-host.v1",
                "purpose": "expert_parity",
                "node_id": node_id,
                "module": source[4:-3].replace("/", "."),
                "outputs": [p for p in node["outputs"] if p != "turn_done"],
                "wall_outputs": [
                    p
                    for p in settings["environment"].get("AISLE_TURN_WALL_OUTPUTS", "").split(",")
                    if p
                ],
                "configuration": settings,
                "launch": copy.deepcopy(launches[node_id]),
                "output": str(output / "workers" / name),
            }
            raw = (json.dumps(config, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
            bindings[node_id] = {
                "config_path": str(path),
                "config_sha256": hashlib.sha256(raw).hexdigest(),
                **{
                    key: config[key]
                    for key in ("module", "outputs", "wall_outputs", "configuration")
                },
            }
            configs[path.relative_to(output).as_posix()] = raw
            expansions[node_id] = expansion
        graph = replace_authored_nodes(
            authored,
            baseline,
            bindings,
            controller,
            expansion_environments=expansions,
            task_surface=surface.identity,
        )
        # Resolve transport paths only after comparison against the authored graph.
        # The validated turn plan is copied byte-for-byte, never recompiled here.
        for node in graph["nodes"]:
            if node["id"] not in bindings:
                node["path"] = str((controller / "graphs" / node["path"]).resolve())
                if "AISLE_TURN_PLAN" in node.get("env", {}):
                    node["env"]["AISLE_TURN_PLAN"] = str(output / "turn-plan.json")
        files = {
            **configs,
            "graph.yaml": yaml.safe_dump(graph, sort_keys=False).encode(),
            "turn-plan.json": _read(snapshot, surface.typed_turn_plan),
        }
        output.mkdir(parents=True, exist_ok=False)
        for name, data in files.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            target.chmod(0o444)
        for binding in bindings.values():
            load_host_config(binding["config_path"], binding["config_sha256"])
        verify_typed_validation_snapshot(snapshot, snapshot_record)
        if (
            not _json_equal(_validation(validation_output, snapshot_record), validation)
            or _read(controller, surface.typed_graph) != baseline_bytes
        ):
            raise StageError("validation or baseline changed during staging")
        for launch in launches.values():
            verify_execution_bundle(launch["bundle"], launch["bundle_manifest"])
        record = {
            "schema_version": "aisle.typed-graph-stage.v1",
            "stage_root": str(output),
            "snapshot_id": snapshot_record["immutable_id"],
            "snapshot_record": copy.deepcopy(snapshot_record),
            "authored_graph_sha256": hashlib.sha256(authored_bytes).hexdigest(),
            "controller_root": str(controller),
            "source_roots": [str(controller), str(snapshot), snapshot_record["participant_root"]],
            "validation": validation,
            "baseline_sha256": hashlib.sha256(baseline_bytes).hexdigest(),
            "hosts": bindings,
            "expansion_environments": expansions,
            "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
            "execution_authorized": False,
        }
        record["immutable_id"] = _digest(record)
        with (output / "stage.json").open("x") as stream:
            stream.write(json.dumps(record, indent=2, allow_nan=False) + "\n")
        verify_graph_stage(output, record)
        return record
    except StageError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise StageError(f"typed staging refused: {exc}") from exc


def verify_graph_stage(output, record):
    """Verify the closed preparation inventory before launching any host."""
    return _verify_graph_stage(output, record)


def _verify_graph_stage(output, record, worker_roots=()):
    """Verify static assets, optionally admitting declared postflight evidence trees."""
    try:
        output = Path(output).absolute()
        expected = dict(record)
        identity = expected.pop("immutable_id")
        if (
            output.resolve() != output
            or record["stage_root"] != str(output)
            or record["schema_version"] != "aisle.typed-graph-stage.v1"
            or _digest(expected) != identity
            or not _json_equal(json.loads(_read(output, "stage.json")), record)
        ):
            raise StageError("graph stage identity differs")
        snapshot_record = record["snapshot_record"]
        verify_typed_validation_snapshot(snapshot_record["snapshot_root"], snapshot_record)
        if (
            record["snapshot_id"] != snapshot_record["immutable_id"]
            or record["authored_graph_sha256"]
            != snapshot_record["files"][record_surface(snapshot_record).typed_graph]["sha256"]
            or record["controller_root"] != snapshot_record["controller_root"]
        ):
            raise StageError("graph stage snapshot binding differs")
        names = set(record["files"]) | {"stage.json"}
        directories = {p.as_posix() for name in names for p in Path(name).parents if p != Path(".")}
        observed = set()
        for path in output.rglob("*"):
            name = path.relative_to(output).as_posix()
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise StageError("graph stage has a redirected or special entry")
            if any(path.is_relative_to(p) for p in worker_roots):
                continue
            if stat.S_ISDIR(mode) and any(p.is_relative_to(path) for p in worker_roots):
                continue
            if stat.S_ISDIR(mode) and name in directories:
                continue
            if not stat.S_ISREG(mode) or name not in names:
                raise StageError("graph stage has an unexpected entry")
            if name != "stage.json" and (
                stat.S_IMODE(mode) != 0o444
                or hashlib.sha256(_read(output, name)).hexdigest() != record["files"][name]
            ):
                raise StageError("graph stage file drift")
            observed.add(name)
        if observed != names:
            raise StageError("graph stage files are missing")
        for binding in record["hosts"].values():
            path = Path(binding["config_path"])
            if path.parent != output / "hosts":
                raise StageError("host config is outside staging")
            load_host_config(path, binding["config_sha256"])
    except StageError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise StageError(f"invalid graph stage: {exc}") from exc


def preflight_graph_stage(output, record):
    """Check every host and cross-worker authority before starting the Dora graph.

    This check does not admit a session, establish an external resource boundary,
    or authorize scoring. Each host repeats its own preflight when Dora starts it.
    """
    try:
        verify_graph_stage(output, record)
        hosts = {}
        launches = {}
        runtime = None
        for node_id, binding in record["hosts"].items():
            config, launch = preflight_host_config(binding["config_path"], binding["config_sha256"])
            if config["node_id"] != node_id or any(
                config[key] != binding[key]
                for key in ("module", "outputs", "wall_outputs", "configuration")
            ):
                raise StageError("host declaration differs from the staged graph binding")
            policy = launch["policy"]
            for source in record["source_roots"]:
                if not any(Path(source).is_relative_to(p) for p in policy.hidden_roots):
                    raise StageError("worker policy does not hide all staged source roots")
            identity = {
                "python_sha256": launch["python_sha256"],
                "runtime_id": launch["runtime_record"]["immutable_id"],
            }
            if runtime is not None and identity != runtime:
                raise StageError("workers do not share one pinned interpreter/runtime")
            runtime = identity
            hosts[node_id] = {
                "config_sha256": binding["config_sha256"],
                "output": config["output"],
                "home": launch["environment_record"]["home"],
            }
            launches[node_id] = launch
        for node_id, launch in launches.items():
            private_home = Path(launch["environment_record"]["home"])
            for other_id, other in launches.items():
                if other_id == node_id:
                    continue
                policy = other["policy"]
                if any(
                    private_home.is_relative_to(p) or p.is_relative_to(private_home)
                    for p in (
                        *policy.visible_roots,
                        *policy.runtime_read_roots,
                        *policy.output_roots,
                    )
                ):
                    raise StageError("worker private state overlaps another worker's authority")
                evidence = Path(hosts[node_id]["output"])
                if not any(evidence.is_relative_to(p) for p in policy.hidden_roots):
                    raise StageError("worker evidence is not hidden from every peer")
        verify_graph_stage(output, record)
        result = {
            "schema_version": "aisle.typed-graph-preflight.v1",
            "stage_id": record["immutable_id"],
            "hosts": hosts,
            "runtime": runtime,
            "session_admitted": False,
            "confirmatory_ready": False,
        }
        result["immutable_id"] = _digest(result)
        return result
    except StageError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise StageError(f"typed graph preflight refused: {exc}") from exc


def transport_for_instrumentation(output, record, *, authored_bytes, graph, controller_root):
    """Return the staged transport and validated registry for the normal trace instrumenter."""
    try:
        verify_graph_stage(output, record)
        snapshot = Path(record["snapshot_record"]["snapshot_root"])
        if (
            hashlib.sha256(authored_bytes).hexdigest() != record["authored_graph_sha256"]
            or Path(graph).absolute()
            != snapshot / record_surface(record["snapshot_record"]).typed_graph
        ):
            raise StageError("transport stage differs from the authored graph snapshot")
        if str(Path(controller_root).resolve()) != record["controller_root"]:
            raise StageError("transport stage belongs to another controller")
        preflight_graph_stage(output, record)
        transport = _read(Path(output), "graph.yaml")
        if hashlib.sha256(transport).hexdigest() != record["files"]["graph.yaml"]:
            raise StageError("transport graph drifted during instrumentation preflight")
        return transport.decode("utf-8"), snapshot
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"typed transport instrumentation refused: {exc}") from exc


def validation_for_rollout_gates(
    output, record, *, authored_bytes, graph, controller_root, embodiment
):
    """Bind the retained normal-validation result to the requested rollout inputs.

    Caller still runs ordinary environment, distribution, ledger, idea and
    lockstep gates. This is not a generic successful-report override.
    """
    verify_graph_stage(output, record)
    snapshot = Path(record["snapshot_record"]["snapshot_root"])
    if (
        record["validation"].get("embodiment") != embodiment
        or hashlib.sha256(authored_bytes).hexdigest() != record["authored_graph_sha256"]
        or Path(graph).absolute()
        != snapshot / record_surface(record["snapshot_record"]).typed_graph
        or str(Path(controller_root).resolve()) != record["controller_root"]
        or record["validation"]["result"].get("graph") != str(Path(graph).absolute())
    ):
        raise StageError("retained validation differs from the requested authored rollout")
    validation = record["validation"]
    if (
        validation.get("classification") != "tool_result"
        or validation.get("ok") is not True
        or validation.get("snapshot_id") != record["snapshot_id"]
        or validation.get("result", {}).get("ok") is not True
    ):
        raise StageError("rollout has no successful bound typed validation")
    preflight_graph_stage(output, record)
    return copy.deepcopy(validation["result"]), snapshot


def select_rollout_stage(
    factory, index, history, *, authored_bytes, graph, controller_root, embodiment
):
    """Select a fresh stage for one incarnation of the same authored rollout."""
    try:
        if type(index) is not int or index != len(history):
            raise StageError("typed launch index is not sequential")
        stage, record = factory(index)
        _, registry_root = validation_for_rollout_gates(
            stage,
            record,
            authored_bytes=authored_bytes,
            graph=graph,
            controller_root=controller_root,
            embodiment=embodiment,
        )
        preflight = preflight_graph_stage(stage, record)
        row = {
            "launch": index,
            "stage_root": str(Path(stage).absolute()),
            "stage_id": record["immutable_id"],
            "snapshot_id": record["snapshot_id"],
            "runtime": preflight["runtime"],
        }
        if any(
            row["stage_root"] == prior["stage_root"] or row["stage_id"] == prior["stage_id"]
            for prior in history
        ):
            raise StageError("typed stage cannot be reused for a relaunch")
        if history and any(row[key] != history[0][key] for key in ("snapshot_id", "runtime")):
            raise StageError("typed relaunch changed its authored snapshot or runtime")
        history.append(row)
        return (Path(stage), record), registry_root
    except Exception as exc:
        raise RuntimeError(f"typed stage selection refused: {exc}") from exc
