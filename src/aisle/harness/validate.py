"""Dataflow validator (SPEC 060 VAL-1..6, CON-8).

Loads a dora dataflow YAML plus every registry manifest and rejects graphs
that cannot run safely: unresolved node ids, duplicate ids, missing
producers, schema mismatches against the CAP-2 vocabulary, oracle leaks
(VAL-6), ungated motion (VAL-5), and motion nodes without evalcards.
Hints are the research agent's learning signal: every error names a registry
capability or a concrete fix. No genesis or dora imports (unit territory).
"""

import difflib
import json
import tomllib
from collections import Counter
from pathlib import Path

import yaml

from aisle.harness.registry import (
    load_capability_schema,
    load_manifests,
    load_vocabulary,
    manifest_schema_errors,
)

# base_cmd is a motion sink too (SPEC 210 MOB-3): a mobile base command
# reaching the bridge MUST traverse the budget guard, or a producer could
# drive the base unguarded.
MOTION_SINK_PORTS = {"joint_cmd", "gripper_cmd", "base_cmd"}
GUARD_ID = "budget-guard"
RATE_BAND = 0.2  # TC-4: rates are contracts within ±20%
# MOB-4: each embodiment profile resolves to an ARM kind. `mobile` is the
# franka arm on a differential-drive base, so franka-arm capabilities work
# unchanged under `mobile`; only base-requiring nodes distinguish them.
EMBODIMENT_ARM = {"franka": "franka", "so101": "so101", "mobile": "franka"}


def _entry(code: str, where: dict, detail: str, hint: str) -> dict:
    return {"code": code, **where, "detail": detail, "hint": hint}


def _closest(name: str, candidates: list[str]) -> str:
    # cutoff 0.75: a weak match ("warp-drive" ~ "arm-driver-sim") is a
    # misleading hint, worse than none
    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=0.75)
    return matches[0] if matches else ""


def _backward_sources(node: dict) -> list[str | None]:
    """Backward-edge sources of a node; None for timers/dora/malformed."""
    sources: list[str | None] = []
    for raw in (node.get("inputs") or {}).values():
        source = raw.get("source") if isinstance(raw, dict) else raw
        if isinstance(source, str) and source and not source.startswith("dora/"):
            sources.append(source)
        else:
            sources.append(None)
    return sources


def _guard_resolved(out_port: str, graph_nodes: dict, manifests: dict) -> bool:
    """The guard hop counts only when fully resolved: a budget-guard graph
    node AND manifest exist, and the referenced output is declared by both
    — a manifest alone (or a phantom output) is not a gate."""
    node = graph_nodes.get(GUARD_ID)
    manifest = manifests.get(GUARD_ID)
    return (
        node is not None
        and manifest is not None
        and out_port in (node.get("outputs") or [])
        and out_port in (manifest.get("outputs") or {})
    )


def _gated_source(
    source: str | None, graph_nodes: dict, manifests: dict, memo: dict, stack: set
) -> bool:
    """VAL-5 traversal semantics: True iff EVERY backward path from this
    source reaches the fully resolved budget-guard before terminating at a
    root, timer, or unresolvable source. Conservative dataflow assumption:
    all of a node's inputs feed its outputs, so one unguarded input taints
    the node; cycles without a guard on them are ungated."""
    if source is None:
        return False
    src_id, _, out_port = source.partition("/")
    if src_id == GUARD_ID:
        return _guard_resolved(out_port, graph_nodes, manifests)
    if src_id in memo:
        return memo[src_id]
    if src_id in stack:
        return False
    node = graph_nodes.get(src_id)
    if node is None or not (node.get("inputs") or {}):
        memo[src_id] = False  # unresolvable source or root: path ends unguarded
        return False
    stack.add(src_id)
    result = all(
        _gated_source(upstream, graph_nodes, manifests, memo, stack)
        for upstream in _backward_sources(node)
    )
    stack.discard(src_id)
    memo[src_id] = result
    return result


