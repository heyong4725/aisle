"""MON-1 treatment-difference table validation (SPEC 440)."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_HASH = re.compile(r"^[0-9a-f]{64}$")
_KINDS = {"identical", "representation-equivalent", "intentionally-different"}


class TreatmentTableError(ValueError):
    """A treatment table cannot establish a complete frozen comparison."""


class TypedSurfaceError(ValueError):
    """A typed deliverable escapes its frozen editable surface."""


def validate_interface_map(fields: list[dict[str, Any]]) -> None:
    """Require exact semantic field/authority parity across both arms."""
    if not fields:
        raise TypedSurfaceError("interface map is empty")
    seen: set[str] = set()
    for field in fields:
        required = {"name", "typed", "monolithic", "authority"}
        if set(field) != required:
            raise TypedSurfaceError("interface field declaration is incomplete")
        name = field["name"]
        if not isinstance(name, str) or not name or name in seen:
            raise TypedSurfaceError("interface field names must be unique")
        seen.add(name)
        if field["typed"] != field["monolithic"]:
            raise TypedSurfaceError(f"semantic field mismatch: {name}")
        if field["authority"] not in {"task", "transport"}:
            raise TypedSurfaceError(f"invalid authority class: {name}")


_MONOLITHIC_FORBIDDEN = ("manifest", "registry", "resolver", "validator", "diagnostic", ".yaml")


def validate_monolithic_surface(editable_files: list[str]) -> None:
    """Reject typed-dataflow facilities from the MON-3 single-module view."""
    for relative in editable_files:
        lowered = relative.lower()
        if any(token in lowered for token in _MONOLITHIC_FORBIDDEN):
            raise TypedSurfaceError(f"forbidden typed facility in monolithic view: {relative}")


def validate_broker_route(route: list[str], *, motion: bool = True) -> None:
    """Require trusted controller, broker, and guard routing before actuation."""
    if not route or route[0] != "trusted_controller" or "primitive_broker" not in route:
        raise TypedSurfaceError("action route must enter trusted controller and primitive broker")
    if motion and "budget_guard" not in route:
        raise TypedSurfaceError("motion route must traverse budget guard")


def validate_trusted_preflight(state: dict[str, Any]) -> None:
    """Fail closed when trusted hashes, confinement, routing, or sink are unresolved."""
    required = ("hashes", "confinement", "route_map", "evidence_sink")
    if any(state.get(key) in (None, False, {}, "") for key in required):
        raise TypedSurfaceError("trusted preflight is unresolved")
    if state["confinement"] is not True:
        raise TypedSurfaceError("confinement is not attested")


_BYPASS_TOKENS = {
    "driver": "direct_driver",
    "unguarded": "unguarded_motion",
    "reset": "out_of_band_reset",
    "scorer": "scorer_access",
    "verifier": "verifier_access",
    "seed": "hidden_seed_access",
    "socket": "network_access",
    "subprocess": "process_access",
    "traversal": "path_traversal",
}


def classify_bypass_attempt(attempt: str) -> str:
    """Classify a MON-7 bypass attempt; unknown attempts fail closed."""
    lowered = attempt.strip().lower()
    for token, category in _BYPASS_TOKENS.items():
        if token in lowered:
            return category
    raise TypedSurfaceError("unclassified bypass attempt")


def validate_expert_artifacts(artifacts: list[dict[str, Any]]) -> None:
    """Require provenance and immutable hashes for one expert artifact per arm."""
    if {item.get("arm") for item in artifacts} != {"typed", "monolithic"}:
        raise TypedSurfaceError("expert artifacts must include exactly typed and monolithic arms")
    for item in artifacts:
        if not all(
            isinstance(item.get(key), str) and item[key].strip()
            for key in ("arm", "author", "path")
        ):
            raise TypedSurfaceError("expert artifact provenance is unresolved")
        if not isinstance(item.get("sha256"), str) or not _HASH.fullmatch(item["sha256"]):
            raise TypedSurfaceError("expert artifact hash is invalid")


def validate_parity_protocol(protocol: dict[str, Any]) -> None:
    """Require a frozen, non-confirmatory expert-parity protocol."""
    required = {
        "purpose",
        "tasks",
        "paired_seeds",
        "acceptance",
        "resource_ceiling",
        "stopping_rule",
    }
    if set(protocol) != required:
        raise TypedSurfaceError("parity protocol fields are incomplete")
    if protocol["purpose"] != "expert_parity":
        raise TypedSurfaceError("parity protocol purpose must be expert_parity")
    if not isinstance(protocol["tasks"], list) or not protocol["tasks"]:
        raise TypedSurfaceError("parity protocol tasks are unresolved")
    if not isinstance(protocol["paired_seeds"], list) or not protocol["paired_seeds"]:
        raise TypedSurfaceError("parity protocol paired_seeds are unresolved")


def validate_campaign_purpose(record: dict[str, Any]) -> None:
    """Keep expert-parity records out of pilot and confirmatory analyses."""
    if record.get("campaign_purpose") != "expert_parity":
        raise TypedSurfaceError("record is not isolated to expert_parity campaign")
    if record.get("pooled", False) is not False:
        raise TypedSurfaceError("expert-parity record cannot be pooled")


def validate_common_evidence(
    typed: dict[str, Any], monolithic: dict[str, Any], required: set[str]
) -> None:
    """Require identical common-evidence envelope keys across both arms."""
    if set(typed) != required or set(monolithic) != required:
        raise TypedSurfaceError("common evidence envelope keys are incomplete or asymmetric")


def validate_matched_treatment(
    typed: dict[str, Any], monolithic: dict[str, Any], differing: set[str]
) -> None:
    """Require shared treatment identity; only declared fields may differ."""
    if set(typed) != set(monolithic):
        raise TypedSurfaceError("treatment identity fields differ")
    for key in typed:
        if key not in differing and typed[key] != monolithic[key]:
            raise TypedSurfaceError(f"undeclared treatment difference: {key}")


def validate_typed_graph(graph_path, root, embodiment: str, allow_unproven: bool = False) -> dict:
    """Run the pinned graph validator; authored deliverables are never repaired."""
    from aisle.harness.validate import validate

    report = validate(graph_path, root, embodiment, allow_unproven)
    if not isinstance(report, dict) or "ok" not in report or "errors" not in report:
        raise TypedSurfaceError("typed validator returned an invalid report")
    return report


def validate_typed_surface(root, editable_files: list[str], allowlist: list[str]) -> None:
    """Validate MON-2's authored typed surface without repairing it."""
    if sorted(editable_files) != sorted(set(editable_files)):
        raise TypedSurfaceError("allowlist contains duplicate files")
    if set(editable_files) - set(allowlist):
        raise TypedSurfaceError("editable files exceed the frozen allowlist")
    base = root.resolve()
    for relative in editable_files:
        path = (base / relative).resolve()
        if base not in path.parents and path != base:
            raise TypedSurfaceError("editable file escapes root")
        if not path.is_file():
            raise TypedSurfaceError(f"missing editable file: {relative}")


