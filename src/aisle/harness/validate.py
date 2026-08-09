"""Dataflow validator (SPEC 060 VAL-1..7, CON-8).

Loads a dora dataflow YAML plus every registry manifest and rejects graphs
that cannot run safely: unresolved node ids, duplicate ids, missing
producers, schema mismatches against the CAP-2 vocabulary, oracle leaks
(VAL-6), ungated motion (VAL-5), motion nodes without evalcards, and
pip:-sourced nodes whose distribution is not installed (INSTALL_MISSING).
Hints are the research agent's learning signal: every error names a registry
capability or a concrete fix. No genesis or dora imports (unit territory).
"""

import difflib
import json
import re
import tomllib
from collections import Counter
from pathlib import Path

import yaml

from aisle.harness.registry import (
    MOTION_SINK_PORTS,
    _path_source_valid,
    _pip_dist,
    _pip_installed,
    load_capability_schema,
    load_manifests,
    load_vocabulary,
    manifest_schema_errors,
)

# MOTION_SINK_PORTS lives in registry.py (shared with the ADR-5 lint rule):
# base_cmd is a motion sink too (SPEC 210 MOB-3) — a mobile base command
# reaching the bridge MUST traverse the budget guard.
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
        env = node.get("env")
        if env is not None and not isinstance(env, dict):
            # PR #80 re-review: downstream checks read env with .get(); a
            # scalar/list here must be a structural refusal, not a crash
            structural.append(
                _entry(
                    "GRAPH_INVALID",
                    {"node": str(node_id)},
                    f"env must be a mapping of variable -> value, got {type(env).__name__}",
                    "write env as {VAR: value} pairs",
                )
            )
    if structural:
        return None, structural
    return nodes, []


def _manifest_launchable(
    manifest: dict,
    arm_kind: str,
    root: Path | None = None,
    embodiment: str | None = None,
    allow_unproven: bool = False,
) -> bool:
    """A candidate INSTALL_MISSING alternative must survive EVERY
    manifest-level check of the NEXT compile (VAL-2's never-fail-the-
    next-compile MUST; PR #63/#64 reviews): an installed distribution —
    or a path source that is a real file under the root (else
    SOURCE_INVALID) — AND arm-compatible AND base-compatible (a
    base-requiring peer on a fixed-base graph is EMBODIMENT_MISMATCH)
    AND, for motion manifests, evalcarded unless the caller allows
    unproven motion (else EVAL_MISSING_FOR_MOTION)."""
    dist = _pip_dist(manifest)
    if dist is not None and not _pip_installed(dist):
        return False
    source = manifest.get("source")
    if (
        dist is None
        and root is not None
        and isinstance(source, str)
        and ":" not in source
        and not _path_source_valid(source, root)
    ):
        return False
    arms = manifest.get("embodiment", {}).get("arm", [])
    if arms and arm_kind not in arms:
        return False
    base = manifest.get("embodiment", {}).get("base", [])
    if base and embodiment is not None and embodiment not in base:
        return False
    if (
        manifest.get("safety_class") == "motion"
        and manifest.get("eval") is None
        and not allow_unproven
    ):
        return False
    return True