def load_graph(path: Path) -> tuple[list | None, list[dict]]:
    """Parse the dataflow YAML and check its structure; returns (nodes, errors)."""
    where = {"node": str(path)}

    def invalid(detail: str, hint: str) -> tuple[None, list[dict]]:
        return None, [_entry("GRAPH_INVALID", where, detail, hint)]

    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError) as exc:
        return invalid(f"cannot read graph: {exc}", "pass a readable UTF-8 dataflow YAML path")
    except yaml.YAMLError as exc:
        return invalid(f"unparseable YAML: {exc}", "fix the YAML syntax; see graphs/ for examples")
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, list) or not all(isinstance(n, dict) for n in nodes):
        return invalid(
            "graph must be a mapping with a `nodes` list of mappings",
            "structure the file as {nodes: [{id, inputs, outputs}, ...]}",
        )
    if not nodes:
        return invalid(
            "the `nodes` list is empty",
            "add at least one node; a graph that runs nothing never validates",
        )
    structural: list[dict] = []
    for index, node in enumerate(nodes):
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            structural.append(
                _entry(
                    "GRAPH_INVALID",
                    {"node": f"nodes[{index}]"},
                    f"node id must be a non-empty string, got {node_id!r}",
                    "give every node a string id matching a registry manifest",
                )
            )
        inputs = node.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            structural.append(
                _entry(
                    "GRAPH_INVALID",
                    {"node": str(node_id)},
                    f"inputs must be a mapping of port -> source, got {type(inputs).__name__}",
                    "write inputs as {port: producer-id/output}",
                )
            )
        outputs = node.get("outputs")
        bad_outputs = not isinstance(outputs, list) or not all(
            isinstance(o, str) and o for o in outputs
        )
        if outputs is not None and bad_outputs:
            structural.append(
                _entry(
                    "GRAPH_INVALID",
                    {"node": str(node_id)},
                    f"outputs must be a list of non-empty strings, got {outputs!r}",
                    "write outputs as a YAML list of output port names",
                )
            )
        elif outputs is not None and len(set(outputs)) != len(outputs):
            duplicates = sorted({o for o in outputs if outputs.count(o) > 1})
            structural.append(
                _entry(
                    "GRAPH_INVALID",
                    {"node": str(node_id)},
                    f"duplicate output ports {duplicates} — each output may appear once",
                    "remove the repeated entries from the outputs list",
                )
            )
    if structural:
        return None, structural
    return nodes, []


def validate_nodes(
    nodes: list[dict],
    manifests: dict[str, dict],
    vocabulary: set[str],
    embodiment: str,
    allow_unproven: bool,
) -> tuple[list[dict], list[dict]]:
    errors: list[dict] = []
    warnings: list[dict] = []
    for node_id, count in sorted(Counter(n["id"] for n in nodes).items()):
        if count > 1:
            errors.append(
                _entry(
                    "NODE_ID_DUPLICATE",
                    {"node": node_id},
                    f"node id {node_id!r} appears {count} times",
                    "give each node instance a unique id matching one manifest",
                )
            )

    graph_nodes = {n["id"]: n for n in nodes}
    for node in nodes:
        node_id = node["id"]
        manifest = manifests.get(node_id)
        if manifest is None:
            close = _closest(node_id, list(manifests))
            suggestion = (
                f"rename the node to {close!r}"
                if close
                else "no similar manifest id exists; find one with: "
                "python -m aisle.harness.registry search --provides <capability>"
            )
            errors.append(
                _entry(
                    "MANIFEST_MISSING",
                    {"node": node_id},
                    f"no manifest for node id {node_id!r}",
                    suggestion,
                )
            )
            # VAL-6 is manifest-based: a node WITHOUT a manifest is never an
            # authorized verifier, so oracle consumption must still surface
            # and not hide behind MANIFEST_MISSING.
            for port, source in (node.get("inputs") or {}).items():
                if isinstance(source, dict):
                    source = source.get("source")
                if isinstance(source, str) and source.endswith("/oracle_state"):
                    errors.append(
                        _entry(
                            "ORACLE_LEAK",
                            {"edge": f"{source} -> {node_id}/{port}"},
                            f"oracle_state consumed by {node_id!r}, which has no "
                            "manifest and so cannot be an authorized verifier (VAL-6)",
                            "only verifier-* manifests may read ground truth",
                        )
                    )
            continue
        # VAL-4: every schema name a graph node's manifest references must be
        # in the vocabulary — including unwired ports; never silently passed
        for direction in ("inputs", "outputs"):
            for port, spec in (manifest.get(direction) or {}).items():
                schema = spec.get("schema") if isinstance(spec, dict) else None
                if schema is not None and schema not in vocabulary:
                    errors.append(
                        _entry(
                            "SCHEMA_UNKNOWN",
                            {"node": node_id},
                            f"{direction}/{port}: schema {schema!r} is not in "
                            "registry/schema/schemas.toml",
                            "add it via a Class C schema-vocabulary change (CAP-2) or fix the name",
                        )
                    )
        # every graph-declared output must exist in the manifest, consumed
        # or not — the graph cannot invent ports the typed contract lacks
        manifest_outputs = manifest.get("outputs") or {}
        for out in node.get("outputs") or []:
            if out not in manifest_outputs:
                errors.append(
                    _entry(
                        "SCHEMA_MISMATCH",
                        {"node": node_id},
                        f"graph declares output {out!r} but {node_id}'s manifest does not",
                        f"use one of the manifest outputs {sorted(manifest_outputs)}, "
                        "or extend the manifest (Class B change)",
                    )
                )
        # MOB-4: an embodiment resolves to an ARM kind; `mobile` runs the
        # franka arm on a base, so a franka-arm graph validates unchanged
        # under `mobile`. Arm nodes are checked against the resolved arm.
        arm_kind = EMBODIMENT_ARM.get(embodiment, embodiment)
        arms = manifest.get("embodiment", {}).get("arm", [])
        if arms and arm_kind not in arms:
            errors.append(
                _entry(
                    "EMBODIMENT_MISMATCH",
                    {"node": node_id},
                    f"{node_id} supports arms {arms}, graph targets "
                    f"{embodiment!r} (arm {arm_kind!r})",
                    f"swap in a capability supporting {arm_kind!r} or change --embodiment",
                )
            )
        # MOB-4: a base-requiring node lists the base-providing embodiments
        # it needs; on a fixed-base graph (no base) that is a mismatch.
        base = manifest.get("embodiment", {}).get("base", [])
        if base and embodiment not in base:
            errors.append(
                _entry(
                    "EMBODIMENT_MISMATCH",
                    {"node": node_id},
                    f"{node_id} requires a base profile {base}, graph targets {embodiment!r}",
                    f"target one of {base} (a mobile base profile), "
                    "or drop the base-requiring node",
                )
            )
        if manifest.get("safety_class") == "motion" and manifest.get("eval") is None:
            entry = _entry(
                "EVAL_MISSING_FOR_MOTION",
                {"node": node_id},
                f"{node_id} is safety_class=motion with no evalcard (CAP-6)",
                "attach an evalcard from its eval suite before motion use",
            )
            (warnings if allow_unproven else errors).append(entry)

        for port, source in (node.get("inputs") or {}).items():
            _validate_edge(
                node, manifest, port, source, graph_nodes, manifests, vocabulary, errors, warnings
            )
    return errors, warnings