def validate_treatment_table(table: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(table, dict)
        or table.get("schema_version") != "aisle.monolithic-treatment.v1"
    ):
        raise TreatmentTableError("invalid treatment-table schema")
    rows = table.get("rows")
    if not isinstance(rows, list) or not rows:
        raise TreatmentTableError("treatment table rows are required")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise TreatmentTableError("each treatment row must be an object")
        required = {
            "id",
            "surface",
            "classification",
            "typed",
            "monolithic",
            "justification",
            "analysis",
        }
        if set(row) != required:
            raise TreatmentTableError("treatment row fields are incomplete or undeclared")
        if not isinstance(row["id"], str) or not row["id"] or row["id"] in seen:
            raise TreatmentTableError("treatment row ids must be unique")
        seen.add(row["id"])
        if row["classification"] not in _KINDS:
            raise TreatmentTableError("invalid treatment classification")
        if not all(isinstance(row[k], str) and row[k].strip() for k in required - {"id"}):
            raise TreatmentTableError("treatment row values must be resolved strings")
        for arm in ("typed", "monolithic"):
            path, digest = row[arm].split("#sha256:", 1) if "#sha256:" in row[arm] else ("", "")
            if not path or not _HASH.fullmatch(digest):
                raise TreatmentTableError(f"{arm} artifact must contain a path and SHA-256")
    canonical = json.dumps(
        table, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return {
        "valid": True,
        "immutable_id": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "row_count": len(rows),
    }


def validate_artifact_hashes(declared: dict[str, str], observed: dict[str, str]) -> None:
    """Fail closed on missing, extra, malformed, or drifted artifacts."""
    if set(declared) != set(observed) or not declared:
        raise TypedSurfaceError("artifact identity set is incomplete or asymmetric")
    for name, digest in declared.items():
        if not _HASH.fullmatch(digest) or observed[name] != digest:
            raise TypedSurfaceError(f"artifact hash drift: {name}")


def validate_conformance(checks: dict[str, bool], required: set[str]) -> None:
    """Fail closed unless every declared MON conformance check passes."""
    if set(checks) != required or not required or not all(checks.values()):
        raise TypedSurfaceError("monolithic conformance is incomplete or failed")
