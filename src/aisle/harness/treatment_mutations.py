"""Independent treatment-component mutation audit for SPEC 420 TRT-13."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aisle.harness.treatment_integrity import (
    _HASH_PATHS,
    _REQUIRED_PATHS,
    ManifestError,
    create_treatment_manifest,
)
from aisle.harness.treatment_postflight import create_postflight_record

SCHEMA_VERSION = "aisle.treatment-mutation-audit.v1"
EVIDENCE_CLASS = "synthetic_unscored_treatment_mutation_audit"
_PROJECT_ROOT = Path(__file__).parents[3]
_FIXTURE_ROOT = _PROJECT_ROOT / "analysis" / "treatment-integrity" / "manifest-core"

# This inventory is deliberately declared independently from the detector's
# private _REQUIRED_PATHS tuple. The audit fails closed if the two diverge, so
# deleting a detector requirement cannot silently delete its mutation case.
_TRT1_COMPONENTS = (
    "schema_version",
    "repository.commit",
    "repository.tree",
    "repository.visible_allowlist",
    "model.requested_identity",
    "model.served_identity",
    "agent.kind",
    "agent.cli_revision",
    "agent.cli_binary_sha256",
    "sampling",
    "prompts.system_sha256",
    "prompts.research_contract_sha256",
    "runtime_binaries",
    "environment.fingerprint_sha256",
    "environment.simulator_backend",
    "environment.platform",
    "policy.approval",
    "policy.tool_policy_sha256",
    "policy.network",
    "policy.allowed_external_tools",
    "state.credential_source_class",
    "state.credential_provenance",
    "state.credential_policy_sha256",
    "state.home_baseline_sha256",
    "state.config_baseline_sha256",
    "state.cache_baseline_sha256",
    "state.environment_baseline_sha256",
    "prior_context.findings_sha256",
    "prior_context.skills_sha256",
    "prior_context.context_sha256",
    "budget.unit",
    "budget.ceiling",
    "assignment.temporal_block",
    "assignment.arm",
    "assignment.randomization_seed_commitment",
    "host_load.sampling_rule_sha256",
    "host_load.baseline",
    "confinement.adapter_binary_sha256",
    "confinement.profile_sha256",
    "confinement.policy_sha256",
)


class MutationAuditError(RuntimeError):
    """The treatment mutation audit cannot produce complete evidence."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def mutation_components() -> tuple[str, ...]:
    """Return the audit-owned TRT-1 treatment component inventory."""
    return _TRT1_COMPONENTS


def _parent(candidate: dict[str, Any], dotted_path: str) -> tuple[dict[str, Any], str]:
    components = dotted_path.split(".")
    cursor: Any = candidate
    for component in components[:-1]:
        if not isinstance(cursor, dict) or component not in cursor:
            raise MutationAuditError(f"mutation path is absent: {dotted_path}")
        cursor = cursor[component]
    if not isinstance(cursor, dict) or components[-1] not in cursor:
        raise MutationAuditError(f"mutation path is absent: {dotted_path}")
    return cursor, components[-1]


def _drop(candidate: dict[str, Any], dotted_path: str) -> None:
    parent, key = _parent(candidate, dotted_path)
    del parent[key]


def _different_hash(dotted_path: str, current: str) -> str:
    digest = hashlib.sha256(f"TRT-13:{dotted_path}".encode()).hexdigest()
    if digest == current:  # pragma: no cover - cryptographically negligible guard
        digest = hashlib.sha256(f"TRT-13:{dotted_path}:alternate".encode()).hexdigest()
    return digest


def _drift(candidate: dict[str, Any], dotted_path: str, visible_root: Path) -> None:
    parent, key = _parent(candidate, dotted_path)
    current = parent[key]
    if dotted_path == "schema_version":
        parent[key] = "aisle.treatment.invalid-mutation"
    elif dotted_path in {"repository.commit", "repository.tree"}:
        replacement = hashlib.sha1(f"TRT-13:{dotted_path}".encode()).hexdigest()
        parent[key] = replacement if replacement != current else "0" * 40
    elif dotted_path in _HASH_PATHS:
        parent[key] = _different_hash(dotted_path, current)
    elif dotted_path == "repository.visible_allowlist":
        extra = visible_root / "synthetic-extra-visible.txt"
        extra.write_text("synthetic additional visible file\n")
        parent[key] = sorted([*current, extra.name])
    elif dotted_path == "runtime_binaries":
        parent[key] = copy.deepcopy(current)
        parent[key][0]["sha256"] = _different_hash(dotted_path, current[0]["sha256"])
    elif dotted_path == "sampling":
        parent[key] = copy.deepcopy(current)
        parent[key]["seed"] += 1
    elif dotted_path == "policy.allowed_external_tools":
        parent[key] = [*current, "pytest"]
    elif dotted_path == "host_load.baseline":
        parent[key] = copy.deepcopy(current)
        parent[key]["load_1m"] += 0.5
    elif dotted_path == "budget.ceiling":
        parent[key] = current + 1
    elif isinstance(current, str):
        parent[key] = f"{current}-drift"
    else:  # fail closed if the contract adds an unhandled treatment type
        raise MutationAuditError(
            f"no drift operator for {dotted_path} with type {type(current).__name__}"
        )