def _parse_timer_hz(source: str) -> float | None:
    """Rate of a well-formed dora/timer/millis/<N> source, else None."""
    parts = source.split("/")
    if len(parts) == 4 and parts[1] == "timer" and parts[2] == "millis":
        if parts[3].isdigit() and int(parts[3]) > 0:
            return 1000.0 / int(parts[3])
    return None


def _validate_edge(
    node, manifest, port, source, graph_nodes, manifests, vocabulary, errors, warnings
) -> None:
    node_id = node["id"]
    if isinstance(source, dict):  # dora extended input form {source: ..., queue_size: N}
        source = source.get("source")
    if not isinstance(source, str) or not source:
        errors.append(
            _entry(
                "GRAPH_INVALID",
                {"edge": f"{node_id}/{port}"},
                f"input source must be a string or {{source: ...}} mapping, got {source!r}",
                "write the source as producer-id/output or dora/timer/millis/<N>",
            )
        )
        return
    edge = {"edge": f"{source} -> {node_id}/{port}"}
    declared_inputs = manifest.get("inputs", {})
    is_dora_source = source.startswith("dora/")
    src_id = None if is_dora_source else source.partition("/")[0]

    # VAL-5 first: every backward path into a motion sink must traverse the
    # RESOLVED budget-guard (topological, per the spec's "every path"; a
    # same-named node with no manifest is spoofing; timers and unresolvable
    # sources are ungated). Never let a later check's early return hide this.
    if manifest.get("safety_class") == "motion" and port in MOTION_SINK_PORTS:
        gated = not is_dora_source and _gated_source(source, graph_nodes, manifests, {}, set())
        if not gated:
            errors.append(
                _entry(
                    "MOTION_UNGATED",
                    edge,
                    f"a path into {node_id}/{port} does not traverse {GUARD_ID} (VAL-5)",
                    f"route every command path through the {GUARD_ID} node (SPEC 080)",
                )
            )

    port_declared = port in declared_inputs
    if not port_declared:
        errors.append(
            _entry(
                "SCHEMA_MISMATCH",
                edge,
                f"{node_id} has no declared input port {port!r}",
                f"rename the input to one of {node_id}'s declared ports "
                f"{sorted(declared_inputs)}, or extend its manifest (Class B change)",
            )
        )

    if is_dora_source:
        timer_hz = _parse_timer_hz(source)
        if timer_hz is None:
            errors.append(
                _entry(
                    "INPUT_NO_PRODUCER",
                    edge,
                    f"{source!r} is not a valid dora builtin source",
                    "only dora/timer/millis/<N> (N > 0) is supported",
                )
            )
            return
        rate_declared = declared_inputs.get(port, {}).get("rate_hz")
        if rate_declared and abs(timer_hz - rate_declared) > RATE_BAND * rate_declared:
            warnings.append(
                _entry(
                    "RATE_INCOMPATIBLE",
                    edge,
                    f"timer drives {port} at {timer_hz:g} Hz; manifest declares "
                    f"{rate_declared} Hz (±20% band, TC-4)",
                    f"use dora/timer/millis/{round(1000 / rate_declared)}",
                )
            )
        return

    out_port = source.partition("/")[2]
    producer = graph_nodes.get(src_id)
    declared_outputs = (producer or {}).get("outputs") or []
    if producer is None or out_port not in declared_outputs:
        if producer is None:
            close = _closest(src_id, list(graph_nodes))
            hint = (
                f"change the edge source to {close!r} (closest graph node id)"
                if close
                else f"add a node producing {out_port!r} or point the edge at one of "
                f"the graph's nodes: {sorted(graph_nodes)}"
            )
        else:
            hint = (
                f"wire from one of {src_id}'s declared outputs {declared_outputs}, "
                f"or add {out_port!r} to that node's outputs list"
            )
        errors.append(
            _entry(
                "INPUT_NO_PRODUCER",
                edge,
                f"no producer for {source!r}",
                hint,
            )
        )
        return

    # VAL-6 before any schema-level return: an oracle leak must never be
    # hidden behind SCHEMA_UNKNOWN/SCHEMA_MISMATCH on the same edge.
    if out_port == "oracle_state" and not node_id.startswith("verifier-"):
        errors.append(
            _entry(
                "ORACLE_LEAK",
                edge,
                f"oracle_state consumed by non-verifier node {node_id!r} (VAL-6)",
                "only verifier-* nodes may read ground truth; use object_pose "
                "providers (oracle-pose, pose-estimator) for perception",
            )
        )

    producer_manifest = manifests.get(src_id)
    manifest_outputs = (producer_manifest or {}).get("outputs") or {}
    if producer_manifest is not None and out_port not in manifest_outputs:
        errors.append(
            _entry(
                "INPUT_NO_PRODUCER",
                edge,
                f"{src_id}'s manifest declares no output {out_port!r} — the graph "
                "outputs list cannot invent ports the typed contract lacks",
                f"{src_id} manifest outputs: {sorted(manifest_outputs)}",
            )
        )
        return
    if not port_declared:
        return

    consumer_schema = declared_inputs[port].get("schema")
    producer_schema = (
        manifest_outputs.get(out_port, {}).get("schema") if producer_manifest else None
    )
    # unknown names were already reported per node by the VAL-4 sweep;
    # a mismatch verdict against an unknown name would be noise
    if any(s is not None and s not in vocabulary for s in (producer_schema, consumer_schema)):
        return
    if producer_schema is not None and producer_schema != consumer_schema:
        errors.append(
            _entry(
                "SCHEMA_MISMATCH",
                edge,
                f"{src_id}/{out_port} produces {producer_schema}; "
                f"{node_id}/{port} expects {consumer_schema}",
                f"{src_id}/{out_port} carries {producer_schema}; feed {port} from "
                f"an output with schema {consumer_schema} instead",
            )
        )


