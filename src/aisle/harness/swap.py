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
import sysconfig
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
            nodes[index] = replacement
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
    health check is a detection belt (the remove->add race itself is
    prevented by the settle delay), and CLI format drift must not brick
    every swap."""
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
    settle_s: float = 2.0,
    sleeper=time.sleep,
) -> dict:
    """HAR-10: validate the FULL post-swap graph (every SPEC 060 check)
    BEFORE any runtime mutation; then remove old / SETTLE / add new with
    the original restored on add failure; on success the graph file is
    written back so the next validation sees live reality.

    The settle delay works around a dora daemon race (H4 shakeout,
    2026-07-31, pinned rev 7eb4a5f; filed as dora-rs/dora#2916 — FIXED
    upstream in eec31a40b, retest-confirmed 2026-07-31; the settle
    stays while our pin predates the fix and is harmless after): a back-to-back remove->add lets the
    REMOVED process's kill-on-drop land ~15 ms AFTER the add, and its
    Signal(9) exit is attributed to the node identity — the daemon marks
    the freshly added replacement failed ("node added successfully" then
    "node exited with error: Signal(9)"; every later episode
    never_grasped). Settling until the old process's exit is accounted
    before re-adding avoids the race; the post-add health check is the
    detection belt, and an unhealthy replacement is rolled back."""

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
        # the race workaround (docstring): the old process's exit must be
        # accounted before the add, or its Signal(9) poisons the new node
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
        # via THIS
        # interpreter, and pin PYTHONPATH to its site-packages: the
        # daemon resolves the interpreter SYMLINK before exec, which
        # bypasses pyvenv.cfg discovery and loses the venv (observed:
        # venv python path in the yaml, ModuleNotFoundError: numpy)
        "path": sys.executable,
        "args": str(Path(__file__).with_name("trace_recorder.py")),
        "inputs": {"probe": {"source": topic, "queue_size": 100}},
        "env": {
            "AISLE_TRACE_DIR": str(root / "runs" / "probes" / probe_id),
            "PYTHONPATH": sysconfig.get_paths()["purelib"],
        },
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
