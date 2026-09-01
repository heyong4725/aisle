"""Hidden-controller randomization and host-load records for SPEC 420.

The controller keeps :class:`SealedPlan` outside the participant view. Before
assignment, participants may receive only ``public_commitment()``. Assignment
records are revealed sequentially. The included audit is synthetic and
unscored; it does not authorize confirmatory collection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

COMMITMENT_SCHEMA_VERSION = "aisle.randomization-commitment.v1"
ASSIGNMENT_SCHEMA_VERSION = "aisle.randomized-assignment.v1"
HOST_LOAD_SCHEMA_VERSION = "aisle.host-load-observation.v1"
HOST_LOAD_AUDIT_SCHEMA_VERSION = "aisle.host-load-audit.v1"
AUDIT_SCHEMA_VERSION = "aisle.randomization-capability.v1"
EVIDENCE_CLASS = "synthetic_unscored_randomization_capability"
ALGORITHM = "sha256-keyed-balanced-block-order-v1"
_ALGORITHM_SHA256 = hashlib.sha256(ALGORITHM.encode()).hexdigest()
_HEX_LENGTH = 64
_PHASES = ("preflight", "postflight")


class RandomizationError(ValueError):
    """Randomization or host-load evidence is ambiguous or inconsistent."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise RandomizationError(f"record is not canonical JSON: {exc}") from exc
    return rendered.encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _content_id(record: dict[str, Any]) -> str:
    retained = dict(record)
    retained.pop("immutable_id", None)
    return f"sha256:{_sha256(_canonical_bytes(retained))}"


def _validate_names(values: Sequence[str], label: str, *, minimum: int) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise RandomizationError(f"{label} must be a sequence of resolved names")
    rendered = tuple(values)
    if len(rendered) < minimum or not all(
        isinstance(value, str) and value.strip() == value and value and "\x00" not in value
        for value in rendered
    ):
        raise RandomizationError(f"{label} must contain at least {minimum} resolved names")
    if len(rendered) != len(set(rendered)):
        raise RandomizationError(f"{label} contains a duplicate")
    return rendered


def _seed_bytes(seed_hex: str) -> bytes:
    if (
        not isinstance(seed_hex, str)
        or len(seed_hex) != _HEX_LENGTH
        or any(char not in "0123456789abcdef" for char in seed_hex)
    ):
        raise RandomizationError("randomization seed must be exactly 256 lowercase hex bits")
    return bytes.fromhex(seed_hex)


@dataclass(frozen=True)
class SealedPlan:
    """Controller-private balanced assignment plan.

    The seed and future assignments intentionally have no public serializer.
    The object itself belongs in the hidden controller, never in the agent view.
    """

    arms: tuple[str, ...]
    temporal_blocks: tuple[str, ...]
    randomization_seed_commitment: str
    plan_commitment: str
    _assignments: tuple[tuple[str, int, str], ...] = field(repr=False)

    def public_commitment(self) -> dict[str, Any]:
        return {
            "algorithm": ALGORITHM,
            "algorithm_sha256": _ALGORITHM_SHA256,
            "arms": sorted(self.arms),
            "assignments": len(self._assignments),
            "plan_commitment": self.plan_commitment,
            "randomization_seed_commitment": self.randomization_seed_commitment,
            "schema_version": COMMITMENT_SCHEMA_VERSION,
            "temporal_blocks": list(self.temporal_blocks),
        }