def validate(graph_path: Path, root: Path, embodiment: str, allow_unproven: bool) -> dict:
    report = {"ok": False, "graph": str(graph_path), "errors": [], "warnings": []}
    nodes, errors = load_graph(graph_path)
    if nodes is None:
        report["errors"] = errors
        return report

    manifest_list, manifest_errors = load_manifests(root)
    if manifest_errors:
        report["errors"] = [
            _entry(
                "GRAPH_INVALID",
                {"node": "(registry)"},
                e["message"],
                "fix the registry before validating graphs (harness/registry.py lint)",
            )
            for e in manifest_errors
        ]
        return report
    try:
        capability_schema = load_capability_schema(root)
        vocabulary = set(load_vocabulary(root))
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        report["errors"] = [
            _entry(
                "GRAPH_INVALID",
                {"node": "(registry)"},
                f"cannot load registry schema files: {exc}",
                "restore registry/schema/ (capability.schema.json, schemas.toml)",
            )
        ]
        return report

    # full CAP-1 schema screen: malformed registry data becomes a structured
    # error before graph validation, never a TypeError mid-check (CON-8).
    # The vocabulary check is deliberately NOT part of this screen — unknown
    # schema NAMES are the validator's own SCHEMA_UNKNOWN concern (VAL-4).
    malformed = [
        _entry(
            "GRAPH_INVALID",
            {"node": "(registry)"},
            f"malformed manifest {path.name}: {message}",
            "fix the registry before validating graphs (harness/registry.py lint)",
        )
        for path, m in manifest_list
        for message in manifest_schema_errors(capability_schema, m)
    ]
    if malformed:
        report["errors"] = malformed
        return report
    manifests = {m["id"]: m for _, m in manifest_list}

    errors, warnings = validate_nodes(nodes, manifests, vocabulary, embodiment, allow_unproven)
    report["errors"] = errors
    report["warnings"] = warnings
    report["ok"] = not errors
    return report
