"""Hashed table of paired task candidates the matched executor may admit.

MON-3 / MON-8: a development protocol v2 names one candidate id and binds the
table's content hash; launch fields (graph, module, verifier, reset, rung) are
derived from the table, never supplied free. BND-4: every artifact the
candidate names must exist in the controller tree. CON-5: same bytes, same id.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

CANDIDATES_SCHEMA = "aisle.matched-candidates.v1"
CANDIDATES_FILE = "docs/monolithic/candidates.json"
DEVELOPMENT_SCHEMA_V1 = "aisle.matched-development.v1"
DEVELOPMENT_SCHEMA_V2 = "aisle.matched-development.v2"
DEVELOPMENT_PURPOSE = "expert_parity"

LAUNCH_FIELDS = ("tier", "embodiment", "perception", "verifier", "reset")
ARTIFACT_FIELDS = ("typed_graph", "turn_plan", "monolithic_template", "monolithic_module")
DOCUMENT_ROLES = ("treatment_table", "interface_map", "allowlist", "experts", "parity_protocol")
CANDIDATE_KEYS = frozenset(("role", *LAUNCH_FIELDS, *ARTIFACT_FIELDS, "documents"))
BUDGET_KEYS = ("seeds", "run_ceiling", "episode_ceiling", "timeout_s")
V2_BASE_KEYS = ("schema_version", "purpose", "candidate", "candidates_sha256", *BUDGET_KEYS)
DERIVED_KEYS = (*LAUNCH_FIELDS, *ARTIFACT_FIELDS, "documents")
_BASE_SET = frozenset(V2_BASE_KEYS)
_FULL_SET = _BASE_SET | frozenset(DERIVED_KEYS)

ALLOWED = {
    "tier": {"T1", "T2"},
    "embodiment": {"franka", "so101"},
    "perception": {"L1", "L2"},
    "verifier": {"oracle", "realistic"},
    "reset": {"teleport", "behavioral"},
}


class CandidateError(ValueError):
    """The candidate table or a protocol bound to it cannot be established."""


def _table_bytes(root: Path) -> bytes:
    try:
        return (Path(root) / CANDIDATES_FILE).read_bytes()
    except OSError as exc:
        raise CandidateError(f"candidate table unreadable: {exc}") from exc


def candidates_sha256(root: Path) -> str:
    return hashlib.sha256(_table_bytes(root)).hexdigest()


def read_candidates(root: Path) -> tuple[dict, str]:
    """One read of the table: the parsed rows and the SHA-256 of the same bytes."""
    raw = _table_bytes(root)
    return _parse_candidates(raw), hashlib.sha256(raw).hexdigest()


def _check_row(candidate_id: str, row: Any) -> None:
    if not isinstance(row, dict) or set(row) != CANDIDATE_KEYS:
        raise CandidateError(f"candidate row is incomplete: {candidate_id}")
    for field, allowed in ALLOWED.items():
        if row[field] not in allowed:
            raise CandidateError(f"candidate {candidate_id} has unsupported {field}")
    documents = row["documents"]
    if not isinstance(documents, dict) or set(documents) != set(DOCUMENT_ROLES):
        raise CandidateError(f"candidate {candidate_id} documents are incomplete")
    for rel in (*(row[field] for field in ARTIFACT_FIELDS), *documents.values()):
        if type(rel) is not str or not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise CandidateError(f"candidate {candidate_id} names a noncanonical artifact")


def _parse_candidates(raw: bytes) -> dict:
    try:
        table = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CandidateError("candidate table is not JSON") from exc
    if (
        not isinstance(table, dict)
        or table.get("schema_version") != CANDIDATES_SCHEMA
        or type(table.get("id")) is not str
        or not isinstance(table.get("candidates"), dict)
        or not table["candidates"]
    ):
        raise CandidateError("candidate table is mis-shaped")
    for candidate_id, row in table["candidates"].items():
        _check_row(candidate_id, row)
    return table


def candidate_artifacts(row: dict) -> list[str]:
    return [*(row[field] for field in ARTIFACT_FIELDS), *row["documents"].values()]


def resolve_candidate(root: Path, candidate_id: str, table: dict | None = None) -> dict:
    """Return the table row plus its id; refuse a candidate with an absent artifact."""
    table = read_candidates(root)[0] if table is None else table
    if candidate_id not in table["candidates"]:
        raise CandidateError(f"unknown candidate: {candidate_id}")
    row = table["candidates"][candidate_id]
    missing = [rel for rel in candidate_artifacts(row) if not (Path(root) / rel).is_file()]
    if missing:
        raise CandidateError(f"candidate artifact missing: {', '.join(sorted(missing))}")
    return {"id": candidate_id, **copy.deepcopy(row)}


def check_budget(protocol: dict) -> None:
    """Shared seeds/ceiling/timeout rule for every development protocol version."""
    seeds = protocol["seeds"]
    if (
        not isinstance(seeds, list)
        or not seeds
        or any(type(seed) is not int or seed < 0 for seed in seeds)
        or len(set(seeds)) != len(seeds)
    ):
        raise CandidateError("development seeds must be distinct nonnegative integers")
    for name in ("run_ceiling", "episode_ceiling"):
        if type(protocol[name]) is not int or protocol[name] <= 0:
            raise CandidateError("development run and episode budgets must be positive integers")
    if len(seeds) > protocol["episode_ceiling"]:
        raise CandidateError("development seed set exceeds its episode budget")
    timeout = protocol["timeout_s"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise CandidateError("development timeout must be a finite positive number")


def normalize_development(protocol: dict, root: Path | None) -> dict:
    """v2 only: bind the candidate table hash, resolve the row, and return the
    protocol with the derived launch fields attached in a fixed key order (or
    verified when already attached, so a retained plan re-verifies to itself)."""
    if not isinstance(protocol, dict) or protocol.get("schema_version") != DEVELOPMENT_SCHEMA_V2:
        raise CandidateError("development protocol schema is unsupported")
    keys = frozenset(protocol)
    if keys not in (_BASE_SET, _FULL_SET):
        raise CandidateError("development protocol fields are unresolved")
    if protocol["purpose"] != DEVELOPMENT_PURPOSE:
        raise CandidateError("development protocol requests an unsupported run mode")
    if root is None:
        raise CandidateError("candidate table root is unresolved")
    table, digest = read_candidates(root)
    if protocol["candidates_sha256"] != digest:
        raise CandidateError("development protocol binds a stale candidate table")
    check_budget(protocol)
    row = resolve_candidate(root, protocol["candidate"], table)
    derived = {field: row[field] for field in DERIVED_KEYS}
    if keys == _FULL_SET and any(protocol[field] != derived[field] for field in DERIVED_KEYS):
        raise CandidateError("development protocol derived fields drift from the candidate")
    return {**{key: protocol[key] for key in V2_BASE_KEYS}, **derived}


def retained_artifacts(development: dict | None) -> list[str]:
    """Paths a retained development form binds beyond the shared surface: the
    candidate's artifacts and documents for v2, nothing for v1 or None."""
    if not isinstance(development, dict) or "candidate" not in development:
        return []
    return candidate_artifacts(development)