def _access_log(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "adapter_active": True,
                "complete": True,
                "events": [
                    {
                        "decision": "deny",
                        "surface": "synthetic_mutation_audit",
                        "target_class": "hidden",
                    }
                ],
                "schema_version": "aisle.hidden-access-log.v1",
            }
        )
    )
    return path


def _fixture(directory: Path) -> tuple[dict[str, Any], Path, Path]:
    visible = directory / "visible"
    shutil.copytree(_FIXTURE_ROOT / "visible", visible)
    candidate = json.loads((_FIXTURE_ROOT / "candidate.json").read_text())
    return candidate, visible, _access_log(directory / "hidden-access-log.json")


def _mutation_id(kind: str, component: str) -> str:
    return f"TRT13-{kind}-{component.replace('.', '-').replace('_', '-')}"


def _missing_case(directory: Path, component: str) -> dict[str, Any]:
    candidate, visible, _ = _fixture(directory)
    _drop(candidate, component)
    diagnostic: str | None = None
    try:
        create_treatment_manifest(candidate, visible)
    except ManifestError as exc:
        detected = True
        diagnostic = str(exc)
    else:
        detected = False
    return {
        "component": component,
        "critical": True,
        "detected": detected,
        "diagnostic": diagnostic,
        "expected": "refusal",
        "mutation_id": _mutation_id("missing", component),
        "mutation_kind": "missing",
        "observed": "refusal" if detected else "accepted",
        "stage": "preflight",
    }


def _drift_case(directory: Path, component: str) -> dict[str, Any]:
    candidate, visible, access_log = _fixture(directory)
    preflight = create_treatment_manifest(candidate, visible)
    current = copy.deepcopy(candidate)
    _drift(current, component, visible)
    postflight = create_postflight_record(preflight, current, visible, access_log)
    detected = postflight["classification"] == "infrastructure_exclusion"
    return {
        "component": component,
        "critical": True,
        "detected": detected,
        "drift_paths": postflight["drift_paths"],
        "exclusion_reasons": postflight["exclusion_reasons"],
        "expected": "infrastructure_exclusion",
        "mutation_id": _mutation_id("drift", component),
        "mutation_kind": "drift",
        "observed": postflight["classification"],
        "stage": "postflight",
    }


def _false_alarm_cases(directory: Path) -> list[dict[str, Any]]:
    candidate, visible, access_log = _fixture(directory / "unchanged")
    preflight = create_treatment_manifest(candidate, visible)
    unchanged = create_postflight_record(preflight, copy.deepcopy(candidate), visible, access_log)

    candidate2, visible2, access_log2 = _fixture(directory / "irrelevant-file")
    preflight2 = create_treatment_manifest(candidate2, visible2)
    (directory / "controller-private-outside-visible-root.txt").write_text(
        "synthetic unlisted controller material\n"
    )
    irrelevant = create_postflight_record(
        preflight2, copy.deepcopy(candidate2), visible2, access_log2
    )
    rows = [
        ("unchanged-treatment", unchanged),
        ("controller-file-outside-visible-root", irrelevant),
    ]
    return [
        {
            "classification": record["classification"],
            "control_id": control_id,
            "expected": "synthetic_pass",
            "false_alarm": record["classification"] != "synthetic_pass",
        }
        for control_id, record in rows
    ]


