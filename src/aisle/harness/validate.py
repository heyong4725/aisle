"""Dataflow validator (SPEC 060 VAL-1..6, CON-8).

Loads a dora dataflow YAML plus every registry manifest and rejects graphs
that cannot run safely: unresolved node ids, duplicate ids, missing
producers, schema mismatches against the CAP-2 vocabulary, oracle leaks
(VAL-6), ungated motion (VAL-5), and motion nodes without evalcards.
Hints are the research agent's learning signal: every error names a registry
capability or a concrete fix. No genesis or dora imports (unit territory).
"""

import difflib
import tomllib
from collections import Counter
from pathlib import Path

import yaml

from aisle.harness.registry import load_manifests, load_vocabulary

MOTION_SINK_PORTS = {"joint_cmd", "gripper_cmd"}
GUARD_ID = "budget-guard"
RATE_BAND = 0.2  # TC-4: rates are contracts within ±20%


def _entry(code: str, where: dict, detail: str, hint: str) -> dict:
    return {"code": code, **where, "detail": detail, "hint": hint}


def _closest(name: str, candidates: list[str]) -> str:
    matches = difflib.get_close_matches(name, candidates, n=1)
    return matches[0] if matches else ""


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
            suggestion = f"did you mean {close!r}?" if close else "check registry/manifests/"
            errors.append(
                _entry(
                    "MANIFEST_MISSING",
                    {"node": node_id},
                    f"no manifest for node id {node_id!r}",
                    f"{suggestion} (harness/registry.py search lists capabilities)",
                )
            )
            continue
        arms = manifest.get("embodiment", {}).get("arm", [])
        if embodiment not in arms:
            errors.append(
                _entry(
                    "EMBODIMENT_MISMATCH",
                    {"node": node_id},
                    f"{node_id} supports arms {arms}, graph targets {embodiment!r}",
                    f"swap in a capability supporting {embodiment!r} or change --embodiment",
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

    # VAL-5 first: a motion sink gated by anything but budget-guard —
    # including a timer or an unresolvable source — is ungated. Never let a
    # later check's early return hide this.
    if (
        manifest.get("safety_class") == "motion"
        and port in MOTION_SINK_PORTS
        and src_id != GUARD_ID
    ):
        errors.append(
            _entry(
                "MOTION_UNGATED",
                edge,
                f"{port} reaches driver {node_id} without traversing {GUARD_ID} (VAL-5)",
                f"route this command through the {GUARD_ID} node (SPEC 080)",
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
        close = _closest(src_id, list(graph_nodes))
        hint = (
            f"node {src_id!r} is not in the graph; did you mean {close!r}?"
            if producer is None
            else f"{src_id} declares outputs {declared_outputs}"
        )
        errors.append(
            _entry(
                "INPUT_NO_PRODUCER",
                edge,
                f"no producer for {source!r}",
                f"{hint} (keep edge ids consistent)",
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

    if port not in declared_inputs:
        errors.append(
            _entry(
                "SCHEMA_MISMATCH",
                edge,
                f"{node_id} has no declared input port {port!r}",
                f"declared inputs: {sorted(declared_inputs)}",
            )
        )
        return

    consumer_schema = declared_inputs[port].get("schema")
    producer_manifest = manifests.get(src_id)
    producer_schema = None
    if producer_manifest is not None:
        producer_schema = producer_manifest.get("outputs", {}).get(out_port, {}).get("schema")
    unknown = False
    # ordered tuple, not a set: report every unknown name deterministically (CON-5)
    for schema in dict.fromkeys((producer_schema, consumer_schema)):
        if schema is not None and schema not in vocabulary:
            unknown = True
            errors.append(
                _entry(
                    "SCHEMA_UNKNOWN",
                    edge,
                    f"schema {schema!r} is not in registry/schema/schemas.toml",
                    "add it via a Class C schema-vocabulary change (CAP-2) or fix the name",
                )
            )
    if unknown:
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
    manifests = {m["id"]: m for _, m in manifest_list if isinstance(m.get("id"), str)}
    try:
        vocabulary = set(load_vocabulary(root))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        report["errors"] = [
            _entry(
                "GRAPH_INVALID",
                {"node": "(registry)"},
                f"cannot load vocabulary: {exc}",
                "restore registry/schema/schemas.toml",
            )
        ]
        return report

    errors, warnings = validate_nodes(nodes, manifests, vocabulary, embodiment, allow_unproven)
    report["errors"] = errors
    report["warnings"] = warnings
    report["ok"] = not errors
    return report
