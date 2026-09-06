"""`harness monolith` — the monolithic arm's launcher and parity gate
(SPEC 440: MON-1 table rendering, MON-3 launcher, MON-10 parity rule,
MON-11 campaign purpose).

The launcher stamps a copy of the frozen monolithic graph with the module
under test and hands it to the ordinary rollout runner: the agent in this
arm sees `harness monolith run --module`, never the YAML, the registry or
`harness validate` (MON-3). `check` performs only what Python itself does
— compile and execute the module against the primitive broker with no
simulator — and reports its syntax/import/runtime error unchanged.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import yaml

TEMPLATE_GRAPH = "graphs/monolithic_t1.yaml"
DOCS_DIR = "docs/monolithic"
CAMPAIGN_PURPOSE = "expert_parity"  # MON-11: never pooled with agent sessions


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(root: Path, name: str) -> dict:
    return json.loads((root / DOCS_DIR / name).read_text(encoding="utf-8"))


# -- MON-3 launcher ------------------------------------------------------


def stamp_graph(root: Path, module: Path, out_dir: Path, template: str = TEMPLATE_GRAPH) -> Path:
    """The frozen monolithic graph with `AISLE_MONOLITH_MODULE` pointed at
    the module under test, node and turn-plan paths absolutized, written
    under `out_dir` (graphs/out/ by default, git-ignored)."""
    template_path = root / template
    doc = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    module = module.resolve()
    for node in doc["nodes"]:
        node["path"] = str((template_path.parent / node["path"]).resolve())
        env = node.get("env") or {}
        if "AISLE_MONOLITH_MODULE" in env:
            env["AISLE_MONOLITH_MODULE"] = str(module)
        if "AISLE_TURN_PLAN" in env:
            env["AISLE_TURN_PLAN"] = str((template_path.parent / env["AISLE_TURN_PLAN"]).resolve())
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"monolithic-{_sha256(module)[:12]}.yaml"
    out.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return out


def check_module(module: Path, embodiment: str = "franka") -> dict:
    """Compile and construct the module's Controller against the broker with
    no simulator: exactly the language/import/runtime failures MON-3
    permits, and nothing else (no topology or type checking)."""
    from aisle.monolith.confinement import ConfinementViolation
    from aisle.nodes.monolith_broker import Broker

    try:
        broker = Broker(module.resolve(), embodiment, log=lambda _msg: None)
    except ConfinementViolation as exc:
        return {
            "ok": False,
            "module": str(module),
            "infrastructure_invalid": True,
            "error": str(exc),
        }
    except SyntaxError as exc:
        return {"ok": False, "module": str(module), "error": f"SyntaxError: {exc}"}
    except Exception as exc:  # the module's own failure, reported as-is
        return {"ok": False, "module": str(module), "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, **broker.record}


def run(
    root: Path,
    module: Path,
    seeds: list[int],
    episodes: int,
    tier: str = "T1",
    embodiment: str = "franka",
    run_id: str | None = None,
    timeout_s: float | None = None,
    no_idea_gate: bool = False,
) -> dict:
    """Stamp the supported T1 graph and roll it out through the trusted runner."""
    if tier != "T1":
        return {
            "ok": False,
            "error": "unsupported_monolithic_tier",
            "tier": tier,
            "supported_tiers": ["T1"],
        }

    from aisle.harness.cli import _branch
    from aisle.harness.rollout import rollout

    pre = check_module(module, embodiment)
    if not pre["ok"]:
        return pre
    graph = stamp_graph(root, module, root / "graphs" / "out")
    import datetime
    import uuid

    run_id = run_id or (
        "monolith-"
        + datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
        + uuid.uuid4().hex[:6]
    )
    report = rollout(
        root=root,
        graph=graph,
        tier=tier,
        episodes=episodes,
        seeds=seeds,
        reset_mode="teleport",
        verifier="oracle",
        run_id=run_id,
        branch=_branch(root),
        no_idea_gate=no_idea_gate,
        timeout_s=timeout_s,
        embodiment=embodiment,
    )
    return {**report, "module": pre, "campaign_purpose": CAMPAIGN_PURPOSE}


# -- MON-1 treatment table -----------------------------------------------

CLASSES = ("identical", "representation-equivalent", "intentionally-different")
ANALYSIS_DIR = "analysis/monolithic-control"


def _set_digest(root: Path, paths: list[str]) -> str:
    """One SHA-256 over the listed artifacts' SHA-256s in order: a change
    to any of them moves the row's hash."""
    joined = "\n".join(f"{rel}:{_sha256(root / rel)}" for rel in paths)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def render_table(root: Path, table: dict) -> str:
    """Human-readable rendering with each artifact's current hash."""
    lines = [
        f"# Treatment-difference table {table['id']} (generated; SPEC 440 MON-1)",
        "",
        "Regenerate with `uv run harness monolith table --write`; CI checks it.",
        "",
        "| id | surface | class | typed arm | monolithic arm | justification "
        "| analysis treatment |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in table["rows"]:
        typed = _render_side(root, row["typed"])
        mono = _render_side(root, row["monolithic"])
        lines.append(
            f"| {row['id']} | {row['surface']} | {row['class']} | {typed} | {mono} | "
            f"{row['justification']} | {row['analysis']} |"
        )
    return "\n".join(lines) + "\n"


def _render_side(root: Path, side: dict) -> str:
    parts = [side["description"]]
    for rel in side.get("paths", []):
        path = root / rel
        digest = _sha256(path)[:12] if path.is_file() else "MISSING"
        parts.append(f"`{rel}` {digest}")
    return "<br>".join(parts)


def table_errors(root: Path, table: dict) -> list[str]:
    """MON-1: every row classified, both sides described, every path
    present; an unresolved row blocks parity."""
    errors: list[str] = []
    for row in table.get("rows", []):
        if row.get("class") not in CLASSES:
            errors.append(f"{row.get('surface')}: class must be one of {CLASSES}")
        for arm in ("typed", "monolithic"):
            side = row.get(arm) or {}
            if not side.get("description"):
                errors.append(f"{row.get('surface')}: {arm} side undescribed")
            if not side.get("paths"):
                errors.append(f"{row.get('surface')}: {arm} names no artifact")
            for rel in side.get("paths", []):
                if not (root / rel).is_file():
                    errors.append(f"{row.get('surface')}: {arm} path missing: {rel}")
        if not row.get("justification"):
            errors.append(f"{row.get('surface')}: no justification")
        if not row.get("analysis"):
            errors.append(f"{row.get('surface')}: no analysis treatment")
    return errors


def treatment_record(root: Path, table: dict) -> dict:
    """The machine-readable MON-1 record in the aisle.monolithic-treatment.v1
    schema `aisle.harness.monolithic.validate_treatment_table` checks: one
    `path#sha256:` per arm per row (the first listed artifact and the set
    digest over every listed artifact)."""
    rows = []
    for row in table["rows"]:
        entry = {"id": row["id"], "surface": row["surface"], "classification": row["class"]}
        for arm in ("typed", "monolithic"):
            paths = row[arm]["paths"]
            entry[arm] = f"{paths[0]}#sha256:{_set_digest(root, paths)}"
        entry["justification"] = row["justification"]
        entry["analysis"] = row["analysis"]
        rows.append(entry)
    return {"schema_version": "aisle.monolithic-treatment.v1", "rows": rows}


def experts_record(root: Path, experts: dict) -> dict:
    """MON-9 provenance with the set digest of each arm's components."""
    return {
        "id": experts["id"],
        "frozen": experts["frozen"],
        "artifacts": [
            {
                "arm": a["arm"],
                "author": a["author"],
                "path": a["path"],
                "sha256": _set_digest(root, a["components"]),
                "blind": a["blind"],
            }
            for a in experts["artifacts"]
        ],
    }


def _dump(obj: dict) -> str:
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def table_report(root: Path, write: bool) -> dict:
    """Render/check the MON-1 table and the generated records: the Markdown
    rendering, the v1 treatment record and the MON-9 experts record. The
    records carry current hashes, so an edit to any listed surface needs
    `--write`; `--check` (the default) fails on drift."""
    from aisle.harness.monolithic import (
        TreatmentTableError,
        TypedSurfaceError,
        validate_expert_artifacts,
        validate_treatment_table,
    )

    table = load_json(root, "treatment-table.json")
    errors = table_errors(root, table)
    if errors:
        return {"ok": False, "table": table["id"], "rows": len(table["rows"]), "errors": errors}
    record = treatment_record(root, table)
    try:
        identity = validate_treatment_table(record)
    except TreatmentTableError as exc:
        return {"ok": False, "table": table["id"], "rows": len(table["rows"]), "errors": [str(exc)]}
    record["immutable_id"] = identity["immutable_id"]
    record["status"] = "shakeout"
    record["source"] = f"{DOCS_DIR}/treatment-table.json"
    experts = experts_record(root, load_json(root, "experts.json"))
    try:
        validate_expert_artifacts(experts["artifacts"])
    except TypedSurfaceError as exc:
        errors.append(str(exc))
    generated = {
        root / DOCS_DIR / "treatment-table.md": render_table(root, table),
        root / ANALYSIS_DIR / "treatment-table-v1.json": _dump(record),
        root / ANALYSIS_DIR / "experts-v1.json": _dump(experts),
    }
    for path, content in generated.items():
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        elif current != content:
            errors.append(
                f"{path.relative_to(root)} is stale; run `harness monolith table --write`"
            )
    return {
        "ok": not errors,
        "table": table["id"],
        "rows": len(table["rows"]),
        "immutable_id": identity["immutable_id"],
        "errors": errors,
    }


# -- MON-4 interface map --------------------------------------------------


def _agent_region(graph: dict, agent_nodes: set[str]) -> tuple[set[str], set[str]]:
    """Semantic edges crossing the agent-editable region of a typed graph:
    (inputs from trusted nodes, outputs consumed by trusted nodes)."""
    inputs: set[str] = set()
    outputs: set[str] = set()
    for node in graph["nodes"]:
        for port, spec in (node.get("inputs") or {}).items():
            src = spec["source"] if isinstance(spec, dict) else spec
            src_node = src.split("/")[0]
            if node["id"] in agent_nodes and src_node not in agent_nodes:
                inputs.add("tick" if src.startswith("dora/timer") else port)
            elif node["id"] not in agent_nodes and src_node in agent_nodes:
                outputs.add(src.split("/")[1])
    if "turn" in inputs:
        # the lockstep clock is transport (MON-1 row); the 1 Hz tick both
        # arms derive from it is the semantic field
        inputs.discard("turn")
        inputs.add("tick")
    outputs.discard("turn_done")
    return inputs, outputs


def interface_errors(root: Path, imap: dict) -> list[str]:
    """MON-4 exactness: the map's semantic fields equal the typed region's
    crossing edges AND the broker's observation/action vocabulary as wired;
    a field with different authority on the two sides is an error."""
    from aisle.harness.monolithic import (
        TypedSurfaceError,
        validate_broker_route,
        validate_interface_map,
    )
    from aisle.nodes.monolith_broker import ACTIONS, OBSERVATIONS

    errors: list[str] = []
    try:
        validate_interface_map(imap["fields"])
    except TypedSurfaceError as exc:
        errors.append(str(exc))
    typed_graph = yaml.safe_load((root / imap["typed"]["graph"]).read_text())
    mono_graph = yaml.safe_load((root / imap["monolithic"]["graph"]).read_text())
    typed_in, typed_out = _agent_region(typed_graph, set(imap["typed"]["agent_nodes"]))
    mono_in, mono_out = _agent_region(mono_graph, {"monolith-broker"})
    obs = {f["name"] for f in imap["fields"] if f["typed"].get("role") == "observe"}
    acts = {f["name"] for f in imap["fields"] if f["typed"].get("role") in ("act", "feedback")}
    if obs != typed_in:
        errors.append(f"observation fields != typed region inputs: {sorted(obs ^ typed_in)}")
    if obs != mono_in:
        errors.append(f"observation fields != broker inputs: {sorted(obs ^ mono_in)}")
    if acts != typed_out:
        errors.append(f"action fields != typed region outputs: {sorted(acts ^ typed_out)}")
    if acts != mono_out:
        errors.append(f"action fields != broker outputs: {sorted(acts ^ mono_out)}")
    if not obs <= set(OBSERVATIONS):
        errors.append(f"broker cannot deliver: {sorted(obs - set(OBSERVATIONS))}")
    mono_action_names = {"episode_feedback": "feedback"}
    for f in imap["fields"]:
        if f["typed"].get("role") != "observe":
            name = mono_action_names.get(f["name"], f["name"])
            if name not in ACTIONS:
                errors.append(f"broker has no action for {f['name']}")
        if f["typed"].get("privileged") != f["monolithic"].get("privileged"):
            errors.append(f"{f['name']}: differently privileged across arms")
    try:
        validate_broker_route(_motion_route(mono_graph))
    except TypedSurfaceError as exc:
        errors.append(str(exc))
    return errors


def _motion_route(graph: dict) -> list[str]:
    """The route a monolithic joint command takes, read from the wiring:
    trusted controller (launcher/runner) -> broker -> guard -> bridge."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    route = ["trusted_controller", "primitive_broker"]
    guard = nodes.get("budget-guard", {}).get("inputs", {}).get("joint_cmd", {})
    if str(guard.get("source", "")).startswith("monolith-broker/"):
        route.append("budget_guard")
    bridge = nodes.get("dora-genesis", {}).get("inputs", {}).get("joint_cmd", {})
    if str(bridge.get("source", "")).startswith("budget-guard/"):
        route.append("sim_bridge")
    return route


def interface_report(root: Path) -> dict:
    imap = load_json(root, "interface-map.json")
    errors = interface_errors(root, imap)
    return {
        "ok": not errors,
        "interface_map": imap["id"],
        "fields": len(imap["fields"]),
        "errors": errors,
    }


# -- MON-10 parity gate ---------------------------------------------------


def _episodes(path: Path) -> dict[int, dict]:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return {int(r["seed"]): r for r in rows}


def parity_decision(protocol: dict, typed: dict[int, dict], mono: dict[int, dict]) -> dict:
    """Apply the frozen rule to paired per-seed outcomes. `gate` is "pass"
    only when every clause holds AND the protocol's preconditions (blind
    authorship MON-9, exact interface map MON-4, complete table MON-1) are
    attested; anything else is "blocked" with every reason listed."""
    from aisle.harness.monolithic import validate_parity_protocol

    validate_parity_protocol(protocol)
    acceptance = protocol["acceptance"]
    seeds = list(protocol["paired_seeds"])
    reasons: list[str] = []
    pairs = []
    for seed in seeds:
        t, m = typed.get(seed), mono.get(seed)
        if t is None or m is None:
            reasons.append(f"seed {seed}: missing paired run")
            continue
        excluded = [
            arm
            for arm, row in (("typed", t), ("monolithic", m))
            if row.get("infrastructure_invalid")
        ]
        pairs.append(
            {
                "seed": seed,
                "typed": t["status"] == "success",
                "monolithic": m["status"] == "success",
                "typed_failure": t.get("failure"),
                "monolithic_failure": m.get("failure"),
                "excluded": excluded,
            }
        )
    valid = [p for p in pairs if not p["excluded"]]
    for p in pairs:
        if p["excluded"]:
            reasons.append(
                f"seed {p['seed']}: infrastructure-invalid in {p['excluded']} (unresolved)"
            )
    unsafe = acceptance["safety"]["forbidden_failures"]
    for p in valid:
        for arm in ("typed", "monolithic"):
            if p[f"{arm}_failure"] in unsafe:
                reasons.append(f"seed {p['seed']}: {arm} safety failure {p[f'{arm}_failure']}")
    n = len(valid)
    rate_t = sum(p["typed"] for p in valid) / n if n else 0.0
    rate_m = sum(p["monolithic"] for p in valid) / n if n else 0.0
    floor = acceptance["functional"]["min_success_rate"]
    if n < acceptance["functional"]["min_valid_pairs"]:
        reasons.append(
            f"only {n} valid pairs; protocol needs {acceptance['functional']['min_valid_pairs']}"
        )
    if rate_t < floor:
        reasons.append(f"typed success {rate_t:.3f} below floor {floor}")
    if rate_m < floor:
        reasons.append(f"monolithic success {rate_m:.3f} below floor {floor}")
    margin = acceptance["equivalence"]["max_abs_success_difference"]
    diff = abs(rate_t - rate_m)
    if diff > margin + 1e-12:
        reasons.append(f"|success difference| {diff:.3f} exceeds margin {margin}")
    for pre, ok in acceptance["preconditions"].items():
        if not ok:
            reasons.append(f"precondition not attested: {pre}")
    canonical = json.dumps(protocol, sort_keys=True, separators=(",", ":")).encode()
    return {
        "campaign_purpose": CAMPAIGN_PURPOSE,
        "pooled": False,
        "protocol": acceptance["protocol_id"],
        "protocol_sha256": hashlib.sha256(canonical).hexdigest(),
        "pairs": pairs,
        "valid_pairs": n,
        "success_rate": {"typed": rate_t, "monolithic": rate_m, "difference": diff},
        "gate": "pass" if not reasons else "blocked",
        "reasons": reasons,
    }


def parity_report(root: Path, typed_path: Path, mono_path: Path) -> dict:
    from aisle.harness.monolithic import validate_campaign_purpose

    protocol = load_json(root, "parity-protocol.json")
    decision = parity_decision(protocol, _episodes(typed_path), _episodes(mono_path))
    validate_campaign_purpose(decision)
    return {
        "ok": True,
        **decision,
        "inputs": {"typed": str(typed_path), "monolithic": str(mono_path)},
    }


def git_head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=root
    ).stdout.strip()