def create_sealed_plan(
    arms: Sequence[str], temporal_blocks: Sequence[str], seed_hex: str
) -> SealedPlan:
    """Create a reproducible balanced plan whose order stays controller-private."""

    arm_names = _validate_names(arms, "arms", minimum=2)
    blocks = _validate_names(temporal_blocks, "temporal blocks", minimum=1)
    seed = _seed_bytes(seed_hex)
    assignments: list[tuple[str, int, str]] = []
    for block in blocks:
        keyed_arms = []
        for arm in arm_names:
            key = hashlib.sha256(seed + b"\0" + block.encode() + b"\0" + arm.encode()).digest()
            keyed_arms.append((key, arm))
        keyed_arms.sort()
        assignments.extend(
            (block, position, arm) for position, (_key, arm) in enumerate(keyed_arms)
        )
    private_payload = {
        "algorithm": ALGORITHM,
        "arms": list(arm_names),
        "assignment_order": [
            {"arm": arm, "temporal_block": block, "within_block_position": position}
            for block, position, arm in assignments
        ],
        "temporal_blocks": list(blocks),
    }
    # The seed salts the plan commitment. Without it, a participant could
    # enumerate the small set of balanced arm orders and recover the future.
    plan_commitment = _sha256(seed + b"\0" + _canonical_bytes(private_payload))
    return SealedPlan(
        arms=arm_names,
        temporal_blocks=blocks,
        randomization_seed_commitment=_sha256(seed),
        plan_commitment=plan_commitment,
        _assignments=tuple(assignments),
    )


def _assignment_record(plan: SealedPlan, assignment_index: int) -> dict[str, Any]:
    if isinstance(assignment_index, bool) or not isinstance(assignment_index, int):
        raise RandomizationError("assignment index must be an integer")
    if assignment_index < 0 or assignment_index >= len(plan._assignments):
        raise RandomizationError("assignment index is outside the sealed plan")
    block, position, arm = plan._assignments[assignment_index]
    record = {
        "algorithm_sha256": _ALGORITHM_SHA256,
        "arm": arm,
        "assignment_index": assignment_index,
        "plan_commitment": plan.plan_commitment,
        "randomization_seed_commitment": plan.randomization_seed_commitment,
        "schema_version": ASSIGNMENT_SCHEMA_VERSION,
        "temporal_block": block,
        "within_block_position": position,
    }
    record["immutable_id"] = _content_id(record)
    return record


def reveal_assignment(
    plan: SealedPlan, assignment_index: int, prior_records: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Reveal exactly the next assignment after verifying the public history."""

    if assignment_index != len(prior_records):
        raise RandomizationError("assignments must be revealed sequentially")
    for index, retained in enumerate(prior_records):
        if retained != _assignment_record(plan, index):
            raise RandomizationError(f"assignment history differs at index {index}")
    return _assignment_record(plan, assignment_index)


@dataclass(frozen=True)
class HostLoadRule:
    """Frozen boundary sampling and anomaly thresholds."""

    high_normalized_load: float
    max_normalized_shift: float

    def __post_init__(self) -> None:
        for name in ("high_normalized_load", "max_normalized_shift"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise RandomizationError(f"{name} must be a positive finite number")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "high_normalized_load": float(self.high_normalized_load),
            "max_normalized_shift": float(self.max_normalized_shift),
            "metric": "os.getloadavg",
            "normalization": "load_average_divided_by_logical_cpus",
            "phases": list(_PHASES),
            "samples_per_phase": 1,
            "schema_version": "aisle.host-load-rule.v1",
        }

    @property
    def sha256(self) -> str:
        return _sha256(_canonical_bytes(self.canonical_dict()))


def _validate_observed_at(observed_at: datetime | None) -> datetime:
    value = datetime.now(UTC) if observed_at is None else observed_at
    if value.tzinfo is None or value.utcoffset() is None:
        raise RandomizationError("host-load observed_at must be timezone-aware")
    return value.astimezone(UTC)


def make_host_load_record(
    phase: str,
    rule: HostLoadRule,
    *,
    load_average: Sequence[float],
    logical_cpus: int,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Create one exact preflight or postflight observation under a frozen rule."""

    if phase not in _PHASES:
        raise RandomizationError(f"host-load phase must be one of {_PHASES}")
    values = tuple(load_average)
    if len(values) != 3 or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in values
    ):
        raise RandomizationError("host-load average must contain three finite non-negative values")
    if isinstance(logical_cpus, bool) or not isinstance(logical_cpus, int) or logical_cpus < 1:
        raise RandomizationError("host-load logical_cpus must be a positive integer")
    timestamp = _validate_observed_at(observed_at)
    record = {
        "load_average": [float(value) for value in values],
        "logical_cpus": logical_cpus,
        "normalized_load_1m": float(values[0]) / logical_cpus,
        "observed_at": timestamp.isoformat(),
        "phase": phase,
        "sampling_rule": rule.canonical_dict(),
        "sampling_rule_sha256": rule.sha256,
        "schema_version": HOST_LOAD_SCHEMA_VERSION,
    }
    record["immutable_id"] = _content_id(record)
    return record