def verify_table_binding(development: dict | None, artifact_hashes: dict) -> None:
    """The retained table digest must equal the hash the plan binds for the file."""
    if isinstance(development, dict) and "candidate" in development:
        if development["candidates_sha256"] != artifact_hashes.get(CANDIDATES_FILE):
            raise CandidateError("candidate table changed during admission")


T1_ORACLE_FIELDS = {
    "tier": "T1",
    "embodiment": "franka",
    "perception": "L1",
    "verifier": "oracle",
    "reset": "teleport",
    "typed_graph": "graphs/expert_t1.yaml",
    "turn_plan": "graphs/turn_plans/expert_t1.json",
    "monolithic_template": "graphs/monolithic_t1.yaml",
    "monolithic_module": "experts/monolithic/expert_t1.py",
    "documents": {
        "treatment_table": "docs/monolithic/treatment-table.json",
        "interface_map": "docs/monolithic/interface-map.json",
        "allowlist": "docs/monolithic/allowlist.json",
        "experts": "docs/monolithic/experts.json",
        "parity_protocol": "docs/monolithic/parity-protocol.json",
    },
}


def launch_fields(development: dict) -> dict:
    """The launch surface a retained development form selects: the fixed T1
    oracle pair for v1, the retained derived fields for v2."""
    if not isinstance(development, dict):
        raise CandidateError("development protocol fields are unresolved")
    if development.get("schema_version") == DEVELOPMENT_SCHEMA_V1:
        return copy.deepcopy(T1_ORACLE_FIELDS)
    if development.get("schema_version") != DEVELOPMENT_SCHEMA_V2 or any(
        key not in development for key in DERIVED_KEYS
    ):
        raise CandidateError("development protocol carries no resolved candidate")
    return {key: copy.deepcopy(development[key]) for key in DERIVED_KEYS}


def participant_python(allowlist: dict) -> tuple[str, ...]:
    """The authored Python surface of the typed arm: the allowlist's .py entries."""
    try:
        editable = allowlist["typed"]["editable"]
    except (KeyError, TypeError) as exc:
        raise CandidateError("typed editable allowlist is invalid") from exc
    files = tuple(sorted(p for p in editable if type(p) is str and p.endswith(".py")))
    if not files or any(not p.startswith("src/aisle/") or "/../" in p for p in files):
        raise CandidateError("typed editable allowlist must name src/aisle Python implementations")
    return files


def modules_for(participant_files) -> frozenset[str]:
    """Import names of the authored Python surface (src/aisle/x/y.py -> aisle.x.y)."""
    return frozenset(p[4:-3].replace("/", ".") for p in participant_files)


def launch_fields_for(development: dict | None) -> dict:
    """`launch_fields`, with the T1 oracle pair when no development form is retained."""
    if development is None:
        return copy.deepcopy(T1_ORACLE_FIELDS)
    return launch_fields(development)


def documents_for(development: dict | None) -> dict:
    """The MON-8 document set a retained development form selects (T1 when None)."""
    if development is None:
        return copy.deepcopy(T1_ORACLE_FIELDS["documents"])
    return launch_fields(development)["documents"]
