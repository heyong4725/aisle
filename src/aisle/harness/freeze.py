"""Content-addressed campaign freeze registry (CSE-15, FEL-18, SFE-9, SEM-9,
BND-12, FLT-9, MON-10, HWP-14, RPR-9; CON-5, CON-8).

Every confirmatory, calibration, or ablation campaign spec asks for the same
thing before scored data: a machine-readable manifest that binds hypotheses,
endpoints, margins, exclusions, task/fault sets, seed commitments, budgets,
integrity gates, analysis code, and exact commands to content hashes, and
that refuses later drift. One declaration format and one registry serve all
of them; the campaign-specific validators (non_oracle, causal_study, ...)
keep their own field rules on top.

Two statuses only. `frozen` requires every integrity gate to have PASSED at a
retained record and an explicit timestamp; anything else is
`registered_pending_review`. A pending external review (STA-12, CON-14) is a
gate like any other, so the registry can never self-attest a freeze the spec
hands to a human.

Pure functions over a repo root (CON-12); no wall clock is read (CON-5).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

DECLARATION_SCHEMA = "aisle.freeze.declaration.v1"
MANIFEST_SCHEMA = "aisle.freeze.manifest.v1"
PURPOSES = ("pre_registration", "calibration", "ablation", "confirmatory")
GATE_KINDS = ("machine_check", "external_review")
GATE_STATUSES = ("pending", "passed", "failed")
ENDPOINT_STATUSES = ("inferential", "descriptive", "exploratory")
REQUIRED_TOP = (
    "schema_version",
    "campaign_id",
    "spec",
    "issue",
    "purpose",
    "hypotheses",
    "endpoints",
    "decision_rules",
    "exclusions",
    "instrument_set",
    "seed_commitment",
    "budgets",
    "integrity_checks",
    "artifacts",
    "analysis",
    "commands",
)
REQUIRED_DECISION = ("smallest_effect", "alpha", "power", "equivalence_margin", "stopping_rule")
REQUIRED_EXCLUSIONS = ("infrastructure", "treatment_integrity", "rerun_policy", "deviation_policy")
_SKIP_PARTS = {"__pycache__", ".DS_Store"}


class FreezeError(Exception):
    """Refusal with the CON-8 details list."""

    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.details = details or []


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_path(root: Path, rel: str) -> str:
    """`sha256:<hex>` of one file, or of a directory as sorted (relative
    path, file digest) pairs; missing paths refuse rather than hash empty."""
    target = (root / rel).resolve()
    if not target.exists():
        raise FreezeError("frozen artifact is missing", [rel])
    if target.is_file():
        return "sha256:" + sha256_hex(target.read_bytes())
    digest = hashlib.sha256()
    files = sorted(p for p in target.rglob("*") if p.is_file() and not (_SKIP_PARTS & set(p.parts)))
    if not files:
        raise FreezeError("frozen artifact directory is empty", [rel])
    for path in files:
        digest.update(path.relative_to(target).as_posix().encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return "sha256:" + digest.hexdigest()


def _check_shape(declaration: dict[str, Any]) -> list[str]:
    errors = []
    missing = [key for key in REQUIRED_TOP if key not in declaration]
    if missing:
        return [f"declaration is missing {name}" for name in missing]
    if declaration["schema_version"] != DECLARATION_SCHEMA:
        errors.append("declaration schema_version is unsupported")
    if declaration["purpose"] not in PURPOSES:
        errors.append("declaration purpose is unknown")
    if not isinstance(declaration["issue"], int):
        errors.append("declaration issue must be an integer issue number")
    if not declaration["hypotheses"] or any(
        not {"id", "statement", "direction"} <= set(h) for h in declaration["hypotheses"]
    ):
        errors.append("every hypothesis needs id, statement, and direction")
    endpoints = declaration["endpoints"]
    if not isinstance(endpoints, dict) or not endpoints.get("primary"):
        errors.append("at least one primary endpoint is required")
    else:
        for group in ("primary", "secondary"):
            for endpoint in endpoints.get(group, []):
                needed = {"id", "outcome", "unit", "aggregation", "direction", "status"}
                if not needed <= set(endpoint) or endpoint["status"] not in ENDPOINT_STATUSES:
                    errors.append(f"{group} endpoint is incomplete")
    if any(key not in declaration["decision_rules"] for key in REQUIRED_DECISION):
        errors.append("decision_rules must name smallest_effect, alpha, power, margin, stopping")
    if any(key not in declaration["exclusions"] for key in REQUIRED_EXCLUSIONS):
        errors.append("exclusions must name infrastructure, integrity, rerun, and deviation rules")
    instrument = declaration["instrument_set"]
    if (
        not isinstance(instrument, dict)
        or not instrument.get("kind")
        or not instrument.get("items")
    ):
        errors.append("instrument_set needs a kind and a non-empty items list")
    commitment = declaration["seed_commitment"]
    if not isinstance(commitment, dict) or not {"seeds_source", "salt_source"} <= set(commitment):
        errors.append("seed_commitment needs seeds_source and salt_source")
    if not isinstance(declaration["budgets"], dict) or not declaration["budgets"]:
        errors.append("budgets must be a non-empty mapping")
    for gate in declaration["integrity_checks"]:
        needed = {"gate", "kind", "status", "record", "owner_role"}
        if (
            not needed <= set(gate)
            or gate["kind"] not in GATE_KINDS
            or gate["status"] not in GATE_STATUSES
        ):
            errors.append("integrity check is incomplete or has an unknown kind/status")
        elif gate["status"] == "passed" and not gate["record"]:
            errors.append(f"passed gate {gate['gate']} must name its retained record")
    if not isinstance(declaration["artifacts"], dict) or not declaration["artifacts"]:
        errors.append("artifacts must name at least one frozen path")
    analysis = declaration["analysis"]
    if not isinstance(analysis, dict) or not analysis.get("scripts") or "seed" not in analysis:
        errors.append("analysis needs scripts and a seed")
    if not declaration["commands"]:
        errors.append("commands must list the exact regeneration commands")
    return errors


def _seed_commitment(root: Path, commitment: dict[str, Any]) -> str:
    """sha256(salt || canonical seeds): the values stay outside the manifest
    (BND-13 withholding) while the commitment pins them."""
    seeds_path = Path(commitment["seeds_source"]).expanduser()
    salt_path = Path(commitment["salt_source"]).expanduser()
    if not seeds_path.is_absolute():
        seeds_path = root / seeds_path
    if not salt_path.is_absolute():
        salt_path = root / salt_path
    missing = [str(p) for p in (seeds_path, salt_path) if not p.is_file()]
    if missing:
        raise FreezeError("seed commitment sources are missing", missing)
    seeds = json.loads(seeds_path.read_text())
    if not isinstance(seeds, list) or not seeds:
        raise FreezeError("seed source must be a non-empty JSON list", [str(seeds_path)])
    return "sha256:" + sha256_hex(salt_path.read_bytes() + canonical_bytes(seeds))


def _gate_records(root: Path, gates: list[dict[str, Any]]) -> dict[str, str]:
    hashes = {}
    for gate in gates:
        if gate["status"] == "passed":
            hashes[gate["gate"]] = hash_path(root, gate["record"])
    return hashes


def build_manifest(
    root: Path,
    declaration: dict[str, Any],
    *,
    git_head: str | None,
    timestamp: str | None = None,
    timestamp_source: str | None = None,
    skip_seed_commitment: bool = False,
) -> dict[str, Any]:
    """Hash every declared artifact and script; decide the freeze status.
    `skip_seed_commitment` exists only for check_manifest on a tree that
    withholds the seed sources; a build always commits."""
    errors = _check_shape(declaration)
    if errors:
        raise FreezeError("declaration is invalid", errors)
    artifact_hashes = {
        name: hash_path(root, rel) for name, rel in sorted(declaration["artifacts"].items())
    }
    script_hashes = {
        rel: hash_path(root, rel) for rel in sorted(declaration["analysis"]["scripts"])
    }
    gate_hashes = _gate_records(root, declaration["integrity_checks"])
    pending = sorted(g["gate"] for g in declaration["integrity_checks"] if g["status"] != "passed")
    if timestamp is not None and not timestamp_source:
        raise FreezeError("a timestamp requires its source", ["timestamp_source"])
    frozen = not pending and timestamp is not None
    return {
        "schema_version": MANIFEST_SCHEMA,
        "campaign_id": declaration["campaign_id"],
        "spec": declaration["spec"],
        "issue": declaration["issue"],
        "purpose": declaration["purpose"],
        "status": "frozen" if frozen else "registered_pending_review",
        "frozen": frozen,
        "pending_gates": pending,
        "declaration_sha256": "sha256:" + sha256_hex(canonical_bytes(declaration)),
        "git_head": git_head,
        "timestamp": timestamp,
        "timestamp_source": timestamp_source,
        "artifact_hashes": artifact_hashes,
        "analysis_script_hashes": script_hashes,
        "analysis_seed": declaration["analysis"]["seed"],
        "seed_commitment": None
        if skip_seed_commitment
        else _seed_commitment(root, declaration["seed_commitment"]),
        "gate_record_hashes": gate_hashes,
        "commands": list(declaration["commands"]),
        "declaration": declaration,
    }


def _seed_sources_present(root: Path, commitment: dict[str, Any]) -> bool:
    paths = [Path(commitment[key]).expanduser() for key in ("seeds_source", "salt_source")]
    return all((p if p.is_absolute() else root / p).is_file() for p in paths)


def check_manifest(
    root: Path, manifest: dict[str, Any], *, require_seed_sources: bool = True
) -> dict[str, Any]:
    """Recompute every hash the manifest binds; any difference is drift.

    Held-out seed sources live outside the worktree by design (BND-13), so a
    checker without them may pass `require_seed_sources=False`: the report
    then says the commitment is `unverified` instead of inventing a hash."""
    if manifest.get("schema_version") != MANIFEST_SCHEMA or "declaration" not in manifest:
        raise FreezeError("manifest schema is unsupported", [str(manifest.get("schema_version"))])
    declaration = manifest["declaration"]
    seeds_present = _seed_sources_present(root, declaration["seed_commitment"])
    if not seeds_present and require_seed_sources:
        raise FreezeError("seed commitment sources are missing", ["seed_commitment"])
    rebuilt = build_manifest(
        root,
        declaration,
        git_head=manifest.get("git_head"),
        timestamp=manifest.get("timestamp"),
        timestamp_source=manifest.get("timestamp_source"),
        skip_seed_commitment=not seeds_present,
    )
    drift = []
    for section in ("artifact_hashes", "analysis_script_hashes", "gate_record_hashes"):
        for name, expected in manifest.get(section, {}).items():
            actual = rebuilt[section].get(name)
            if actual != expected:
                drift.append(f"{section}.{name}: {expected} -> {actual}")
    for field in ("declaration_sha256", "seed_commitment"):
        if field == "seed_commitment" and not seeds_present:
            continue
        if rebuilt[field] != manifest.get(field):
            drift.append(f"{field}: {manifest.get(field)} -> {rebuilt[field]}")
    return {
        "ok": not drift,
        "schema_version": "aisle.freeze.check.v1",
        "seed_commitment": manifest.get("seed_commitment") if seeds_present else "unverified",
        "campaign_id": manifest.get("campaign_id"),
        "status": manifest.get("status"),
        "frozen": manifest.get("frozen"),
        "pending_gates": manifest.get("pending_gates", []),
        "drift": drift,
    }