def sample_host_load(phase: str, rule: HostLoadRule) -> dict[str, Any]:
    """Capture the current controller host under the frozen sampling rule."""

    try:
        load_average = os.getloadavg()
    except (AttributeError, OSError) as exc:
        raise RandomizationError(f"host-load sampling is unavailable: {exc}") from exc
    logical_cpus = os.cpu_count()
    if logical_cpus is None:
        raise RandomizationError("host-load logical CPU count is unavailable")
    return make_host_load_record(
        phase,
        rule,
        load_average=load_average,
        logical_cpus=logical_cpus,
    )


def _verify_host_load_record(record: dict[str, Any], phase: str, rule: HostLoadRule) -> None:
    if not isinstance(record, dict):
        raise RandomizationError(f"{phase} host-load record must be a mapping")
    if record.get("schema_version") != HOST_LOAD_SCHEMA_VERSION:
        raise RandomizationError(f"{phase} host-load schema is unsupported")
    if record.get("immutable_id") != _content_id(record):
        raise RandomizationError(f"{phase} host-load record identity is invalid")
    if record.get("phase") != phase:
        raise RandomizationError(f"{phase} host-load record has the wrong phase")
    if record.get("sampling_rule_sha256") != rule.sha256:
        raise RandomizationError(f"{phase} host-load sampling rule drifted")
    if record.get("sampling_rule") != rule.canonical_dict():
        raise RandomizationError(f"{phase} host-load sampling rule content drifted")


def classify_host_load(
    preflight: dict[str, Any], postflight: dict[str, Any], rule: HostLoadRule
) -> dict[str, Any]:
    """Retain load shifts as analyzer-visible anomalies without discarding sessions."""

    _verify_host_load_record(preflight, "preflight", rule)
    _verify_host_load_record(postflight, "postflight", rule)
    before = float(preflight["normalized_load_1m"])
    after = float(postflight["normalized_load_1m"])
    codes = []
    if after > rule.high_normalized_load:
        codes.append("HIGH_POSTFLIGHT_LOAD")
    if abs(after - before) > rule.max_normalized_shift:
        codes.append("LOAD_SHIFT")
    record = {
        "anomaly": bool(codes),
        "anomaly_codes": codes,
        "normalized_load_1m_shift": after - before,
        "postflight": postflight,
        "preflight": preflight,
        "sampling_rule_sha256": rule.sha256,
        "schema_version": HOST_LOAD_AUDIT_SCHEMA_VERSION,
    }
    record["immutable_id"] = _content_id(record)
    return record


def _case(case_id: str, passed: bool) -> dict[str, Any]:
    return {"id": case_id, "passed": bool(passed)}


