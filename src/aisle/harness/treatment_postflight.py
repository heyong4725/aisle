"""Fail-closed postflight classification for SPEC 420 treatment identity."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from aisle.harness.treatment_integrity import ManifestError, create_treatment_manifest

SCHEMA_VERSION = "aisle.treatment-postflight.v2"
ACCESS_LOG_SCHEMA_VERSION = "aisle.hidden-access-log.v1"


class PostflightError(RuntimeError):
    """A postflight record cannot be safely retained."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _content_id(value: dict) -> str:
    return f"sha256:{hashlib.sha256(_canonical_bytes(value)).hexdigest()}"


def _preflight_identity(preflight: Any) -> tuple[dict | None, str | None]:
    if not isinstance(preflight, dict):
        return None, "preflight is not a mapping"
    retained = copy.deepcopy(preflight)
    immutable_id = retained.pop("immutable_id", None)
    try:
        expected_id = _content_id(retained)
    except (TypeError, ValueError):
        return None, "preflight is not canonical JSON"
    if not isinstance(immutable_id, str) or immutable_id != expected_id:
        return None, "preflight immutable identity is absent or invalid"
    return retained, None


def _diff_paths(before: Any, after: Any, path: str = "") -> list[str]:
    if type(before) is not type(after):
        return [path or "treatment"]
    if isinstance(before, dict):
        if set(before) != set(after):
            return [path or "treatment"]
        differences: list[str] = []
        for key in sorted(before):
            child = f"{path}.{key}" if path else key
            differences.extend(_diff_paths(before[key], after[key], child))
        return differences
    if isinstance(before, list):
        return [] if before == after else [path or "treatment"]
    return [] if before == after else [path or "treatment"]


def _access_log_record(path: Path) -> tuple[dict | None, list[str], str, str]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None, ["hidden_access_log_unreadable"], "unreadable", "unresolved"

    digest = hashlib.sha256(raw).hexdigest()
    base = {"sha256": digest, "status": "invalid"}
    if (
        not isinstance(value, dict)
        or set(value) != {"adapter_active", "complete", "events", "schema_version"}
        or value.get("schema_version") != ACCESS_LOG_SCHEMA_VERSION
        or type(value.get("adapter_active")) is not bool
        or type(value.get("complete")) is not bool
        or not isinstance(value.get("events"), list)
    ):
        return base, ["hidden_access_log_invalid"], "invalid", "unresolved"

    events = value["events"]
    for event in events:
        if (
            not isinstance(event, dict)
            or set(event) != {"decision", "surface", "target_class"}
            or event.get("decision") not in {"allow", "deny"}
            or event.get("target_class") not in {"hidden", "visible"}
            or not isinstance(event.get("surface"), str)
            or not event["surface"]
        ):
            return base, ["hidden_access_log_invalid"], "invalid", "unresolved"

    hidden_denials = sum(
        event["decision"] == "deny" and event["target_class"] == "hidden" for event in events
    )
    hidden_exposures = sum(
        event["decision"] != "deny" and event["target_class"] == "hidden" for event in events
    )
    visible_allows = sum(
        event["decision"] == "allow" and event["target_class"] == "visible" for event in events
    )
    record = {
        "events_total": len(events),
        "hidden_denials": hidden_denials,
        "hidden_exposures": hidden_exposures,
        "sha256": digest,
        "status": "complete" if value["complete"] else "incomplete",
        "visible_allows": visible_allows,
    }
    reasons: list[str] = []
    confinement_status = "pass" if value["adapter_active"] else "fail"
    log_status = "pass" if value["complete"] else "incomplete"
    if not value["adapter_active"]:
        reasons.append("confinement_inactive")
    if not value["complete"]:
        reasons.append("hidden_access_log_incomplete")
    if hidden_exposures:
        reasons.append("hidden_material_exposure")
        log_status = "exposure"
    return record, reasons, log_status, confinement_status


