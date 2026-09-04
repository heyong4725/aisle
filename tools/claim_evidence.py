#!/usr/bin/env python3
"""Audit and render AISLE's canonical claim-to-evidence catalog (SPEC 410).

The checker is deliberately repository-aware: evidence must be tracked, test
node ids must exist, public claim markers must be registered and unique, and a
generated matrix must match the catalog byte-for-byte. A valid catalog can
still be release-blocked when the independent CLM-12 terminology review is
pending; ``--require-release-ready`` turns that explicit blocker into a failing
release gate without pretending the catalog itself is malformed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = Path("docs/claim-evidence.yaml")
DEFAULT_OUTPUT = Path("docs/generated/claim-evidence.md")

CLAIM_TYPES = {"structural", "empirical", "causal", "reproducibility", "future"}
CLAIM_STATUSES = {
    "supported",
    "weakened",
    "rejected",
    "unrun",
    "undecidable",
    "unattested",
    "hardware_pending",
}
NON_SUPPORTED = CLAIM_STATUSES - {"supported"}
SURFACES = {"readme", "technical_report", "focused_paper"}
SCOPE_FIELDS = {"environment", "task", "perception", "agent_model", "platform"}
SAFETY_CATEGORIES = {
    "graph_topology",
    "kinematic_enforcement",
    "semantic_detection",
    "identity_authorization",
    "observed_outcomes",
}
ARCHITECTURE_ZONES = {
    "mutable_participant",
    "frozen_evaluator",
    "trusted_actuation",
    "hidden_controller",
}
EVIDENCE_KINDS = {
    "source",
    "test",
    "protocol",
    "raw_record",
    "analyzer",
    "reproduction_record",
    "intervention_record",
    "bypass_record",
}
MARKER_RE = re.compile(r"<!--\s*claim:([A-Za-z0-9._/-]+)\s*-->")
CURRENT_STATUS_RE = re.compile(r"<!--\s*current-status:canonical\s*-->")
TEST_NODE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(order=True)
class AuditError:
    requirement: str
    code: str
    location: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "requirement": self.requirement,
            "code": self.code,
            "location": self.location,
            "message": self.message,
        }


@dataclass
class Audit:
    root: Path
    tracked: set[str] | None
    errors: list[AuditError] = field(default_factory=list)
    release_blockers: list[str] = field(default_factory=list)

    def add(self, requirement: str, code: str, location: str, message: str) -> None:
        self.errors.append(AuditError(requirement, code, location, message))

    def path(self, value: Any, requirement: str, location: str) -> Path | None:
        if not isinstance(value, str) or not value.strip():
            self.add(requirement, "PATH_INVALID", location, "path must be a non-empty string")
            return None
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            self.add(
                requirement,
                "PATH_OUTSIDE_ROOT",
                location,
                "path must be repository-relative and may not traverse '..'",
            )
            return None
        resolved = (self.root / candidate).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            self.add(requirement, "PATH_OUTSIDE_ROOT", location, "path resolves outside root")
            return None
        return resolved

    def tracked_file(self, value: Any, requirement: str, location: str) -> Path | None:
        path = self.path(value, requirement, location)
        if path is None:
            return None
        rel = path.relative_to(self.root).as_posix()
        if not path.is_file():
            self.add(
                requirement, "EVIDENCE_MISSING", location, f"tracked file does not exist: {rel}"
            )
            return None
        if self.tracked is not None and rel not in self.tracked:
            self.add(requirement, "EVIDENCE_UNTRACKED", location, f"file is not tracked: {rel}")
            return None
        return path


def _tracked_paths(root: Path) -> set[str] | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return {entry for entry in proc.stdout.decode("utf-8").split("\0") if entry}


def _is_na(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("value") == "not_applicable"
        and isinstance(value.get("rationale"), str)
        and bool(value["rationale"].strip())
        and set(value) == {"value", "rationale"}
    )


def _is_present(value: Any) -> bool:
    if _is_na(value):
        return True
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(_is_present(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and all(
            isinstance(key, str) and _is_present(item) for key, item in value.items()
        )
    return value is not None


def _is_real_value(value: Any) -> bool:
    return _is_present(value) and not _is_na(value)


def _cell(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(", ", ": "))
    return text.replace("|", "\\|").replace("\n", " ").strip() or "—"


def _load_catalog(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("catalog root must be a mapping")
    return loaded


def _validate_row_schema(audit: Audit, row: Any, index: int) -> dict[str, Any] | None:
    location = f"claims[{index}]"
    if not isinstance(row, dict):
        audit.add("CLM-1", "ROW_INVALID", location, "claim row must be a mapping")
        return None
    required = {
        "id",
        "claim",
        "type",
        "status",
        "scope",
        "experimental_unit",
        "sample",
        "uncertainty",
        "attestation",
        "evidence",
        "counterevidence",
        "limitations",
        "allowed_wording",
        "headlines",
    }
    for name in sorted(required):
        if name not in row or (name != "headlines" and not _is_present(row.get(name))):
            audit.add(
                "CLM-1",
                "FIELD_MISSING",
                f"{location}.{name}",
                "field must be non-empty or an explicit not_applicable mapping with rationale",
            )

    claim_id = row.get("id")
    if not isinstance(claim_id, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]*", claim_id) is None:
        audit.add(
            "CLM-1",
            "CLAIM_ID_INVALID",
            f"{location}.id",
            "id must be stable lower-case kebab-case",
        )

    scope = row.get("scope")
    if not isinstance(scope, dict) or set(scope) != SCOPE_FIELDS:
        audit.add(
            "CLM-1",
            "SCOPE_INVALID",
            f"{location}.scope",
            f"scope must contain exactly {sorted(SCOPE_FIELDS)}",
        )
    elif not all(_is_present(value) for value in scope.values()):
        audit.add("CLM-1", "SCOPE_EMPTY", f"{location}.scope", "scope values may not be empty")

    attestation = row.get("attestation")
    if (
        not isinstance(attestation, dict)
        or not {
            "status",
            "rationale",
        }.issubset(attestation)
        or not all(
            isinstance(attestation.get(name), str) and attestation[name].strip()
            for name in ("status", "rationale")
        )
    ):
        audit.add(
            "CLM-1",
            "ATTESTATION_INVALID",
            f"{location}.attestation",
            "attestation requires non-empty status and rationale",
        )

    evidence = row.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        audit.add(
            "CLM-1", "EVIDENCE_EMPTY", f"{location}.evidence", "evidence must be a non-empty list"
        )
    counterevidence = row.get("counterevidence")
    if not isinstance(counterevidence, list) or not counterevidence:
        audit.add(
            "CLM-1",
            "COUNTEREVIDENCE_EMPTY",
            f"{location}.counterevidence",
            "counterevidence must be a non-empty list",
        )
    limitations = row.get("limitations")
    if (
        not isinstance(limitations, list)
        or not limitations
        or not all(isinstance(item, str) and item.strip() for item in limitations)
    ):
        audit.add(
            "CLM-1",
            "LIMITATIONS_INVALID",
            f"{location}.limitations",
            "limitations must be a non-empty list of statements",
        )
    allowed = row.get("allowed_wording")
    if (
        not isinstance(allowed, dict)
        or set(allowed) != SURFACES
        or not all(isinstance(value, str) and value.strip() for value in allowed.values())
    ):
        audit.add(
            "CLM-1",
            "WORDING_INVALID",
            f"{location}.allowed_wording",
            f"allowed_wording must contain non-empty {sorted(SURFACES)} strings",
        )
    headlines = row.get("headlines")
    if not isinstance(headlines, list):
        audit.add("CLM-1", "HEADLINES_INVALID", f"{location}.headlines", "headlines must be a list")
    return row


def _validate_types_and_scope(audit: Audit, row: dict[str, Any], index: int) -> None:
    location = f"claims[{index}]"
    claim_type = row.get("type")
    status = row.get("status")
    if claim_type not in CLAIM_TYPES:
        audit.add(
            "CLM-2", "TYPE_UNKNOWN", f"{location}.type", f"unknown claim type: {claim_type!r}"
        )
    if status not in CLAIM_STATUSES:
        audit.add(
            "CLM-2", "STATUS_UNKNOWN", f"{location}.status", f"unknown claim status: {status!r}"
        )
    if claim_type == "future" and status == "supported":
        audit.add("CLM-2", "FUTURE_SUPPORTED", location, "a future claim cannot be supported")

    scope = row.get("scope")
    if not isinstance(scope, dict) or status != "supported":
        return
    if scope.get("environment") == "hardware":
        evidence = row.get("evidence", [])
        required_hardware_kinds = {
            "structural": {"source", "test"},
            "empirical": {"raw_record"},
            "causal": {"raw_record"},
            "reproducibility": {"reproduction_record"},
        }.get(claim_type, {"raw_record"})
        hardware_kinds = {
            item.get("kind")
            for item in evidence
            if isinstance(item, dict) and item.get("scope") == "hardware"
        }
        if not required_hardware_kinds.issubset(hardware_kinds):
            audit.add(
                "CLM-2",
                "SIMULATION_AS_HARDWARE",
                f"{location}.scope.environment",
                "supported hardware scope requires hardware-scoped evidence kinds "
                f"{sorted(required_hardware_kinds)}",
            )


def _validate_evidence_kind(audit: Audit, item: dict[str, Any], location: str, path: Path) -> None:
    kind = item.get("kind")
    suffix = path.suffix.lower()
    rel = path.relative_to(audit.root).as_posix()
    if kind == "raw_record" and suffix not in {".json", ".jsonl", ".csv", ".tsv", ".parquet"}:
        audit.add(
            "CLM-4", "EVIDENCE_KIND_MISMATCH", location, f"raw record has invalid path: {rel}"
        )
    elif kind == "analyzer" and suffix != ".py":
        audit.add("CLM-4", "EVIDENCE_KIND_MISMATCH", location, f"analyzer must be Python: {rel}")
    elif kind == "test" and (not rel.startswith("tests/") or not path.name.startswith("test_")):
        audit.add(
            "CLM-4", "EVIDENCE_KIND_MISMATCH", location, f"test kind is not a test module: {rel}"
        )
    elif kind == "protocol" and suffix not in {".md", ".json", ".yaml", ".yml", ".toml"}:
        audit.add("CLM-4", "EVIDENCE_KIND_MISMATCH", location, f"protocol has invalid path: {rel}")
    elif kind in {"reproduction_record", "intervention_record", "bypass_record"} and suffix not in {
        ".json",
        ".jsonl",
        ".md",
        ".csv",
        ".parquet",
    }:
        audit.add("CLM-4", "EVIDENCE_KIND_MISMATCH", location, f"record has invalid path: {rel}")

    if kind == "test":
        node = item.get("node")
        expected_prefix = f"{rel}::"
        if not isinstance(node, str) or not node.startswith(expected_prefix):
            audit.add(
                "CLM-4",
                "TEST_NODE_INVALID",
                f"{location}.node",
                f"test node must start with {expected_prefix}",
            )
            return
        function = node.removeprefix(expected_prefix)
        if "::" in function or TEST_NODE_RE.fullmatch(function) is None:
            audit.add(
                "CLM-4",
                "TEST_NODE_INVALID",
                f"{location}.node",
                "only module-level test function node ids are accepted",
            )
            return
        source = path.read_text(encoding="utf-8")
        if re.search(rf"^def\s+{re.escape(function)}\s*\(", source, flags=re.MULTILINE) is None:
            audit.add(
                "CLM-4",
                "TEST_NODE_MISSING",
                f"{location}.node",
                f"test function does not exist: {node}",
            )


def _validate_evidence(audit: Audit, row: dict[str, Any], index: int) -> set[str]:
    kinds: set[str] = set()
    evidence = row.get("evidence")
    if not isinstance(evidence, list):
        return kinds
    for evidence_index, item in enumerate(evidence):
        location = f"claims[{index}].evidence[{evidence_index}]"
        if _is_na(item):
            continue
        if not isinstance(item, dict):
            audit.add("CLM-4", "EVIDENCE_INVALID", location, "evidence entry must be a mapping")
            continue
        kind = item.get("kind")
        if kind not in EVIDENCE_KINDS:
            audit.add(
                "CLM-4", "EVIDENCE_KIND_UNKNOWN", f"{location}.kind", f"unknown kind: {kind!r}"
            )
            continue
        kinds.add(kind)
        if item.get("scope") not in {"simulation", "hardware", "agnostic"}:
            audit.add(
                "CLM-4",
                "EVIDENCE_SCOPE_INVALID",
                f"{location}.scope",
                "evidence scope must be simulation, hardware, or agnostic",
            )
        if not isinstance(item.get("rationale"), str) or not item["rationale"].strip():
            audit.add(
                "CLM-4",
                "EVIDENCE_RATIONALE_MISSING",
                f"{location}.rationale",
                "evidence requires a rationale",
            )
        path = audit.tracked_file(item.get("path"), "CLM-4", f"{location}.path")
        if path is not None:
            _validate_evidence_kind(audit, item, location, path)
    return kinds


def _validate_support(audit: Audit, row: dict[str, Any], index: int, kinds: set[str]) -> None:
    if row.get("status") != "supported":
        return
    location = f"claims[{index}]"
    claim_type = row.get("type")
    required: set[str] = set()
    if claim_type == "structural":
        required = {"source", "test"}
    elif claim_type == "empirical":
        required = {"raw_record", "analyzer"}
    elif claim_type == "causal":
        required = {"protocol", "raw_record", "analyzer"}
    elif claim_type == "reproducibility":
        required = {"reproduction_record"}
    missing = required - kinds
    if missing:
        audit.add(
            "CLM-3",
            "SUPPORT_EVIDENCE_INSUFFICIENT",
            f"{location}.evidence",
            f"supported {claim_type} claim lacks evidence kinds: {sorted(missing)}",
        )
    if claim_type == "causal":
        if not _is_real_value(row.get("registered_control")):
            audit.add(
                "CLM-3",
                "CONTROL_MISSING",
                f"{location}.registered_control",
                "supported causal claim requires a registered control",
            )
        unit = row.get("experimental_unit")
        if not isinstance(unit, str) or "session" not in unit:
            audit.add(
                "CLM-3",
                "EXPERIMENTAL_UNIT_INVALID",
                f"{location}.experimental_unit",
                "supported causal claim requires a session experimental unit",
            )
        if not _is_real_value(row.get("uncertainty")):
            audit.add(
                "CLM-3",
                "UNCERTAINTY_MISSING",
                f"{location}.uncertainty",
                "supported causal claim requires session-level uncertainty",
            )


def _validate_safety(audit: Audit, rows: list[dict[str, Any]]) -> None:
    by_category: dict[str, list[int]] = {category: [] for category in SAFETY_CATEGORIES}
    for index, row in enumerate(rows):
        category = row.get("safety_category")
        if category is None:
            continue
        if category not in SAFETY_CATEGORIES:
            audit.add(
                "CLM-5",
                "SAFETY_CATEGORY_UNKNOWN",
                f"claims[{index}].safety_category",
                f"unknown safety category: {category!r}",
            )
            continue
        by_category[category].append(index)
        wording_parts = [
            str(row.get("claim", "")).lower(),
            *(str(value).lower() for value in (row.get("allowed_wording") or {}).values()),
        ]
        semantic_prevention = any(
            re.search(
                r"\b(verifier|guard)\b.*\bprevent\w*\b.*\b(wrong[- ]object|semantic|identity)",
                wording,
            )
            or re.search(
                r"\bprevent\w*\b.*\b(wrong[- ]object|semantic|identity)\b.*\b(verifier|guard)\b",
                wording,
            )
            for wording in wording_parts
        )
        if semantic_prevention and "intervention_record" not in {
            item.get("kind") for item in row.get("evidence", []) if isinstance(item, dict)
        }:
            audit.add(
                "CLM-5",
                "SEMANTIC_PREVENTION_OVERCLAIM",
                f"claims[{index}].allowed_wording",
                "semantic prevention attributed to verifier/guard requires intervention evidence",
            )
        if any("unbypassable" in wording for wording in wording_parts) and "bypass_record" not in {
            item.get("kind") for item in row.get("evidence", []) if isinstance(item, dict)
        }:
            audit.add(
                "CLM-5",
                "UNBYPASSABLE_OVERCLAIM",
                f"claims[{index}].allowed_wording",
                "unbypassable wording requires scoped bypass evidence from issue #350",
            )
    for category, indices in sorted(by_category.items()):
        if len(indices) != 1:
            audit.add(
                "CLM-5",
                "SAFETY_ROW_CARDINALITY",
                "claims",
                f"safety category {category} must have exactly one row; found {len(indices)}",
            )


def _read_markdown(
    audit: Audit, path_value: Any, requirement: str, location: str
) -> tuple[Path, str] | None:
    path = audit.tracked_file(path_value, requirement, location)
    if path is None:
        return None
    try:
        return path, path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        audit.add(requirement, "UTF8_REQUIRED", location, str(exc))
        return None


def _markdown_anchors(text: str) -> set[str]:
    """Return deterministic GitHub-style anchors for ordinary headings."""
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for match in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, flags=re.MULTILINE):
        heading = re.sub(r"<!--.*?-->", "", match.group(1))
        heading = re.sub(r"[`*_~]", "", heading).strip().lower()
        slug = re.sub(r"[^\w\- ]", "", heading, flags=re.UNICODE)
        slug = re.sub(r"[\s-]+", "-", slug).strip("-")
        if not slug:
            continue
        suffix = counts.get(slug, 0)
        counts[slug] = suffix + 1
        anchors.add(f"#{slug}" if suffix == 0 else f"#{slug}-{suffix}")
    return anchors


def _validate_markers(
    audit: Audit,
    catalog: dict[str, Any],
    rows: list[dict[str, Any]],
) -> None:
    declared: dict[str, tuple[str, str | None]] = {}
    for index, row in enumerate(rows):
        headlines = row.get("headlines")
        if not isinstance(headlines, list):
            continue
        for headline_index, headline in enumerate(headlines):
            location = f"claims[{index}].headlines[{headline_index}]"
            if not isinstance(headline, dict) or set(headline) != {"path", "marker"}:
                audit.add(
                    "CLM-6",
                    "HEADLINE_INVALID",
                    location,
                    "headline requires exactly path and marker",
                )
                continue
            marker = headline.get("marker")
            path = headline.get("path")
            if not isinstance(marker, str) or not marker or not isinstance(path, str):
                audit.add("CLM-6", "HEADLINE_INVALID", location, "path and marker must be strings")
                continue
            if marker in declared:
                audit.add("CLM-6", "MARKER_DECLARED_TWICE", location, f"duplicate marker: {marker}")
            declared[marker] = (path, row.get("status"))

    architecture = catalog.get("architecture")
    if isinstance(architecture, dict):
        marker, path = architecture.get("marker"), architecture.get("path")
        if isinstance(marker, str) and isinstance(path, str):
            declared[marker] = (path, None)
    publications = catalog.get("publications")
    if isinstance(publications, dict):
        for publication in publications.values():
            if isinstance(publication, dict):
                marker, path = publication.get("marker"), publication.get("path")
                if isinstance(marker, str) and isinstance(path, str):
                    if marker in declared:
                        audit.add("CLM-6", "MARKER_DECLARED_TWICE", "publications", marker)
                    declared[marker] = (path, None)

    occurrences: dict[str, list[tuple[str, int, str]]] = {}
    markdown_paths: list[Path]
    if audit.tracked is None:
        markdown_paths = sorted(audit.root.rglob("*.md"))
    else:
        markdown_paths = sorted(
            audit.root / rel
            for rel in audit.tracked
            if rel.endswith(".md") and (audit.root / rel).is_file()
        )
    for path in markdown_paths:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(audit.root).as_posix()
        for match in MARKER_RE.finditer(text):
            occurrences.setdefault(match.group(1), []).append((rel, match.start(), text))

    for marker, found in sorted(occurrences.items()):
        if marker not in declared:
            audit.add("CLM-6", "MARKER_UNKNOWN", found[0][0], f"unknown claim marker: {marker}")
        if len(found) != 1:
            audit.add(
                "CLM-6",
                "MARKER_DUPLICATE",
                found[0][0],
                f"marker {marker} occurs {len(found)} times",
            )
    for marker, (expected_path, status) in sorted(declared.items()):
        found = occurrences.get(marker, [])
        if not found:
            audit.add(
                "CLM-6", "MARKER_MISSING", expected_path, f"declared marker is absent: {marker}"
            )
            continue
        actual_path, offset, text = found[0]
        if actual_path != expected_path:
            audit.add(
                "CLM-6",
                "MARKER_WRONG_PATH",
                actual_path,
                f"marker {marker} declared at {expected_path}",
            )
        if status in NON_SUPPORTED:
            qualifier = str(status).replace("_", r"[ _]")
            visible = text[offset : offset + 700]
            if re.search(qualifier, visible, flags=re.IGNORECASE) is None:
                audit.add(
                    "CLM-6",
                    "HEADLINE_UNQUALIFIED",
                    actual_path,
                    f"non-supported marker {marker} lacks visible {status} qualification",
                )


def _validate_status_source(audit: Audit, catalog: dict[str, Any]) -> None:
    canonical = catalog.get("canonical_status")
    overview = catalog.get("overview_documents")
    if not isinstance(canonical, dict) or not {
        "path",
        "marker",
        "anchor",
    }.issubset(canonical):
        audit.add(
            "CLM-8",
            "CANONICAL_STATUS_INVALID",
            "canonical_status",
            "canonical status is incomplete",
        )
        return
    if not isinstance(overview, list) or not overview:
        audit.add(
            "CLM-8", "OVERVIEW_LIST_INVALID", "overview_documents", "overview list is required"
        )
        return
    canonical_entries = [
        entry for entry in overview if isinstance(entry, dict) and entry.get("role") == "canonical"
    ]
    if len(canonical_entries) != 1 or canonical_entries[0].get("path") != canonical.get("path"):
        audit.add(
            "CLM-8",
            "CANONICAL_STATUS_CARDINALITY",
            "overview_documents",
            "exactly one canonical overview must match canonical_status.path",
        )

    status_markers = 0
    for index, entry in enumerate(overview):
        location = f"overview_documents[{index}]"
        if not isinstance(entry, dict):
            audit.add("CLM-8", "OVERVIEW_INVALID", location, "overview entry must be a mapping")
            continue
        loaded = _read_markdown(audit, entry.get("path"), "CLM-8", f"{location}.path")
        if loaded is None:
            continue
        _, text = loaded
        status_markers += len(CURRENT_STATUS_RE.findall(text))
        role = entry.get("role")
        if role == "canonical":
            marker = canonical.get("marker")
            if marker != "current-status:canonical" or len(CURRENT_STATUS_RE.findall(text)) != 1:
                audit.add(
                    "CLM-8",
                    "CANONICAL_MARKER_INVALID",
                    location,
                    "canonical README requires exactly one current-status marker",
                )
            anchor = canonical.get("anchor")
            if not isinstance(anchor, str) or anchor not in _markdown_anchors(text):
                audit.add(
                    "CLM-8",
                    "CANONICAL_ANCHOR_MISSING",
                    location,
                    f"canonical heading does not define anchor {anchor!r}",
                )
        elif role == "snapshot":
            date = entry.get("snapshot_date")
            link = entry.get("canonical_link")
            if not isinstance(date, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) is None:
                audit.add(
                    "CLM-8", "SNAPSHOT_DATE_INVALID", location, "snapshot date must be YYYY-MM-DD"
                )
            if not isinstance(link, str) or not link:
                audit.add(
                    "CLM-8",
                    "SNAPSHOT_LINK_MISSING",
                    location,
                    "snapshot must link to README status",
                )
            elif isinstance(date, str):
                anchor = canonical.get("anchor")
                if not isinstance(anchor, str) or not link.endswith(anchor):
                    audit.add(
                        "CLM-8",
                        "SNAPSHOT_LINK_TARGET_INVALID",
                        location,
                        f"snapshot link must target canonical anchor {anchor!r}",
                    )
                marker = f"<!-- status-snapshot:{date} canonical:{link} -->"
                if marker not in text or link not in text:
                    audit.add(
                        "CLM-8",
                        "SNAPSHOT_MARKER_INVALID",
                        location,
                        "snapshot marker/date/canonical link must agree",
                    )
        else:
            audit.add("CLM-8", "OVERVIEW_ROLE_INVALID", location, f"unknown role: {role!r}")
    if status_markers != 1:
        audit.add(
            "CLM-8",
            "CURRENT_STATUS_DUPLICATE",
            "overview_documents",
            f"expected one canonical current-status marker; found {status_markers}",
        )


def _validate_architecture(audit: Audit, catalog: dict[str, Any]) -> None:
    architecture = catalog.get("architecture")
    if not isinstance(architecture, dict):
        audit.add(
            "CLM-10", "ARCHITECTURE_INVALID", "architecture", "architecture mapping is required"
        )
        return
    if architecture.get("experimental_unit") != "coding_agent_session":
        audit.add(
            "CLM-10",
            "ARCHITECTURE_UNIT_INVALID",
            "architecture.experimental_unit",
            "benchmark experimental unit must be coding_agent_session",
        )
    zones = architecture.get("zones")
    if (
        not isinstance(zones, dict)
        or set(zones) != ARCHITECTURE_ZONES
        or not all(isinstance(value, str) and value.strip() for value in zones.values())
    ):
        audit.add(
            "CLM-10",
            "ARCHITECTURE_ZONES_INVALID",
            "architecture.zones",
            f"architecture must define exactly {sorted(ARCHITECTURE_ZONES)}",
        )
    for name in ("inaccessible", "forbidden"):
        value = architecture.get(name)
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            audit.add(
                "CLM-10",
                "ACCESS_BOUNDARY_INVALID",
                f"architecture.{name}",
                f"{name} must be a non-empty explicit list",
            )
    if architecture.get("threat_model_issue") != "#350":
        audit.add(
            "CLM-10",
            "THREAT_MODEL_NOT_DEFERRED",
            "architecture.threat_model_issue",
            "wider bypass claims must defer to issue #350",
        )
    loaded = _read_markdown(audit, architecture.get("path"), "CLM-10", "architecture.path")
    if loaded is not None:
        _, text = loaded
        required_phrases = (
            "coding-agent session",
            "mutable participant",
            "frozen evaluator",
            "trusted actuation",
            "hidden controller",
            "inaccessible",
            "forbidden",
            "#350",
        )
        lowered = text.lower()
        missing = [phrase for phrase in required_phrases if phrase.lower() not in lowered]
        if missing:
            audit.add(
                "CLM-10",
                "ARCHITECTURE_NARRATIVE_INCOMPLETE",
                architecture.get("path", "architecture.path"),
                f"missing phrases: {missing}",
            )


def _validate_publications(audit: Audit, catalog: dict[str, Any]) -> None:
    publications = catalog.get("publications")
    if not isinstance(publications, dict) or set(publications) != {
        "technical_report",
        "focused_paper",
    }:
        audit.add(
            "CLM-11",
            "PUBLICATION_BOUNDARY_INVALID",
            "publications",
            "technical_report and focused_paper declarations are required",
        )
        return
    for name, entry in publications.items():
        location = f"publications.{name}"
        if not isinstance(entry, dict):
            audit.add("CLM-11", "PUBLICATION_INVALID", location, "publication must be a mapping")
            continue
        for field_name in ("purpose", "marker"):
            if not isinstance(entry.get(field_name), str) or not entry[field_name].strip():
                audit.add(
                    "CLM-11",
                    "PUBLICATION_FIELD_MISSING",
                    f"{location}.{field_name}",
                    "field is required",
                )
        for field_name in ("in_scope", "out_of_scope"):
            value = entry.get(field_name)
            if (
                not isinstance(value, list)
                or not value
                or not all(isinstance(item, str) and item.strip() for item in value)
            ):
                audit.add(
                    "CLM-11",
                    "PUBLICATION_SCOPE_INVALID",
                    f"{location}.{field_name}",
                    "scope must be a non-empty list",
                )
        _read_markdown(audit, entry.get("path"), "CLM-11", f"{location}.path")

    report_scope = publications.get("technical_report", {}).get("in_scope", [])
    paper_scope = publications.get("focused_paper", {}).get("in_scope", [])
    if isinstance(report_scope, list) and isinstance(paper_scope, list):
        overlap = set(report_scope) & set(paper_scope)
        if overlap:
            audit.add(
                "CLM-11",
                "PUBLICATION_SCOPE_OVERLAP",
                "publications",
                f"report and focused paper in-scope purposes overlap: {sorted(overlap)}",
            )


def _validate_terminology_review(audit: Audit, catalog: dict[str, Any]) -> None:
    review = catalog.get("terminology_review")
    if not isinstance(review, dict) or review.get("required_before") != "public_benchmark_release":
        audit.add(
            "CLM-12",
            "REVIEW_GATE_INVALID",
            "terminology_review",
            "terminology review must gate public_benchmark_release",
        )
        audit.release_blockers.append("CLM-12")
        return
    status = review.get("status")
    if status == "pending":
        if not _is_na(review.get("review_record")):
            audit.add(
                "CLM-12",
                "PENDING_REVIEW_RECORD_INVALID",
                "terminology_review.review_record",
                "pending review record must be explicit not_applicable with rationale",
            )
        audit.release_blockers.append("CLM-12")
        return
    if status != "complete":
        audit.add(
            "CLM-12",
            "REVIEW_STATUS_INVALID",
            "terminology_review.status",
            f"unknown status: {status!r}",
        )
        audit.release_blockers.append("CLM-12")
        return
    path = audit.tracked_file(
        review.get("review_record"), "CLM-12", "terminology_review.review_record"
    )
    if path is None:
        audit.release_blockers.append("CLM-12")
        return
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        audit.add("CLM-12", "REVIEW_RECORD_INVALID", str(path), str(exc))
        audit.release_blockers.append("CLM-12")
        return
    valid = isinstance(record, dict)
    valid = valid and isinstance(record.get("reviewer"), str) and bool(record["reviewer"].strip())
    valid = valid and record.get("independent_from_authorship") is True
    valid = valid and isinstance(record.get("signed_at"), str) and bool(record["signed_at"].strip())
    valid = valid and isinstance(record.get("signature"), str) and bool(record["signature"].strip())
    findings = record.get("findings") if isinstance(record, dict) else None
    valid = valid and isinstance(findings, list)
    valid = valid and all(
        isinstance(finding, dict)
        and isinstance(finding.get("id"), str)
        and bool(finding["id"].strip())
        and isinstance(finding.get("disposition"), str)
        and bool(finding["disposition"].strip())
        for finding in (findings or [])
    )
    if not valid:
        audit.add(
            "CLM-12",
            "REVIEW_RECORD_INCOMPLETE",
            path.relative_to(audit.root).as_posix(),
            "review requires named reviewer, independence=true, signature, signed_at, "
            "and every finding disposition",
        )
        audit.release_blockers.append("CLM-12")


def audit_catalog(root: Path, catalog: dict[str, Any]) -> Audit:
    audit = Audit(root=root, tracked=_tracked_paths(root))
    if catalog.get("schema_version") != 1:
        audit.add(
            "CLM-1", "SCHEMA_VERSION_INVALID", "schema_version", "schema_version must equal 1"
        )
    claims = catalog.get("claims")
    if not isinstance(claims, list) or not claims:
        audit.add("CLM-1", "CLAIMS_INVALID", "claims", "claims must be a non-empty list")
        claims = []

    rows: list[dict[str, Any]] = []
    ids: dict[str, int] = {}
    for index, raw_row in enumerate(claims):
        row = _validate_row_schema(audit, raw_row, index)
        if row is None:
            continue
        rows.append(row)
        claim_id = row.get("id")
        if isinstance(claim_id, str):
            if claim_id in ids:
                audit.add(
                    "CLM-1",
                    "CLAIM_ID_DUPLICATE",
                    f"claims[{index}].id",
                    f"duplicate id first declared at claims[{ids[claim_id]}]",
                )
            ids[claim_id] = index
        _validate_types_and_scope(audit, row, index)
        kinds = _validate_evidence(audit, row, index)
        _validate_support(audit, row, index, kinds)

    _validate_safety(audit, rows)
    _validate_markers(audit, catalog, rows)
    _validate_status_source(audit, catalog)
    _validate_architecture(audit, catalog)
    _validate_publications(audit, catalog)
    _validate_terminology_review(audit, catalog)
    audit.errors.sort()
    audit.release_blockers = sorted(set(audit.release_blockers))
    return audit


def render_catalog(catalog: dict[str, Any]) -> str:
    architecture = catalog["architecture"]
    publications = catalog["publications"]
    review = catalog["terminology_review"]
    rows = sorted(catalog["claims"], key=lambda row: row["id"])
    lines = [
        "# AISLE claim-to-evidence matrix",
        "",
        "> Generated from `docs/claim-evidence.yaml` by `tools/claim_evidence.py`; do not edit.",
        "",
        "## Release and architecture status",
        "",
        f"- Terminology review: `{_cell(review['status'])}` "
        f"(required before `{_cell(review['required_before'])}`)",
        f"- Experimental unit: `{_cell(architecture['experimental_unit'])}`",
        f"- Threat-model dependency: `{_cell(architecture['threat_model_issue'])}`",
        "- Trust zones: " + ", ".join(f"`{name}`" for name in sorted(architecture["zones"])),
        "- Inaccessible: " + _cell(architecture["inaccessible"]),
        "- Forbidden: " + _cell(architecture["forbidden"]),
        "",
        "## Publication purpose boundary",
        "",
        "| Surface | Purpose | In scope | Out of scope |",
        "|---|---|---|---|",
    ]
    for name in ("technical_report", "focused_paper"):
        publication = publications[name]
        lines.append(
            f"| `{name}` | {_cell(publication['purpose'])} | {_cell(publication['in_scope'])} | "
            f"{_cell(publication['out_of_scope'])} |"
        )
    lines.extend(
        [
            "",
            "## Claim index",
            "",
            "| Claim | Type | Status | Environment | Attestation |",
            "|---|---|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| [`{row['id']}`](#{row['id']}) | `{row['type']}` | `{row['status']}` | "
            f"{_cell(row['scope']['environment'])} | {_cell(row['attestation'])} |"
        )
    for row in rows:
        lines.extend(
            [
                "",
                f"## {row['id']}",
                "",
                row["claim"],
                "",
                f"- Type / status: `{row['type']}` / `{row['status']}`",
                f"- Scope: {_cell(row['scope'])}",
                f"- Experimental unit and sample: {_cell(row['experimental_unit'])}; "
                f"{_cell(row['sample'])}",
                f"- Uncertainty: {_cell(row['uncertainty'])}",
                f"- Attestation: {_cell(row['attestation'])}",
                f"- Evidence: {_cell(row['evidence'])}",
                f"- Counterevidence: {_cell(row['counterevidence'])}",
                f"- Limitations: {_cell(row['limitations'])}",
                f"- Allowed wording: {_cell(row['allowed_wording'])}",
                f"- Headline markers: {_cell(row['headlines'])}",
            ]
        )
        if "safety_category" in row:
            lines.append(f"- Safety category: `{row['safety_category']}`")
    return "\n".join(lines) + "\n"


def _resolve_under(root: Path, path: Path) -> Path:
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path resolves outside --root: {path}") from exc
    return resolved


def _result(
    *,
    ok: bool,
    reason: str,
    catalog_path: Path,
    output_path: Path,
    digest: str | None,
    audit: Audit,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "reason": reason,
        "catalog": catalog_path.relative_to(audit.root).as_posix(),
        "output": output_path.relative_to(audit.root).as_posix(),
        "sha256": digest,
        "claims": None,
        "release_ready": ok and not audit.release_blockers and not audit.errors,
        "release_blockers": audit.release_blockers,
        "errors": [error.as_dict() for error in audit.errors],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--require-release-ready", action="store_true")
    args = parser.parse_args(argv)

    try:
        root = args.root.resolve()
        catalog_path = _resolve_under(root, args.catalog)
        output_path = _resolve_under(root, args.output)
        catalog = _load_catalog(catalog_path)
        audit = audit_catalog(root, catalog)
        rendered = render_catalog(catalog) if not audit.errors else None
        digest = (
            hashlib.sha256(rendered.encode("utf-8")).hexdigest() if rendered is not None else None
        )

        reason = "invalid" if audit.errors else "current"
        ok = not audit.errors
        if rendered is not None and args.write:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            previous = output_path.read_text(encoding="utf-8") if output_path.exists() else None
            output_path.write_text(rendered, encoding="utf-8")
            reason = "current" if previous == rendered else "written"
        elif rendered is not None:
            if not output_path.exists():
                ok = False
                reason = "missing"
            elif output_path.read_bytes() != rendered.encode("utf-8"):
                ok = False
                reason = "stale"
        if args.require_release_ready and audit.release_blockers and ok:
            ok = False
            reason = "release_blocked"
        result = _result(
            ok=ok,
            reason=reason,
            catalog_path=catalog_path,
            output_path=output_path,
            digest=digest,
            audit=audit,
        )
        result["claims"] = len(catalog.get("claims", []))
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        root = args.root.resolve()
        audit = Audit(root=root, tracked=None)
        audit.add("CLM-7", "CATALOG_ERROR", str(args.catalog), str(exc))
        result = {
            "ok": False,
            "reason": "invalid",
            "catalog": str(args.catalog),
            "output": str(args.output),
            "sha256": None,
            "claims": None,
            "release_ready": False,
            "release_blockers": [],
            "errors": [error.as_dict() for error in audit.errors],
        }

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
