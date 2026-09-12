"""Allocate actual worker declarations for the authored nodes of a validated snapshot."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from aisle.harness.matched_surface import LEGACY_SURFACE, record_surface
from aisle.harness.matched_surface import task_surface as resolve_task_surface
from aisle.harness.typed_graph_hosts import _source
from aisle.harness.typed_graph_stage import _validation
from aisle.harness.typed_snapshot import _read, verify_typed_validation_snapshot
from aisle.harness.worker_declaration import provision_worker_declaration


def authored_worker_nodes(graph, *, task_surface=LEGACY_SURFACE):
    """Select every authored source instance, preserving graph-defined node identities."""
    resolve_task_surface(task_surface)
    if type(graph) is not dict or type(graph.get("nodes")) is not list:
        raise ValueError("typed worker graph must contain a node list")
    selected, seen = {}, set()
    for node in graph["nodes"]:
        if (
            type(node) is not dict
            or type(node.get("id")) is not str
            or re.fullmatch("[A-Za-z0-9_-]+", node["id"]) is None
            or node["id"] in seen
        ):
            raise ValueError("typed worker graph has invalid or duplicate node identities")
        seen.add(node["id"])
        source = _source(node, task_surface=task_surface)
        if source is not None:
            selected[node["id"]] = source
    return selected


def provision_typed_workers(
    *,
    snapshot,
    snapshot_record,
    validation_output,
    allocation_root,
    evidence,
    hidden_roots,
    runtime_record,
    python,
    python_sha256,
    adapter_sha256,
    timeout_s,
    max_calls=100000,
):
    """Provision one launch's workers only after normal validation of the exact snapshot.

    Bundles remain reserved and empty; prepare_typed_stages owns subsequent source
    capture and stage preflight. Each call requires fresh paths, including retries.
    """
    snapshot, validation_output, allocation_root, evidence = (
        Path(p).absolute() for p in (snapshot, validation_output, allocation_root, evidence)
    )
    verify_typed_validation_snapshot(snapshot, snapshot_record)
    _validation(validation_output, snapshot_record)
    surface = record_surface(snapshot_record)
    selected = authored_worker_nodes(
        yaml.safe_load(_read(snapshot, surface.typed_graph)), task_surface=surface.identity
    )
    if not selected:
        raise ValueError("typed graph has no authored worker sources to provision")
    hidden = tuple(
        dict.fromkeys(
            [
                *(Path(p).absolute() for p in hidden_roots),
                Path(snapshot_record["controller_root"]),
                Path(snapshot_record["participant_root"]),
                snapshot,
                validation_output,
            ]
        )
    )
    if any(p.resolve() != p or not p.is_dir() or p == Path("/") for p in hidden):
        raise ValueError("typed worker hidden roots must be canonical existing directories")
    for root in (allocation_root, evidence):
        if root.resolve() != root or root.exists() or root.is_symlink():
            raise ValueError("typed worker provisioning requires fresh canonical roots")
    protected = (*hidden, evidence, *(Path(p) for p in runtime_record["trees"]))
    if any(
        allocation_root.is_relative_to(p) or p.is_relative_to(allocation_root) for p in protected
    ):
        raise ValueError("typed worker allocation overlaps protected state")
    if any(
        evidence.is_relative_to(Path(p)) or Path(p).is_relative_to(evidence)
        for p in runtime_record["trees"]
    ):
        raise ValueError("typed worker evidence overlaps runtime")
    evidence.mkdir(parents=True, exist_ok=False)
    receipt = {
        "schema_version": "aisle.typed-worker-provisioning.v1",
        "ok": False,
        "snapshot_id": snapshot_record["immutable_id"],
        "workers": {},
        "error": None,
    }
    try:
        allocation_root.mkdir(parents=True, exist_ok=False)
        declarations = {}
        for node_id in sorted(selected):
            name = hashlib.sha256(node_id.encode()).hexdigest()
            allocation = allocation_root / name
            declarations[node_id] = provision_worker_declaration(
                arm="typed",
                bundle=allocation / "bundle",
                home=allocation / "home",
                evidence=evidence / name,
                hidden_roots=(*hidden, evidence),
                runtime_record=runtime_record,
                python=python,
                python_sha256=python_sha256,
                adapter_sha256=adapter_sha256,
                timeout_s=timeout_s,
                max_calls=max_calls,
            )
            receipt["workers"][node_id] = {
                "source": selected[node_id],
                "declaration": f"{name}/declaration.json",
                "declaration_sha256": hashlib.sha256(
                    _read(evidence / name, "declaration.json")
                ).hexdigest(),
            }
        verify_typed_validation_snapshot(snapshot, snapshot_record)
        _validation(validation_output, snapshot_record)
        receipt["ok"] = True
        return declarations
    except BaseException as exc:
        receipt["error"] = str(exc) or type(exc).__name__
        raise
    finally:
        (evidence / "provisioning.json").write_text(json.dumps(receipt, allow_nan=False))
