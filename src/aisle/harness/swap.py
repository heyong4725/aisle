"""Hot-swap and live-probe operations (SPEC 070 HAR-10..12; design doc
§9.1 decision 1). The H4 mechanism: iterate on a RUNNING dataflow instead
of relaunching, with the validator still the gatekeeper for every
mutation. CON-8: callers emit JSON; helpers here return dicts.

Hardened per the PR #50 adversarial review: trust-anchor nodes (the
budget guard and anything executing from the frozen set) can never be
swapped; staging happens in an unpredictable tmpdir with a byte-hash
re-check before the runtime mutation (TOCTOU); a failed add restores the
original node; a successful swap writes the graph file back so the NEXT
swap validates against live reality; EVERY attempt — success, failure,
or refusal — logs a HAR-12 event.

The dora interaction is a thin injectable seam (`runner`) so unit tests
never need a live dataflow; the default drives the `dora` CLI
(node add / remove — present since 1.0.0-rc.4).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import yaml

from aisle.harness.ideas import open_ideas
from aisle.harness.validate import validate

# the trust anchors a live mutation may never touch: the guard is VAL-5's
# premise (topology checks assume its CODE is the frozen one), and frozen
# env nodes are CON-7's
GUARD_ID = "budget-guard"
FROZEN_ROOTS = (
    "src/aisle/scenes",
    "src/aisle/verifier",
    "src/aisle/reset",
    "env",
    "src/aisle/nodes/budget_guard.py",
    # issue #127: the sim bridge is the EN module's live half — replacing it
    # wholesale can drop the TC-9 rung declaration (env rides the node) and
    # turn an L1 run into an L0 one while each mutation validates
    "src/aisle/nodes/dora_genesis.py",
)


def _default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["dora", *cmd], capture_output=True, text=True, timeout=120)


def _under_frozen(root: Path, candidate: Path) -> bool:
    resolved = candidate.resolve()
    for frozen in FROZEN_ROOTS:
        anchor = (root / frozen).resolve()
        if resolved == anchor or anchor in resolved.parents:
            return True
    return False


def _frozen_anchor(root: Path, graph_path: Path, node_id: str, node: dict) -> bool:
    """Trust-anchor test keyed on what dora actually receives (the node
    ID): the id's MANIFEST source is the spoof-proof authority (a crafted
    --graph with a benign path cannot dodge it), with the graph entry's
    resolved path as a second belt. Resolution closes the //-, ..- and
    relative-path dodges of a substring check (PR #50 re-review)."""
    if node_id == GUARD_ID:
        return True
    manifest = root / "registry" / "manifests" / f"{node_id}.yaml"
    if manifest.exists():
        try:
            source = (yaml.safe_load(manifest.read_text()) or {}).get("source")
        except yaml.YAMLError:
            source = None
        if isinstance(source, str) and _under_frozen(root, root / source):
            return True
    graph_rel = str(node.get("path", ""))
    return bool(graph_rel) and _under_frozen(root, graph_path.parent / graph_rel)


def _absolutize_paths(doc: dict, base: Path) -> None:
    """Rewrite every path-form node `path` to an absolute path resolved
    from the ORIGINAL graph's directory (PR #62 review P1: staged copies
    live in an unpredictable tmpdir, and dora resolves paths against ITS
    base — one authoritative base for validator and runtime, or a staged
    replacement can retain an approved id while resolving different
    code). pip: forms are names, not paths — untouched."""
    for node in doc.get("nodes") or []:
        path = node.get("path")
        if isinstance(path, str) and path and not path.startswith("pip:"):
            node["path"] = str((base / path).resolve()) if not Path(path).is_absolute() else path


def swapped_graph_doc(graph_path: Path, node_id: str, replacement: dict, root: Path) -> dict | str:
    """The POST-SWAP graph document with the named node replaced in place,
    or an error STRING (CON-8: refusals are JSON on stdout, never a
    SystemExit-to-stderr)."""
    from aisle.harness.registry import load_manifests
    from aisle.harness.validate import graph_perception_rung

    doc = yaml.safe_load(graph_path.read_text())
    nodes = doc.get("nodes") or []
    for index, node in enumerate(nodes):
        if node.get("id") == node_id:
            if _frozen_anchor(root, graph_path, node_id, node):
                return (
                    f"{node_id!r} is a trust anchor (budget guard / frozen set): "
                    "live swaps are refused — VAL-5's topology check assumes its "
                    "CODE is the frozen one (human review required, CON-7)"
                )
            manifest_list, manifest_errors = load_manifests(root)
            manifests = {} if manifest_errors else {m["id"]: m for _, m in manifest_list}
            pre_rung, _, pre_errors = graph_perception_rung(nodes, manifests)
            nodes[index] = replacement
            # issue #127 defense in depth beyond the bridge anchor: the rung
            # binds ANY sim_bridge provider (a future world-model-env bridge
            # is legitimately swappable code), and a swap that changes the
            # declared rung changes WHAT THE RUN MEASURES. FAIL CLOSED on
            # unreadable rungs (PR #135 round-2 review): deferring errors to
            # the post-swap validate fails open — validate checks the POST
            # graph's internal consistency, never invariance against the
            # pre graph, so a registry corrupted for the duration of this
            # call (and restored before validate) would smuggle a rung
            # change through. Invariance that cannot be asserted refuses.
            post_rung, _, post_errors = graph_perception_rung(nodes, manifests)
            if manifest_errors or pre_errors or post_errors:
                return (
                    "swap refused: the perception rung cannot be asserted "
                    "invariant (TC-9, issue #127) — registry or rung "
                    "declaration unreadable; fix the registry "
                    "(harness/registry.py lint) and re-validate the graph"
                )
            if post_rung != pre_rung:
                return (
                    f"swap changes the perception rung {pre_rung} -> {post_rung} "
                    "(TC-9, issue #127): the rung is what the run MEASURES — "
                    "relaunch a new graph instead of mutating the rung live"
                )
            return doc
    return f"node {node_id!r} not in {graph_path}"


def swap_event(root: Path, branch: str, event: dict) -> dict:
    """HAR-12: the append-only swap/probe event log feeding the H4
    iteration-latency table. EVERY attempt is logged, including failures
    (a failed mutation may still have changed the runtime)."""
    ideas = [i.get("id") for i in open_ideas(root, branch)]
    entry = {"ts": time.time(), "open_idea": ideas[-1] if ideas else None, **event}
    path = root / "runs" / "swaps" / f"{branch.replace('/', '__')}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def _node_health(runner, dataflow: str, node_id: str, sleeper, tries: int = 6) -> bool | None:
    """Post-add health of the replacement via `dora node list --format
    json`: True = Running, False = Failed/absent after every retry,
    None = the CLI output is unrecognized. None does NOT refuse — the
    health check is a detection belt, and CLI format drift must not
    brick every swap. At dora cd597e705 a dynamically re-added node
    reports status "Unknown" (the metrics view loses re-added nodes:
    pid "-"), so post-swap health is usually None/"unknown" and the
    episode stream is the authoritative liveness signal (PR #86 live
    retest: 10/10 settle-free swaps, stream healthy throughout)."""
    last: bool | None = False
    for attempt in range(tries):
        proc = runner(["node", "list", "-d", dataflow, "--format", "json"])
        out = getattr(proc, "stdout", "") or ""
        if not out.strip():
            return None  # no output (or a runner without stdout): belt off
        # the CLI emits JSON LINES (one object per node), not an array
        entries = []
        for line in out.splitlines():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        if not entries:
            return None  # non-JSONL output: unrecognized format
        mine = [e for e in entries if isinstance(e, dict) and e.get("node") == node_id]
        if mine:
            status = str(mine[0].get("status", "")).lower()
            if "running" in status:
                return True
            if not ("failed" in status or "exited" in status):
                return None  # unknown status vocabulary: belt off
            last = False
        if attempt < tries - 1:
            sleeper(0.5)
    return last


def swap(
    root: Path,
    graph: Path,
    dataflow: str,
    node_id: str,
    with_yaml: Path,
    embodiment: str,
    branch: str,
    runner=_default_runner,
    settle_s: float = 0.0,
    sleeper=time.sleep,
) -> dict:
    """HAR-10: validate the FULL post-swap graph (every SPEC 060 check)
    BEFORE any runtime mutation; then remove old / add new with the
    original restored on add failure; on success the graph file is
    written back so the next validation sees live reality.

    settle_s defaults to 0: the daemon race it worked around (H4
    shakeout, 2026-07-31; a back-to-back remove->add let the REMOVED
    process's kill-on-drop land ~15 ms after the add, its Signal(9) exit
    was attributed to the node identity, and the daemon marked the fresh
    replacement failed; filed as dora-rs/dora#2916) is FIXED at our pin
    (eec31a40b, in cd597e705) — retest-confirmed live with repeated
    settle-free identity swaps on a running expert_t0 stream (PR #86).
    The parameter stays as an escape hatch for older daemons; the
    post-add health check remains the detection belt, and an unhealthy
    replacement is rolled back."""

    def refused(error: str) -> dict:
        swap_event(root, branch, {"action": "swap_refused", "dataflow": dataflow, "node": node_id})
        return {"ok": False, "error": error}

    replacement = yaml.safe_load(with_yaml.read_text())
    if not isinstance(replacement, dict) or replacement.get("id") != node_id:
        return refused(
            "replacement yaml must be a single node doc with the SAME id "
            "(edges are preserved by identity)"
        )
    doc = swapped_graph_doc(graph, node_id, replacement, root)
    if isinstance(doc, str):
        return refused(doc)
    # ONE authoritative base (PR #62 review P1): absolutize before the
    # staged validation AND the staged node handed to `dora node add` —
    # the replacement's relative path is resolved from the original
    # graph's directory, same as every other node
    _absolutize_paths(doc, graph.parent)
    replacement = next(n for n in doc["nodes"] if n["id"] == node_id)
    original = next(n for n in yaml.safe_load(graph.read_text())["nodes"] if n["id"] == node_id)
    _absolutize_paths({"nodes": [original]}, graph.parent)

    # unpredictable 0700 tmpdir OUTSIDE the session-writable graphs/ dir;
    # byte-hash re-checked right before the mutation (TOCTOU, PR #50)
    tmpdir = Path(tempfile.mkdtemp(prefix="aisle-swap-"))
    try:
        staged_graph = tmpdir / "post-swap-graph.yaml"
        staged_graph.write_text(yaml.safe_dump(doc, sort_keys=False))
        staged_node = tmpdir / "node.yaml"
        node_bytes = yaml.safe_dump(replacement, sort_keys=False).encode()
        staged_node.write_bytes(node_bytes)
        node_sha = hashlib.sha256(node_bytes).hexdigest()

        report = validate(staged_graph, root, embodiment, allow_unproven=False)
        if not report["ok"]:
            swap_event(
                root, branch, {"action": "swap_refused", "dataflow": dataflow, "node": node_id}
            )
            return {"ok": False, "refused": report}

        if hashlib.sha256(staged_node.read_bytes()).hexdigest() != node_sha:
            return refused("staged node changed after validation (TOCTOU)")

        removed = runner(["node", "remove", "-d", dataflow, node_id])
        if removed.returncode != 0:
            swap_event(
                root, branch, {"action": "swap_failed", "dataflow": dataflow, "node": node_id}
            )
            return {
                "ok": False,
                "error": f"dora node remove failed: {(removed.stderr or '')[-200:]}",
            }
        # settle_s is 0 unless a caller opts into the pre-#2916 workaround
        if settle_s:
            sleeper(settle_s)
        added = runner(["node", "add", "-d", dataflow, "--from-yaml", str(staged_node)])
        if added.returncode != 0:
            # restore the original so the live dataflow is never left
            # without the node (PR #50: no-rollback finding)
            restore_file = tmpdir / "restore.yaml"
            restore_file.write_text(yaml.safe_dump(original, sort_keys=False))
            restored = runner(["node", "add", "-d", dataflow, "--from-yaml", str(restore_file)])
            swap_event(
                root, branch, {"action": "swap_failed", "dataflow": dataflow, "node": node_id}
            )
            return {
                "ok": False,
                "error": f"dora node add failed: {(added.stderr or '')[-200:]}",
                "restored": restored.returncode == 0,
                "degraded": restored.returncode != 0,
            }
        health = _node_health(runner, dataflow, node_id, sleeper)
        if health is False:
            # the replacement registered but is not running — the race's
            # signature. Roll back rather than leave a dead node on a
            # live stream (remove the corpse, re-add the original).
            runner(["node", "remove", "-d", dataflow, node_id])
            if settle_s:
                sleeper(settle_s)
            restore_file = tmpdir / "restore.yaml"
            restore_file.write_text(yaml.safe_dump(original, sort_keys=False))
            restored = runner(["node", "add", "-d", dataflow, "--from-yaml", str(restore_file)])
            swap_event(
                root, branch, {"action": "swap_failed", "dataflow": dataflow, "node": node_id}
            )
            return {
                "ok": False,
                "error": "replacement unhealthy after add (not Running)",
                "restored": restored.returncode == 0,
                "degraded": restored.returncode != 0,
            }
    finally:
        for leftover in tmpdir.glob("*"):
            leftover.unlink(missing_ok=True)
        tmpdir.rmdir()

    graph.write_text(yaml.safe_dump(doc, sort_keys=False))  # live reality persisted
    event = swap_event(root, branch, {"action": "swap", "dataflow": dataflow, "node": node_id})
    return {
        "ok": True,
        "swapped": node_id,
        "dataflow": dataflow,
        "ts": event["ts"],
        "replacement_health": "running" if health else "unknown",
    }


def probe(
    root: Path,
    dataflow: str,
    topic: str,
    seconds: float,
    branch: str,
    runner=_default_runner,
) -> dict:
    """HAR-11: attach a temporary read-only inspector to a live topic and
    detach after the window — detach runs in a finally so an interrupted
    window can never leak the probe silently. oracle_state is refused
    (VAL-6 has no probe exemption); probes have no outputs so they can
    never publish."""

    def probe_refused(error: str) -> dict:
        swap_event(root, branch, {"action": "probe_refused", "dataflow": dataflow, "topic": topic})
        return {"ok": False, "error": error}

    if topic.endswith("/oracle_state"):
        return probe_refused("probes may not read ground truth (VAL-6)")
    if seconds < 0:
        return probe_refused("probe window must be >= 0 seconds")
    probe_id = f"probe-{uuid.uuid4().hex[:8]}"
    node_doc = {
        "id": probe_id,
        # dynamic adds spawn WITHOUT the dataflow's --uv wrapping (H4
        # shakeout: the recorder died at import under the daemon's bare
        # python, ExitCode(1) before register; dora-rs/dora#2918) — spawn
        # via THIS interpreter. The PYTHONPATH pin that used to sit here
        # is gone: the daemon no longer resolves the interpreter symlink
        # before exec (dora-rs/dora#2942, fixed at our pin cd597e705), so
        # the venv's pyvenv.cfg discovery works and site-packages resolve
        # normally (live probe retest in PR #86).
        "path": sys.executable,
        "args": str(Path(__file__).with_name("trace_recorder.py")),
        "inputs": {"probe": {"source": topic, "queue_size": 100}},
        "env": {"AISLE_TRACE_DIR": str(root / "runs" / "probes" / probe_id)},
    }
    staged = root / "runs" / "probes" / f"{probe_id}.yaml"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(yaml.safe_dump(node_doc, sort_keys=False))
    proc = runner(["node", "add", "-d", dataflow, "--from-yaml", str(staged)])
    if proc.returncode != 0:
        swap_event(root, branch, {"action": "probe_failed", "dataflow": dataflow, "topic": topic})
        return {"ok": False, "error": f"attach failed: {(proc.stderr or '')[-200:]}"}
    try:
        time.sleep(seconds)
    finally:
        detach = runner(["node", "remove", "-d", dataflow, probe_id])
        event = swap_event(root, branch, {"action": "probe", "dataflow": dataflow, "topic": topic})
    if detach.returncode != 0:
        return {
            "ok": False,
            "error": f"detach FAILED — probe {probe_id!r} may still be attached; "
            f"remove it manually: dora node remove -d {dataflow} {probe_id}",
            "probe": probe_id,
        }
    return {
        "ok": True,
        "probe": probe_id,
        "traces": str(root / "runs" / "probes" / probe_id),
        "ts": event["ts"],
    }