def run_randomization_capability_audit() -> dict[str, Any]:
    """Run an unscored audit of concealment, balance, and load visibility."""

    seed_hex = "0123456789abcdef" * 4
    arms = ("typed", "monolithic")
    blocks = ("block-01", "block-02", "block-03")
    plan = create_sealed_plan(arms, blocks, seed_hex)
    public = plan.public_commitment()
    history: list[dict[str, Any]] = []
    for index in range(len(arms) * len(blocks)):
        history.append(reveal_assignment(plan, index, history))
    repeated = create_sealed_plan(arms, blocks, seed_hex)
    repeated_history: list[dict[str, Any]] = []
    for index in range(len(history)):
        repeated_history.append(reveal_assignment(repeated, index, repeated_history))

    rule = HostLoadRule(high_normalized_load=1.0, max_normalized_shift=0.25)
    live_preflight = sample_host_load("preflight", rule)
    live_postflight = sample_host_load("postflight", rule)
    live_load_audit = classify_host_load(live_preflight, live_postflight, rule)
    fixture_preflight = make_host_load_record(
        "preflight",
        rule,
        load_average=(2.0, 1.5, 1.0),
        logical_cpus=8,
        observed_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
    )
    fixture_postflight = make_host_load_record(
        "postflight",
        rule,
        load_average=(12.0, 8.0, 4.0),
        logical_cpus=8,
        observed_at=datetime(2026, 9, 1, 11, 0, tzinfo=UTC),
    )
    anomaly_probe = classify_host_load(fixture_preflight, fixture_postflight, rule)
    balanced_blocks = sum(
        sorted(row["arm"] for row in history if row["temporal_block"] == block) == sorted(arms)
        for block in blocks
    )
    public_rendered = json.dumps(public, sort_keys=True)
    cases = [
        _case("seed_concealed_before_assignment", seed_hex not in public_rendered),
        _case(
            "future_order_concealed_before_assignment",
            "assignment_order" not in public and "within_block_position" not in public,
        ),
        _case("balanced_temporal_blocks", balanced_blocks == len(blocks)),
        _case("frozen_seed_reproduces_order", history == repeated_history),
        _case(
            "assignments_revealed_sequentially",
            [row["assignment_index"] for row in history] == list(range(len(history))),
        ),
        _case(
            "preflight_postflight_use_frozen_rule",
            live_preflight["sampling_rule_sha256"]
            == live_postflight["sampling_rule_sha256"]
            == rule.sha256,
        ),
        _case(
            "load_anomaly_remains_visible",
            anomaly_probe["anomaly"]
            and anomaly_probe["anomaly_codes"] == ["HIGH_POSTFLIGHT_LOAD", "LOAD_SHIFT"],
        ),
    ]
    recorded_at = datetime.now(UTC)
    capability_pass = all(row["passed"] for row in cases)
    return {
        "assignments": history,
        "capability_pass": capability_pass,
        "cases": cases,
        "confirmatory_ready": False,
        "evidence_class": EVIDENCE_CLASS,
        "host_load_observations": live_load_audit,
        "implementation_sha256": _sha256(Path(__file__).read_bytes()),
        "limitations": [
            "synthetic two-arm plan only; no confirmatory assignment",
            "controller object is not yet wired to a sealed campaign launcher",
            "live host-load samples are immediate capability observations, not session brackets",
        ],
        "platform": {
            "machine": platform.machine(),
            "python": platform.python_version(),
            "release": platform.release(),
            "system": platform.system(),
        },
        "public_commitment": public,
        "recorded_at": recorded_at.isoformat(),
        "schema_version": AUDIT_SCHEMA_VERSION,
        "session_id": f"randomization-capability-{recorded_at.strftime('%Y%m%dT%H%M%S%fZ')}",
        "summary": {
            "assignments": len(history),
            "balanced_blocks": balanced_blocks,
            "checks": len(cases),
            "detection_rate": sum(row["passed"] for row in cases) / len(cases),
        },
        "synthetic_anomaly_probe": anomaly_probe,
    }


def write_randomization_capability_audit(output: Path) -> dict[str, Any]:
    """Retain one non-overwriting synthetic TRT-8 capability record."""

    output = Path(output)
    if output.exists():
        raise RandomizationError(f"randomization capability audit already exists: {output}")
    report = run_randomization_capability_audit()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise RandomizationError(
            f"randomization capability audit already exists: {output}"
        ) from exc
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the synthetic TRT-8 controller audit")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit-synthetic")
    audit.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = write_randomization_capability_audit(args.output)
    except RandomizationError as exc:
        print(json.dumps({"error": str(exc), "ok": False}, sort_keys=True), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "capability_pass": report["capability_pass"],
                "confirmatory_ready": report["confirmatory_ready"],
                "evidence_class": report["evidence_class"],
                "ok": report["capability_pass"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report["capability_pass"] else 3


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess test
    raise SystemExit(main())