def create_postflight_record(
    preflight: dict,
    current_candidate: dict,
    visible_root: Path,
    hidden_access_log: Path,
) -> dict:
    """Recompute treatment state and classify drift as infrastructure."""
    baseline, baseline_error = _preflight_identity(preflight)
    checks = {
        "confinement_active": "pass",
        "hidden_access_log": "pass",
        "treatment_identity": "pass",
        "visible_tree": "pass",
    }
    reasons: list[str] = []
    drift_paths: list[str] = []
    diagnostic: str | None = None

    current: dict | None = None
    try:
        current = create_treatment_manifest(current_candidate, Path(visible_root))
    except ManifestError as exc:
        checks["treatment_identity"] = "unresolved"
        checks["visible_tree"] = "unresolved"
        reasons.append("current_treatment_unresolved")
        diagnostic = str(exc)

    if baseline_error is not None:
        checks["treatment_identity"] = "unresolved"
        checks["visible_tree"] = "unresolved"
        reasons.append("preflight_unusable")
        diagnostic = baseline_error
    elif current is not None:
        current_without_id = copy.deepcopy(current)
        current_without_id.pop("immutable_id")
        drift_paths = _diff_paths(baseline, current_without_id)
        if drift_paths:
            reasons.append("treatment_drift")
            checks["treatment_identity"] = "drift"
            if any(path == "repository.visible_files" for path in drift_paths):
                checks["visible_tree"] = "drift"

    access_record, access_reasons, access_status, confinement_status = _access_log_record(
        Path(hidden_access_log)
    )
    reasons.extend(access_reasons)
    checks["hidden_access_log"] = access_status
    checks["confinement_active"] = confinement_status

    integrity_pass = not reasons
    record: dict[str, Any] = {
        "checks": checks,
        "classification": "synthetic_pass" if integrity_pass else "infrastructure_exclusion",
        "confirmatory_ready": False,
        "drift_paths": drift_paths,
        "eligible_for_estimate": False,
        "evidence_class": "synthetic_unscored_postflight",
        "exclusion_reasons": reasons,
        "preflight_immutable_id": preflight.get("immutable_id")
        if isinstance(preflight, dict)
        else None,
        "schema_version": SCHEMA_VERSION,
    }
    if current is not None:
        record["current_treatment_immutable_id"] = current["immutable_id"]
    if access_record is not None:
        record["hidden_access_log"] = access_record
    if diagnostic is not None:
        record["diagnostic"] = diagnostic
    record["immutable_id"] = _content_id(record)
    return record


def write_postflight_record(
    preflight: dict,
    current_candidate: dict,
    visible_root: Path,
    hidden_access_log: Path,
    output: Path,
) -> dict:
    """Retain one content-addressed postflight without replacing evidence."""
    output = Path(output)
    if output.exists():
        raise PostflightError(f"postflight record already exists: {output}")
    record = create_postflight_record(preflight, current_candidate, visible_root, hidden_access_log)
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(record, indent=2, sort_keys=True) + "\n"
    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(rendered)
    except FileExistsError as exc:
        raise PostflightError(f"postflight record already exists: {output}") from exc
    return record


def verify_postflight_record(record: dict) -> dict:
    """Verify retained postflight content identity and classification invariants."""
    if not isinstance(record, dict):
        raise PostflightError("postflight record must be a mapping")
    retained = copy.deepcopy(record)
    immutable_id = retained.pop("immutable_id", None)
    try:
        expected_id = _content_id(retained)
    except (TypeError, ValueError) as exc:
        raise PostflightError("postflight record is not canonical JSON") from exc
    if not isinstance(immutable_id, str) or immutable_id != expected_id:
        raise PostflightError("postflight immutable_id is absent or invalid")
    if retained.get("schema_version") != SCHEMA_VERSION:
        raise PostflightError("postflight schema_version is unsupported")
    classification = retained.get("classification")
    eligible = retained.get("eligible_for_estimate")
    reasons = retained.get("exclusion_reasons")
    if (
        classification not in {"synthetic_pass", "infrastructure_exclusion"}
        or eligible is not False
        or retained.get("confirmatory_ready") is not False
        or retained.get("evidence_class") != "synthetic_unscored_postflight"
        or not isinstance(reasons, list)
        or not all(isinstance(reason, str) and reason for reason in reasons)
        or (classification == "synthetic_pass") == bool(reasons)
    ):
        raise PostflightError("postflight classification is internally inconsistent")
    return copy.deepcopy(record)


def _read_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PostflightError(f"unreadable {label} JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PostflightError(f"{label} JSON must contain one object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SPEC 420 treatment postflight")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--preflight", type=Path, required=True)
    create.add_argument("--candidate", type=Path, required=True)
    create.add_argument("--root", type=Path, required=True)
    create.add_argument("--hidden-access-log", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--postflight", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the controller-facing postflight CLI."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            record = write_postflight_record(
                _read_json_object(args.preflight, "preflight"),
                _read_json_object(args.candidate, "candidate"),
                args.root,
                args.hidden_access_log,
                args.output,
            )
        else:
            record = verify_postflight_record(_read_json_object(args.postflight, "postflight"))
    except PostflightError as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2
    summary = {
        "classification": record["classification"],
        "immutable_id": record["immutable_id"],
        "ok": True,
    }
    print(json.dumps(summary, sort_keys=True))
    return 0 if record["classification"] == "synthetic_pass" else 3


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess test
    raise SystemExit(main())