def summarize_mutation_cases(
    mutation_cases: list[dict[str, Any]], false_alarm_cases: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compute auditable rates without hiding surviving mutations."""
    total = len(mutation_cases)
    detected = sum(case.get("detected") is True for case in mutation_cases)
    false_alarms = sum(case.get("false_alarm") is True for case in false_alarm_cases)
    survivors = sorted(
        str(case.get("mutation_id"))
        for case in mutation_cases
        if case.get("critical") is True and case.get("detected") is not True
    )
    by_kind: dict[str, dict[str, int | float]] = {}
    for kind in sorted({str(case.get("mutation_kind")) for case in mutation_cases}):
        kind_cases = [case for case in mutation_cases if case.get("mutation_kind") == kind]
        kind_detected = sum(case.get("detected") is True for case in kind_cases)
        by_kind[kind] = {
            "detection_rate": kind_detected / len(kind_cases) if kind_cases else 0.0,
            "detected": kind_detected,
            "total": len(kind_cases),
        }
    capability_pass = total > 0 and detected == total and false_alarms == 0 and not survivors
    return {
        "capability_pass": capability_pass,
        "by_kind": by_kind,
        "critical_survivor_blocks_confirmatory": bool(survivors),
        "detection_rate": detected / total if total else 0.0,
        "false_alarm_cases": len(false_alarm_cases),
        "false_alarm_rate": false_alarms / len(false_alarm_cases) if false_alarm_cases else 0.0,
        "false_alarms": false_alarms,
        "mutations_detected": detected,
        "mutations_total": total,
        "surviving_blind_spots": survivors,
    }


def run_treatment_mutation_audit() -> dict[str, Any]:
    """Run all missing/drift mutations plus negative controls."""
    components = mutation_components()
    if tuple(_REQUIRED_PATHS) != components:
        raise MutationAuditError(
            "detector treatment inventory differs from the independent TRT-13 inventory"
        )
    with tempfile.TemporaryDirectory(prefix="aisle-treatment-mutations-") as directory:
        root = Path(directory)
        mutation_cases: list[dict[str, Any]] = []
        for index, component in enumerate(components):
            mutation_cases.append(_missing_case(root / f"missing-{index:02d}", component))
            mutation_cases.append(_drift_case(root / f"drift-{index:02d}", component))
        false_alarm_cases = _false_alarm_cases(root / "false-alarms")

    mutation_cases.sort(key=lambda case: case["mutation_id"])
    summary = summarize_mutation_cases(mutation_cases, false_alarm_cases)
    recorded_at = datetime.now(UTC)
    integrity_source = _PROJECT_ROOT / "src" / "aisle" / "harness" / "treatment_integrity.py"
    postflight_source = _PROJECT_ROOT / "src" / "aisle" / "harness" / "treatment_postflight.py"
    report = {
        "capability_pass": summary["capability_pass"],
        "component_inventory": list(components),
        "component_inventory_source": "audit_declared_from_SPEC_420_TRT_1",
        "component_inventory_sha256": _sha256_bytes(_canonical_bytes(list(components))),
        "confirmatory_ready": False,
        "detector_sha256": {
            "preflight": _sha256_bytes(integrity_source.read_bytes()),
            "postflight": _sha256_bytes(postflight_source.read_bytes()),
        },
        "environment": {
            "machine": platform.machine(),
            "os": platform.system(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        },
        "evidence_class": EVIDENCE_CLASS,
        "false_alarm_cases": false_alarm_cases,
        "fixture_sha256": _sha256_bytes((_FIXTURE_ROOT / "candidate.json").read_bytes()),
        "limitations": [
            "synthetic manifest and visible-tree fixtures only",
            "does not mutate operating-system confinement or live vendor sessions",
            "not a protocol freeze or confirmatory campaign",
        ],
        "mutation_cases": mutation_cases,
        "randomization": {"seed": None, "status": "exhaustive_component_enumeration"},
        "recorded_at": recorded_at.isoformat().replace("+00:00", "Z"),
        "schema_version": SCHEMA_VERSION,
        "session_id": f"treatment-mutation-{recorded_at.strftime('%Y%m%dT%H%M%S%fZ')}",
        "source_sha256": _sha256_bytes(Path(__file__).read_bytes()),
        "summary": summary,
    }
    return report


def write_treatment_mutation_audit(output: Path) -> dict[str, Any]:
    """Write one non-overwriting treatment mutation report."""
    output = Path(output)
    if output.exists():
        raise MutationAuditError(f"treatment mutation audit already exists: {output}")
    report = run_treatment_mutation_audit()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise MutationAuditError(f"treatment mutation audit already exists: {output}") from exc
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="run all TRT-1 treatment mutations")
    audit.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = write_treatment_mutation_audit(args.output)
    except (MutationAuditError, ManifestError) as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "confirmatory_ready": report["confirmatory_ready"],
                "ok": report["capability_pass"],
                "output": str(args.output),
                "summary": report["summary"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["capability_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