def validate_nodes(
    nodes: list[dict],
    manifests: dict[str, dict],
    vocabulary: set[str],
    embodiment: str,
    allow_unproven: bool,
    graph_dir: Path | None = None,
    root: Path | None = None,
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
    # MOB-4: an embodiment resolves to an ARM kind; `mobile` runs the
    # franka arm on a base, so a franka-arm graph validates unchanged
    # under `mobile`. Arm nodes are checked against the resolved arm.
    arm_kind = EMBODIMENT_ARM.get(embodiment, embodiment)
    # TC-9/VAL-8: the perception rung is a property of the GRAPH, read once
    rung, rung_errors = graph_perception_rung(nodes)
    errors.extend(rung_errors)
    for node in nodes:
        node_id = node["id"]
        # ADR-25 (issue #71, PR #80 review): the bridge's reset-less
        # bring-up opt-out must never reach a validated graph — the rollout
        # runner scrubs it from os.environ, but node env in graph YAML
        # bypasses that scrub and would restore the pre-reset startup race
        # in a measured rollout. Fail closed on the truthy spellings the
        # bridge itself accepts.
        bringup = str((node.get("env") or {}).get("AISLE_STEP_WITHOUT_RESET", ""))
        if bringup.strip().lower() in ("1", "true", "yes"):
            errors.append(
                _entry(
                    "BRINGUP_ENV_FORBIDDEN",
                    {"node": node_id},
                    f"node {node_id!r} sets AISLE_STEP_WITHOUT_RESET={bringup!r} in graph env",
                    "remove it: pre-reset free-running is a bring-up mode for "
                    "reset-less debug graphs run directly via `dora run`, never "
                    "for validated rollouts (CON-5, ADR-25)",
                )
            )
        manifest = manifests.get(node_id)
        if manifest is None:
            close = _closest(node_id, list(manifests))
            suggestion = (
                f"rename the node to {close!r}"
                if close
                else "no similar manifest id exists; find one with: "
                "python -m aisle.harness.registry search --provides <capability> "
                "--installed (issue #39: only launchable nodes)"
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
                if not isinstance(source, str):
                    continue
                if source.endswith("/oracle_state"):
                    errors.append(
                        _entry(
                            "ORACLE_LEAK",
                            {"edge": f"{source} -> {node_id}/{port}"},
                            f"oracle_state consumed by {node_id!r}, which has no "
                            "manifest and so cannot be an authorized verifier (VAL-6)",
                            "only verifier-* manifests may read ground truth",
                        )
                    )
                # VAL-8 needs the same fallback for the same reason: the rung
                # binds the GRAPH, not the registry, so an unregistered node —
                # a hand-added one, or anything a harness injects — must not
                # be exempt from it. Measured before this was added: at L1 an
                # unregistered consumer of `poses` reported MANIFEST_MISSING
                # alone, where the byte-identical oracle_state graph reported
                # MANIFEST_MISSING + ORACLE_LEAK.
                if source.rpartition("/")[2] in FORBIDDEN_BY_RUNG.get(rung, ()):
                    errors.append(
                        _rung_entry(
                            {"edge": f"{source} -> {node_id}/{port}"},
                            source.rpartition("/")[2],
                            node_id,
                            rung,
                        )
                    )
            continue
        # VAL-2 SOURCE_INVALID (issue #35): unknown schemes and empty pip
        # dists are CAP-1 pattern violations (schema/lint + the registry
        # screen above), so the remaining dodge channel a schema cannot
        # check is a path-form source naming NO FILE under the root — the
        # manifest's launch claim is unverifiable: error out and skip the
        # source-derived checks for this node.
        source_val = manifest.get("source")
        source_invalid = False
        if isinstance(source_val, str) and root is not None:
            if ":" not in source_val and not _path_source_valid(source_val, root):
                source_invalid = True
                errors.append(
                    _entry(
                        "SOURCE_INVALID",
                        {"node": node_id},
                        f"manifest source {source_val!r} is not a regular "
                        "file contained by the root — the graph would "
                        "validate but never launch (or launch code outside "
                        "the vetted tree)",
                        "point the manifest source at the node's real file "
                        "(or register the node with harness skill register)",
                    )
                )
        # VAL-2 INSTALL_MISSING (H1-discovered): a pip:-sourced capability
        # that is not installed validates into a graph that cannot launch —
        # the agent's only signal is this error, so the hint must name an
        # installed same-capability alternative when one exists
        dist = None if source_invalid else _pip_dist(manifest)
        if dist is not None and not _pip_installed(dist):
            provided = set(manifest.get("provides") or [])
            # a usable alternative must FULLY cover the missing node's
            # capabilities — a partial provider is not a replacement
            alternatives = sorted(
                other_id
                for other_id, other in manifests.items()
                if other_id != node_id
                and provided <= set(other.get("provides") or [])
                and _manifest_launchable(other, arm_kind, root, embodiment, allow_unproven)
            )
            if alternatives:
                hint = f"use an installed provider of {sorted(provided)}: " + ", ".join(
                    alternatives
                )
            elif dist:
                hint = (
                    f"install {dist!r} (env-change PR) or author a node "
                    f"providing {sorted(provided)}"
                )
            else:
                # `install ''` is a nonsense instruction (red-team, PR #34)
                hint = (
                    "fix the manifest source: `pip:` carries no distribution "
                    f"name; name one or author a node providing {sorted(provided)}"
                )
            detail = (
                f"manifest source {manifest.get('source')!r}: distribution "
                + (f"{dist!r} is not installed in this environment" if dist else "name is empty")
                + " — the graph would validate but never launch"
            )
            errors.append(_entry("INSTALL_MISSING", {"node": node_id}, detail, hint))
        # VAL-2 PATH_MANIFEST_MISMATCH (issue #36; H3 campaign 2 live case):
        # dora launches the graph node's `path`, not the manifest's
        # `source` — an approved id with a divergent path executes unvetted
        # code under a vetted identity, and every topology check passes
        node_path = node.get("path")
        source = manifest.get("source")
        if (
            isinstance(node_path, str)
            and isinstance(source, str)
            and not source_invalid
            and graph_dir is not None
            and root is not None
        ):
            pip_name = _pip_dist(manifest)
            if pip_name is not None:
                # graphs reference pip nodes as the manifest source
                # verbatim or the bare NORMALIZED distribution name (PR
                # #62 review P2: reuse _pip_dist so decorated/case-varied
                # sources match the INSTALL_MISSING contract) — anything
                # else launches other code
                canonical_path = re.sub(r"[-_.]+", "-", node_path.strip()).lower()
                matches = node_path == source or canonical_path == pip_name
            else:
                # exactly ONE base: the graph's own directory — the base
                # dora resolves against. A graphs-dir fallback approved
                # tmpdir-staged graphs whose paths resolve elsewhere at
                # runtime (PR #62 review P1: the live-swap bypass);
                # staged copies must carry absolute paths instead.
                matches = (graph_dir / node_path).resolve() == (root / source).resolve()
            if not matches:
                errors.append(
                    _entry(
                        "PATH_MANIFEST_MISMATCH",
                        {"node": node_id},
                        f"path {node_path!r} does not resolve to the manifest "
                        f"source {source!r} — dora would launch code the "
                        f"registry never vetted under the id {node_id!r}",
                        f"point path at the manifest source ({source!r}), or "
                        "register a manifest for what path actually launches "
                        "(harness skill register)",
                    )
                )
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

        # SPEC 210 MOB-3: on a mobile graph the guard (it outputs
        # base_cmd_safe) MUST also wire base_pose + base_watchdog, or the
        # keep-out and stale-command watchdog are silently disabled — the
        # validator otherwise does not require every manifest input.
        if embodiment == "mobile" and "base_cmd_safe" in (manifest.get("outputs") or {}):
            missing = {"base_pose", "base_watchdog"} - set(node.get("inputs") or {})
            if missing:
                errors.append(
                    _entry(
                        "MOBILE_GUARD_INCOMPLETE",
                        {"node": node_id},
                        f"{node_id} guards the base on a mobile graph but does not "
                        f"wire {sorted(missing)}",
                        "wire base_pose and base_watchdog into the guard so MOB-3 "
                        "keep-out and the stale-command watchdog stay active",
                    )
                )

        for port, source in (node.get("inputs") or {}).items():
            _validate_edge(
                node,
                manifest,
                port,
                source,
                graph_nodes,
                manifests,
                vocabulary,
                errors,
                warnings,
                rung,
            )
    return errors, warnings


def graph_perception_rung(nodes: list) -> tuple[str, list[dict]]:
    """The graph's declared perception rung (TC-9) and any errors reading it.

    The rung rides the GRAPH so the graph hash attests which pose source a
    result used — the same reasoning as ADR-25's bring-up scrub and ADR-11
    clause 14's capture declaration. A graph that declares nothing is L0,
    which is what every pre-TC-9 graph is: the check below then permits
    `poses` exactly as before.

    Two ways of reading this wrong both END IN A SILENT PASS, because an
    unrecognized rung makes FORBIDDEN_BY_RUNG.get(...) empty and forbids
    nothing, so both are errors rather than best-effort guesses:

    * a value this table does not know (`L3`, a stray space in `"L1 "`).
      Measured before this was fixed: `AISLE_PERCEPTION: "L1 "` on a graph
      wiring ground-truth `poses` validated ok=true, exit 0. The bridge
      normalizes with .strip().upper() and REFUSES an unknown rung, so the
      un-stripped read also disagreed with the runtime about the same text.
    * two nodes declaring DIFFERENT rungs. Node order in a dataflow YAML is
      arbitrary, so first-wins let an L0 declaration sitting above the
      bridge's L1 downgrade the whole graph. An ambiguous attestation is not
      a thing to resolve by position; it is a thing to reject.
    """
    errors: list[dict] = []
    declared = {}
    for node in nodes:
        env = node.get("env") or {}
        raw = env.get("AISLE_PERCEPTION")
        if raw is not None and str(raw).strip():
            declared[node["id"]] = str(raw).strip().upper()
    unknown = {n: r for n, r in declared.items() if r not in FORBIDDEN_BY_RUNG}
    for node_id, rung in sorted(unknown.items()):
        errors.append(
            _entry(
                "PERCEPTION_RUNG_VIOLATION",
                {"node": node_id},
                f"unknown perception rung {rung!r} declared by {node_id!r} (VAL-8)",
                f"set AISLE_PERCEPTION to one of {sorted(FORBIDDEN_BY_RUNG)} — an "
                "unrecognized rung would forbid nothing and silently pass the check",
            )
        )
    distinct = sorted(set(declared.values()))
    if len(distinct) > 1:
        errors.append(
            _entry(
                "PERCEPTION_RUNG_VIOLATION",
                {"node": sorted(declared)[0]},
                f"conflicting perception rungs {distinct} declared by {sorted(declared)} (VAL-8)",
                "declare AISLE_PERCEPTION once, on the sim-bridge node — node order "
                "in the YAML is arbitrary, so a graph with two rungs attests neither",
            )
        )
    # on any bad declaration, enforce the STRICTEST rung the table has rather
    # than the most permissive: the graph is already rejected, and a forbidden
    # edge in it should still be named in the same report instead of surfacing
    # only after the declaration is fixed
    if errors:
        return max(FORBIDDEN_BY_RUNG, key=lambda r: len(FORBIDDEN_BY_RUNG[r])), errors
    return (distinct[0] if distinct else "L0"), errors


FORBIDDEN_BY_RUNG = {
    "L0": (),
    # TC-9: at L1 pose must be ESTIMATED from segmentation + depth, so the
    # ground-truth shortcut is closed. At L2 segmentation goes too.
    "L1": ("poses",),
    "L2": ("poses", "seg_overhead"),
}

# VAL-2/VAL-3: a hint MUST NOT name an alternative that fails the NEXT
# compile. The L1 remedy is segmentation + depth, but seg_overhead is itself
# forbidden at L2, so a single shared hint sent an L2 author to a topic their
# own rung rejects — measured: following it produced a second
# PERCEPTION_RUNG_VIOLATION. The remedy is per-rung for that reason.
RUNG_REMEDY = {
    "L1": "estimate pose from seg_overhead + depth_overhead (use the segmented-pose provider)",
    "L2": "estimate pose from rgb_overhead/rgb_wrist alone — L2 forbids segmentation too",
}


def _rung_entry(edge: dict, out_port: str, node_id: str, rung: str) -> dict:
    """One PERCEPTION_RUNG_VIOLATION, phrased the same wherever it is raised.

    The hint deliberately does NOT offer "or declare L0 to use ground truth".
    SPEC 060's opening line is that these messages are the research agent's
    learning signal, and the rung is self-declared in the graph the agent
    authors — so naming the downgrade would hand it a one-token way to make
    the error disappear without changing what the run actually measures.
    Lowering a rung is a claim about a result, not a lint fix."""
    return _entry(
        "PERCEPTION_RUNG_VIOLATION",
        edge,
        f"{out_port!r} consumed by {node_id!r} under perception rung {rung} (VAL-8)",
        f"at {rung}, {RUNG_REMEDY.get(rung, 'do not consume ground truth')}",
    )


def _parse_timer_hz(source: str) -> float | None:
    """Rate of a well-formed dora/timer/millis/<N> source, else None."""
    parts = source.split("/")
    if len(parts) == 4 and parts[1] == "timer" and parts[2] == "millis":
        if parts[3].isdigit() and int(parts[3]) > 0:
            return 1000.0 / int(parts[3])
    return None


def _validate_edge(
    node, manifest, port, source, graph_nodes, manifests, vocabulary, errors, warnings, rung
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
    # The guard itself is motion under the ratified ADR-5 boundary (it emits
    # the *_safe actuation streams) but is exempt as a sink: it IS the gate
    # every path must traverse — raw commands are exactly what it consumes.
    if (
        manifest.get("safety_class") == "motion"
        and port in MOTION_SINK_PORTS
        and node_id != GUARD_ID
    ):
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

    # VAL-8 before any schema-level return, for the same reason as VAL-6: a
    # rung violation must not hide behind SCHEMA_* on the same edge. Without
    # this the ladder is advisory — a graph could keep consuming ground-truth
    # pose while its results were reported as L1, which would silently
    # invalidate every L1 number published from it (TC-9).
    if out_port in FORBIDDEN_BY_RUNG.get(rung, ()):
        errors.append(_rung_entry(edge, out_port, node_id, rung))

    # VAL-6 before any schema-level return: an oracle leak must never be
    # hidden behind SCHEMA_UNKNOWN/SCHEMA_MISMATCH on the same edge.
    if out_port == "oracle_state" and not node_id.startswith("verifier-"):
        errors.append(
            _entry(
                "ORACLE_LEAK",
                edge,
                f"oracle_state consumed by non-verifier node {node_id!r} (VAL-6)",
                # PR #34 red-team: naming pose-estimator here sent agents to
                # an uninstalled pip node — the next compile's INSTALL_MISSING
                "only verifier-* nodes may read ground truth; use an installed "
                "object_pose provider (e.g. oracle-pose) for perception",
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
    report = {"ok": False, "graph": str(graph_path), "errors": [], "warnings": [], "dist_state": {}}
    # ADR-24 D5 (PR #69 review F4): the diagnostic is computed from the
    # REGISTRY alone, before any graph parsing — early graph errors still
    # carry the three registry mappings
    try:
        import importlib.metadata as _md

        for _, m_early in load_manifests(root)[0]:
            dist_early = _pip_dist(m_early) if isinstance(m_early, dict) else None
            if dist_early:
                try:
                    report["dist_state"][dist_early] = _md.version(dist_early)
                except _md.PackageNotFoundError:
                    report["dist_state"][dist_early] = None
        report["dist_state"] = dict(sorted(report["dist_state"].items()))
    except Exception:  # noqa: BLE001 — a broken registry surfaces via its own errors
        pass
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

    errors, warnings = validate_nodes(
        nodes,
        manifests,
        vocabulary,
        embodiment,
        allow_unproven,
        graph_dir=graph_path.parent,
        root=root,
    )
    report["errors"] = errors
    report["warnings"] = warnings
    report["ok"] = not errors
    return report
