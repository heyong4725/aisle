"""Deterministic fail-closed submission validation (BMK-13, BMK-14; SPEC
540, issue #357).

Returns every machine-readable reason without repairing the bundle:
missing or unknown fields against the committed submission schema, version
drift, digest format, treatment and parity mismatch across sessions,
incomplete session denominators, budget overrun against the participant
contract, leaked private markers, unattested execution, unregistered
exclusions, and a participant-supplied score. Pure (CON-12).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_REL = Path("docs/benchmark/v1/submission.schema.json")
BENCHMARK_VERSION = "aisle-benchmark-v1-draft"
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PRIVATE_MARKERS = (
    "aisle-private",
    "bank.json",
    "reveal-key",
    "sealed-ledger",
    "hidden_seed",
    "calibration-report.json",
)
BUDGETS = {"tokens": 200000, "wall_seconds": 3600.0}
REGISTERED_EXCLUSIONS = {"infrastructure", "treatment_integrity"}


def _load_schema(root: Path) -> dict:
    return json.loads((root / SCHEMA_REL).read_text())


def _check_object(value: Any, schema: dict, path: str, problems: list[str]) -> None:
    if not isinstance(value, dict):
        problems.append(f"{path}: expected object")
        return
    required = set(schema.get("required", []))
    props = schema.get("properties", {})
    for name in sorted(required - set(value)):
        problems.append(f"{path}.{name}: missing")
    if schema.get("additionalProperties") is False:
        for name in sorted(set(value) - set(props)):
            problems.append(f"{path}.{name}: unknown field")
    for name, sub in props.items():
        if name not in value:
            continue
        item = value[name]
        if "const" in sub and item != sub["const"]:
            problems.append(f"{path}.{name}: must equal {sub['const']!r}")
        if "enum" in sub and item not in sub["enum"]:
            problems.append(f"{path}.{name}: not one of {sub['enum']}")
        if sub.get("type") == "object" and isinstance(item, dict):
            _check_object(item, sub, f"{path}.{name}", problems)
        if sub.get("type") == "array":
            if not isinstance(item, list):
                problems.append(f"{path}.{name}: expected array")
            elif sub.get("minItems") and len(item) < sub["minItems"]:
                problems.append(f"{path}.{name}: fewer than {sub['minItems']} items")
            elif "items" in sub:
                for index, element in enumerate(item):
                    _check_object(element, sub["items"], f"{path}.{name}[{index}]", problems)
        if sub.get("additionalProperties") and isinstance(item, dict):
            pattern = sub["additionalProperties"].get("pattern")
            if pattern:
                for key, digest in item.items():
                    if not isinstance(digest, str) or not re.fullmatch(pattern, digest):
                        problems.append(f"{path}.{name}.{key}: digest format invalid")


def _scan_markers(value: Any, path: str, problems: list[str]) -> None:
    if isinstance(value, str):
        for marker in PRIVATE_MARKERS:
            if marker in value:
                problems.append(f"{path}: leaked private marker {marker!r}")
    elif isinstance(value, dict):
        for key, item in value.items():
            _scan_markers(item, f"{path}.{key}", problems)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_markers(item, f"{path}[{index}]", problems)


def validate_submission(bundle: Any, *, root: Path) -> list[str]:
    """Every reason, deterministically ordered; an empty list is valid."""
    problems: list[str] = []
    schema = _load_schema(root)
    _check_object(bundle, schema, "bundle", problems)
    if not isinstance(bundle, dict):
        return problems
    if bundle.get("benchmark_version") not in (None, BENCHMARK_VERSION):
        problems.append("bundle.benchmark_version: version drift")
    if bundle.get("declared_score") is not None:
        problems.append("bundle.declared_score: participant-supplied score is refused")
    treatment = bundle.get("treatment")
    sessions = bundle.get("sessions") if isinstance(bundle.get("sessions"), list) else []
    ids = [s.get("session_id") for s in sessions if isinstance(s, dict)]
    if len(ids) != len(set(ids)):
        problems.append("bundle.sessions: duplicate session ids")
    for index, session in enumerate(sessions):
        if not isinstance(session, dict):
            continue
        if session.get("treatment") != treatment:
            problems.append(f"bundle.sessions[{index}].treatment: parity mismatch with bundle")
        outcome = session.get("outcome")
        if not isinstance(outcome, dict) or not outcome:
            problems.append(f"bundle.sessions[{index}].outcome: incomplete denominator")
        exclusion = session.get("exclusion")
        if isinstance(exclusion, dict) and exclusion.get("kind") not in REGISTERED_EXCLUSIONS:
            problems.append(f"bundle.sessions[{index}].exclusion: unregistered exclusion kind")
        provenance = session.get("provenance")
        if not isinstance(provenance, dict) or not provenance.get("git_sha"):
            problems.append(f"bundle.sessions[{index}].provenance: unattested execution")
    resources = bundle.get("resources") if isinstance(bundle.get("resources"), dict) else {}
    for field, ceiling in BUDGETS.items():
        value = resources.get(field)
        if isinstance(value, int | float) and value > ceiling * max(len(sessions), 1):
            problems.append(f"bundle.resources.{field}: budget overrun")
    artifacts = bundle.get("artifacts") if isinstance(bundle.get("artifacts"), dict) else {}
    for key in ("authored_hash", "executed_hash"):
        digest = artifacts.get(key)
        if digest is not None and not (isinstance(digest, str) and HASH_RE.fullmatch(digest)):
            problems.append(f"bundle.artifacts.{key}: digest format invalid")
    _scan_markers(bundle, "bundle", problems)
    return sorted(set(problems))
