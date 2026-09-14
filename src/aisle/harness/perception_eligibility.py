"""BND-7 eligibility of one candidate from a retained perception-audit report.

The record is derived from the report's per-stratum measurements against the
frozen envelope (never from the report's own pass flags), after the report's
content hash is recomputed from its raw predictions, its envelope hash is
matched, and the audited run's graph is matched to the candidate (BND-16).
Nothing is recomputed or relaxed (BND-10). Kept outside the hashed auditor
and harness CLI so existing registrations binding those files do not drift.
"""

from __future__ import annotations

from typing import Any

from aisle.harness.non_oracle import NonOracleError, validate_perception_eligibility
from aisle.harness.perception_audit import REPORT_SCHEMA, PerceptionAuditError, content_hash

ELIGIBILITY_SCHEMA = "aisle.perception-eligibility.v1"
RECORD_SPLIT_KEYS = ("raw_predictions_file", "raw_predictions_note")
CELL_KEYS = {"accuracy", "accuracy_lower_bound", "refusal_rate", "n"}


def verify_report_hash(report: dict, raw_predictions: list) -> None:
    """The retained hash must equal the audit's hash over the full report."""
    full = {k: v for k, v in report.items() if k != "report_hash" and k not in RECORD_SPLIT_KEYS}
    full["raw_predictions"] = raw_predictions
    if content_hash(full) != report.get("report_hash"):
        raise PerceptionAuditError("report hash does not match its raw predictions")


def _cells(report: Any, candidate: str) -> dict:
    strata = report.get("strata") if isinstance(report, dict) else None
    if not isinstance(strata, dict) or not strata or report.get("schema_version") != REPORT_SCHEMA:
        raise PerceptionAuditError("perception report is not an audit report", [candidate])
    for axis, cells in strata.items():
        if not isinstance(cells, dict) or not cells:
            raise PerceptionAuditError(f"perception report has an empty stratum: {axis}")
        for label, cell in cells.items():
            if (
                not isinstance(cell, dict)
                or not CELL_KEYS <= set(cell)
                or any(type(cell[k]) not in (int, float) for k in CELL_KEYS)
            ):
                raise PerceptionAuditError(f"perception stratum cell is malformed: {axis}={label}")
    failures = report.get("failures")
    if not isinstance(failures, list) or any(type(f) is not str for f in failures):
        raise PerceptionAuditError("perception report failures are malformed")
    latency = report.get("latency_s")
    if not isinstance(latency, dict) or type(latency.get("within_ceiling")) is not bool:
        raise PerceptionAuditError("perception report latency summary is malformed")
    return strata


def eligibility(
    report: dict,
    *,
    envelope: dict,
    manifest: dict,
    raw_predictions: list,
    candidate: str,
    role: str,
    graph: str,
) -> dict:
    strata = _cells(report, candidate)
    if content_hash(envelope) != report.get("envelope_hash"):
        raise PerceptionAuditError("envelope differs from the one the report was audited under")
    verify_report_hash(report, raw_predictions)
    if not isinstance(manifest, dict) or manifest.get("run_id") != report.get("run_id"):
        raise PerceptionAuditError("run manifest does not belong to the audited run")
    if manifest.get("graph") != graph:
        raise PerceptionAuditError("candidate graph differs from the audited run", [graph])
    floor, limit = envelope["accuracy_floor"], envelope["refusal_availability_limit"]
    within_ceiling = report["latency_s"]["within_ceiling"]
    latency_ms = (
        None if report["latency_s"].get("max") is None else report["latency_s"]["max"] * 1000
    )
    rows = [
        {
            "name": f"{axis}={label}",
            "accuracy": cell["accuracy"],
            "max_error": {
                "reported": False,
                "reason": "localization failures are counted in accuracy via the taxonomy",
            },
            "latency_ms": latency_ms,
            "refusal_rate": cell["refusal_rate"],
            "eligible": cell["accuracy_lower_bound"] >= floor
            and cell["refusal_rate"] <= limit
            and within_ceiling,
        }
        for axis, cells in sorted(strata.items())
        for label, cell in sorted(cells.items())
    ]
    errors = list(report["failures"])
    try:
        validate_perception_eligibility(rows)
    except NonOracleError as refused:
        errors.append(str(refused))
    ok = not errors and report.get("eligibility") == "perception_eligible"
    return {
        "ok": ok,
        "schema_version": ELIGIBILITY_SCHEMA,
        "candidate": candidate,
        "role": role,
        "graph": graph,
        "graph_hash": manifest.get("graph_hash"),
        "report_run_id": report["run_id"],
        "report_hash": report["report_hash"],
        "envelope_hash": report["envelope_hash"],
        "eligibility": "perception_eligible" if ok else "not_eligible",
        "strata": rows,
        "errors": errors,
        "selector_input_complete": False,
        "wording": (
            "BND-7 perception eligibility only; the BND-10 selector also needs BND-8 parity "
            "and BND-9 pilot-band evidence, which this record does not carry"
        ),
    }
