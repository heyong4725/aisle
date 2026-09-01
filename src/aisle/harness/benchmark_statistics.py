"""Auditable session-level statistics for AISLE benchmark campaigns.

The implementation is dependency-free so a frozen protocol names the whole
analysis surface through the repository commit rather than a floating
statistics stack.  It intentionally favors explicit, inspectable methods over
feature breadth: exact binomial inversion, Wilson/Newcombe intervals, a seeded
session bootstrap, and a small Kaplan-Meier implementation.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from statistics import NormalDist, fmean
from typing import Any

PROTOCOL_SCHEMA = "aisle.stats.protocol.v1"
RECORDS_SCHEMA = "aisle.stats.records.v1"
RESULT_SCHEMA = "aisle.stats.result.v1"
POWER_SCHEMA = "aisle.stats.power.v1"


class StatisticsInputError(ValueError):
    """A fail-closed protocol or campaign-record refusal."""

    def __init__(self, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.details = details or []


def _canonical_hash(value: dict) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def protocol_core_hash(protocol: dict) -> str:
    """Hash the protocol content before its detached review/freeze envelope.

    Excluding ``freeze`` avoids a circular self-hash while still binding every
    scientific choice that the reviewer approves.
    """
    return _canonical_hash({key: value for key, value in protocol.items() if key != "freeze"})


def _required(protocol: dict, names: tuple[str, ...], errors: list[str]) -> None:
    for name in names:
        if name not in protocol or protocol[name] in (None, "", [], {}):
            errors.append(f"missing required field: {name}")


def _valid_probability(value: Any, *, open_interval: bool = False) -> bool:
    if not _finite_number(value):
        return False
    return 0 < value < 1 if open_interval else 0 <= value <= 1


def _finite_number(value: Any) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_freeze(protocol: dict, errors: list[str]) -> None:
    def external_timestamp(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None

    freeze = protocol.get("freeze")
    if not isinstance(freeze, dict):
        errors.append("confirmatory freeze requires an independent statistical review")
        return
    if freeze.get("status") != "frozen":
        errors.append("freeze.status must be frozen")
    if not external_timestamp(freeze.get("external_timestamp")):
        errors.append("freeze.external_timestamp must be an externally sourced ISO-8601 timestamp")
    hashes = freeze.get("artifact_hashes")
    if not isinstance(hashes, dict) or not hashes:
        errors.append("freeze.artifact_hashes are required")
    elif not {"protocol_core", "analysis_script", "fixtures"}.issubset(hashes):
        errors.append("freeze hashes must cover protocol_core, analysis_script, and fixtures")
    elif any(
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value.removeprefix("sha256:")) != 64
        or any(character not in "0123456789abcdef" for character in value.removeprefix("sha256:"))
        for value in hashes.values()
    ):
        errors.append("every frozen artifact hash must be a sha256:<64 hex> identifier")
    elif hashes["protocol_core"] != f"sha256:{protocol_core_hash(protocol)}":
        errors.append("freeze protocol_core hash does not match the protocol content")

    review = freeze.get("review")
    if not isinstance(review, dict):
        errors.append("confirmatory freeze requires an independent statistical review")
        return
    for field in ("reviewer_id", "reviewer_role", "signature"):
        if not review.get(field):
            errors.append(f"freeze.review.{field} is required")
    if not external_timestamp(review.get("signed_at")):
        errors.append("freeze.review.signed_at must be an externally sourced ISO-8601 timestamp")
    if review.get("independent_from_analyzer_author") is not True:
        errors.append("independent statistical review must be independent from analyzer author")
    if review.get("limitations_reviewed") is not True:
        errors.append("statistical review must cover known limitations")
    findings = review.get("findings")
    if not isinstance(findings, list):
        errors.append("freeze.review.findings must retain every reviewed finding")
    elif any(
        not isinstance(item, dict)
        or item.get("disposition") != "resolved"
        or not item.get("resolution")
        for item in findings
    ):
        errors.append("every statistical review finding must be resolved with a disposition")


def validate_protocol(protocol: dict, *, purpose: str = "analysis") -> list[str]:
    """Return all protocol defects instead of silently filling design choices."""
    errors: list[str] = []
    if not isinstance(protocol, dict):
        return ["protocol must be a JSON object"]
    _required(
        protocol,
        (
            "schema_version",
            "protocol_id",
            "campaign_id",
            "campaign_phase",
            "primary_estimand",
            "experimental_unit",
            "treatment_arms",
            "control_arm",
            "treatment_arm",
            "outcome",
            "smallest_effect",
            "alpha",
            "target_power",
            "allocation_ratio",
            "stopping_rule",
            "inclusion_rules",
            "exclusion_rules",
            "analysis_seed",
            "decision",
        ),
        errors,
    )
    if errors:
        return errors
    if protocol["schema_version"] != PROTOCOL_SCHEMA:
        errors.append(f"schema_version must be {PROTOCOL_SCHEMA}")
    if protocol["campaign_phase"] not in {"pilot", "synthetic", "confirmatory"}:
        errors.append("campaign_phase must be pilot, synthetic, or confirmatory")
    if protocol["experimental_unit"] != "session":
        errors.append("experimental_unit must be session")
    estimand = protocol["primary_estimand"]
    if not isinstance(estimand, dict) or any(
        not estimand.get(field) for field in ("population", "contrast", "summary")
    ):
        errors.append("primary_estimand must declare population, contrast, and summary")
    arms = protocol["treatment_arms"]
    if (
        not isinstance(arms, list)
        or len(arms) != 2
        or not all(isinstance(arm, str) and arm for arm in arms)
        or len(set(arms)) != 2
    ):
        errors.append("treatment_arms must contain exactly two distinct arms")
    elif protocol["control_arm"] not in arms or protocol["treatment_arm"] not in arms:
        errors.append("control_arm and treatment_arm must name declared treatment_arms")
    elif protocol["control_arm"] == protocol["treatment_arm"]:
        errors.append("control_arm and treatment_arm must differ")
    if not _valid_probability(protocol["alpha"], open_interval=True):
        errors.append("alpha must be strictly between 0 and 1")
    if not _valid_probability(protocol["target_power"], open_interval=True):
        errors.append("target_power must be strictly between 0 and 1")
    if not _finite_number(protocol["allocation_ratio"]) or protocol["allocation_ratio"] <= 0:
        errors.append("allocation_ratio must be positive")
    if not isinstance(protocol["analysis_seed"], int) or isinstance(
        protocol["analysis_seed"], bool
    ):
        errors.append("analysis_seed must be an integer")

    outcome = protocol["outcome"]
    if not isinstance(outcome, dict) or outcome.get("type") not in {"binary", "continuous"}:
        errors.append("outcome.type must be binary or continuous")
    else:
        if not outcome.get("field"):
            errors.append("outcome.field is required")
        if outcome.get("direction") not in {"higher", "lower"}:
            errors.append("outcome.direction must be higher or lower")
    smallest = protocol["smallest_effect"]
    smallest_value = smallest.get("value", math.nan) if isinstance(smallest, dict) else math.nan
    if not isinstance(smallest, dict) or not _finite_number(smallest.get("value")):
        errors.append("smallest_effect.value must be numeric")
    elif smallest["value"] <= 0:
        errors.append("smallest_effect.value must be positive")
    elif isinstance(outcome, dict):
        expected_measure = (
            "risk_difference" if outcome.get("type") == "binary" else "mean_difference"
        )
        if smallest.get("measure") != expected_measure:
            errors.append(f"smallest_effect.measure must be {expected_measure}")
        if isinstance(estimand, dict) and estimand.get("summary") != smallest.get("measure"):
            errors.append("primary_estimand.summary must match smallest_effect.measure")

    stopping = protocol["stopping_rule"]
    if (
        not isinstance(stopping, dict)
        or stopping.get("type") != "fixed"
        or not isinstance(stopping.get("max_sessions_per_arm"), int)
        or stopping["max_sessions_per_arm"] <= 0
    ):
        errors.append("stopping_rule must declare a positive fixed max_sessions_per_arm")
    if not isinstance(protocol["inclusion_rules"], list) or not protocol["inclusion_rules"]:
        errors.append("inclusion_rules must be a non-empty list")
    if not isinstance(protocol["exclusion_rules"], list) or not protocol["exclusion_rules"]:
        errors.append("exclusion_rules must be a non-empty list")

    decision = protocol["decision"]
    kinds = {"superiority", "equivalence", "non_inferiority"}
    if not isinstance(decision, dict) or decision.get("kind") not in kinds:
        errors.append("decision.kind must be superiority, equivalence, or non_inferiority")
    elif decision["kind"] in {"equivalence", "non_inferiority"}:
        margin = decision.get("margin")
        if not _finite_number(margin) or margin <= 0:
            errors.append("decision.margin must be positive for equivalence/non-inferiority")
    elif decision.get("margin") is not None or not decision.get("rationale"):
        errors.append("superiority must declare margin null with a not-applicable rationale")

    power = protocol.get("power")
    if not isinstance(power, dict):
        errors.append("power assumptions are required")
    elif isinstance(outcome, dict) and outcome.get("type") == "binary":
        for field in ("control_probability", "treatment_probability"):
            if not _valid_probability(power.get(field)):
                errors.append(f"power.{field} must be a probability")
        if all(
            _valid_probability(power.get(field))
            for field in ("control_probability", "treatment_probability")
        ):
            declared_difference = abs(
                float(power["treatment_probability"]) - float(power["control_probability"])
            )
            if not math.isclose(
                declared_difference,
                float(smallest_value),
                rel_tol=0,
                abs_tol=1e-12,
            ):
                errors.append("binary power probabilities must differ by smallest_effect.value")
            signed_difference = float(power["treatment_probability"]) - float(
                power["control_probability"]
            )
            if (outcome.get("direction") == "higher" and signed_difference <= 0) or (
                outcome.get("direction") == "lower" and signed_difference >= 0
            ):
                errors.append("binary power probabilities conflict with outcome.direction")
    elif isinstance(outcome, dict) and outcome.get("type") == "continuous":
        if (
            not _finite_number(power.get("standard_deviation"))
            or power.get("standard_deviation", 0) <= 0
        ):
            errors.append("power.standard_deviation must be positive")
        if not _finite_number(power.get("mean_difference")) or power.get("mean_difference", 0) == 0:
            errors.append("power.mean_difference must be non-zero")
        elif not math.isclose(
            abs(float(power["mean_difference"])),
            float(smallest_value),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            errors.append("power.mean_difference must equal smallest_effect.value")
        elif (outcome.get("direction") == "higher" and power["mean_difference"] <= 0) or (
            outcome.get("direction") == "lower" and power["mean_difference"] >= 0
        ):
            errors.append("power.mean_difference sign conflicts with outcome.direction")
    if isinstance(power, dict):
        if power.get("test_sidedness") not in {"one-sided", "two-sided"}:
            errors.append("power.test_sidedness must be one-sided or two-sided")
        sensitivity = power.get("sensitivity_effects")
        if (
            not isinstance(sensitivity, list)
            or not sensitivity
            or any(not _finite_number(value) or value <= 0 for value in sensitivity)
        ):
            errors.append("power.sensitivity_effects must be a non-empty list of positive numbers")

    bootstrap = protocol.get("bootstrap")
    if not isinstance(bootstrap, dict):
        errors.append("bootstrap assumptions are required")
    else:
        if not isinstance(bootstrap.get("replicates"), int) or bootstrap.get("replicates", 0) < 100:
            errors.append("bootstrap.replicates must be an integer of at least 100")
        if not _valid_probability(bootstrap.get("confidence_level"), open_interval=True):
            errors.append("bootstrap.confidence_level must be strictly between 0 and 1")

    survival = protocol.get("survival")
    if not isinstance(survival, dict) or not all(
        survival.get(field) for field in ("event_time_field", "censor_time_field")
    ):
        errors.append("survival must declare event_time_field and censor_time_field")
    zero_event = protocol.get("zero_event")
    if not isinstance(zero_event, dict) or not all(
        zero_event.get(field)
        for field in ("event_field", "exposure_field", "exposure_unit", "confidence_level")
    ):
        errors.append("zero_event must declare event, exposure, unit, and confidence fields")
    elif not _valid_probability(zero_event["confidence_level"], open_interval=True):
        errors.append("zero_event.confidence_level must be strictly between 0 and 1")
    if not isinstance(protocol.get("strata"), list) or any(
        not isinstance(field, str) or not field for field in protocol.get("strata", [])
    ):
        errors.append("strata must be a list, empty only when predeclared")
    threshold = protocol.get("artifact_claim_threshold")
    if not _valid_probability(threshold, open_interval=True):
        errors.append("artifact_claim_threshold must be strictly between 0 and 1")

    if purpose not in {"power", "analysis", "freeze"}:
        errors.append(f"unknown validation purpose: {purpose}")
    if purpose == "freeze" and protocol.get("campaign_phase") != "confirmatory":
        errors.append("only a confirmatory protocol can be frozen for scored collection")
    if protocol.get("campaign_phase") == "confirmatory" and purpose in {"analysis", "freeze"}:
        _validate_freeze(protocol, errors)
    return errors


def _normal_power(delta: float, standard_error: float, critical: float, *, sidedness: str) -> float:
    normal = NormalDist()
    upper = 1.0 - normal.cdf((critical - delta) / standard_error)
    if sidedness == "one-sided":
        return min(1.0, max(0.0, upper))
    lower = normal.cdf((-critical - delta) / standard_error)
    return min(1.0, max(0.0, upper + lower))


def _binary_power(p0: float, p1: float, n0: int, n1: int, alpha: float, sidedness: str) -> float:
    pooled = (n0 * p0 + n1 * p1) / (n0 + n1)
    null_se = math.sqrt(pooled * (1.0 - pooled) * (1.0 / n0 + 1.0 / n1))
    alt_se = math.sqrt(p0 * (1.0 - p0) / n0 + p1 * (1.0 - p1) / n1)
    if alt_se == 0:
        return 1.0
    tail_alpha = alpha / 2.0 if sidedness == "two-sided" else alpha
    critical = NormalDist().inv_cdf(1.0 - tail_alpha) * null_se
    delta = abs(p1 - p0) if sidedness == "one-sided" else p1 - p0
    return _normal_power(delta, alt_se, critical, sidedness=sidedness)


def _continuous_power(
    delta: float, sd: float, n0: int, n1: int, alpha: float, sidedness: str
) -> float:
    standard_error = sd * math.sqrt(1.0 / n0 + 1.0 / n1)
    tail_alpha = alpha / 2.0 if sidedness == "two-sided" else alpha
    critical = NormalDist().inv_cdf(1.0 - tail_alpha) * standard_error
    return _normal_power(abs(delta), standard_error, critical, sidedness=sidedness)


def _required_sample_size(protocol: dict, effect: float) -> tuple[int, int, float]:
    outcome_type = protocol["outcome"]["type"]
    ratio = float(protocol["allocation_ratio"])
    target = float(protocol["target_power"])
    alpha = float(protocol["alpha"])
    assumptions = protocol["power"]
    sidedness = assumptions["test_sidedness"]

    def achieved_for(control_n: int) -> tuple[int, float]:
        treatment_n = max(2, math.ceil(control_n * ratio))
        if outcome_type == "binary":
            p0 = float(assumptions["control_probability"])
            declared_p1 = float(assumptions["treatment_probability"])
            sign = 1.0 if declared_p1 >= p0 else -1.0
            p1 = p0 + sign * effect
            if not 0 <= p1 <= 1:
                raise StatisticsInputError("binary sensitivity effect leaves probability range")
            achieved = _binary_power(p0, p1, control_n, treatment_n, alpha, sidedness)
        else:
            achieved = _continuous_power(
                effect,
                float(assumptions["standard_deviation"]),
                control_n,
                treatment_n,
                alpha,
                sidedness,
            )
        return treatment_n, achieved

    maximum = 1_000_000
    high = 2
    _, achieved = achieved_for(high)
    while achieved < target and high < maximum:
        high = min(maximum, high * 2)
        _, achieved = achieved_for(high)
    if achieved < target:
        raise StatisticsInputError("power target requires more than 1,000,000 control sessions")
    low = 2
    while low < high:
        midpoint = (low + high) // 2
        _, midpoint_power = achieved_for(midpoint)
        if midpoint_power >= target:
            high = midpoint
        else:
            low = midpoint + 1
    treatment_n, achieved = achieved_for(low)
    return low, treatment_n, achieved


def power_analysis(protocol: dict, *, protocol_hash: str | None = None) -> dict:
    """Compute frozen-assumption power without inspecting campaign outcomes."""
    errors = validate_protocol(protocol, purpose="power")
    if errors:
        raise StatisticsInputError("invalid protocol", errors)
    effect = abs(float(protocol["smallest_effect"]["value"]))
    n0, n1, achieved = _required_sample_size(protocol, effect)
    sensitivity = []
    for candidate in protocol["power"].get("sensitivity_effects", [effect]):
        c0, c1, candidate_power = _required_sample_size(protocol, abs(float(candidate)))
        sensitivity.append(
            {
                "effect": float(candidate),
                "per_arm_sample_size": {
                    protocol["control_arm"]: c0,
                    protocol["treatment_arm"]: c1,
                },
                "achieved_power": candidate_power,
            }
        )
    method = (
        "normal approximation for two independent session proportions"
        if protocol["outcome"]["type"] == "binary"
        else "normal approximation for two independent session means"
    )
    result = {
        "ok": True,
        "schema_version": POWER_SCHEMA,
        "protocol_id": protocol["protocol_id"],
        "campaign_id": protocol["campaign_id"],
        "analysis_seed": protocol["analysis_seed"],
        "protocol_core_sha256": protocol_core_hash(protocol),
        "method": method,
        "experimental_unit": "session",
        "per_arm_sample_size": {
            protocol["control_arm"]: n0,
            protocol["treatment_arm"]: n1,
        },
        "achieved_power": achieved,
        "assumptions": {
            "alpha": protocol["alpha"],
            "target_power": protocol["target_power"],
            "allocation_ratio": protocol["allocation_ratio"],
            "smallest_effect": protocol["smallest_effect"],
            **protocol["power"],
        },
        "sensitivity": sensitivity,
        "warnings": [
            "normal-approximation planning assumptions require independent review",
            *(
                ["difference-detection power is not equivalence/non-inferiority decision power"]
                if protocol["decision"]["kind"] != "superiority"
                else []
            ),
        ],
    }
    if protocol_hash:
        result["input_hashes"] = {"protocol_sha256": protocol_hash}
    return result


def _binomial_cdf(k: int, n: int, probability: float) -> float:
    return sum(
        math.comb(n, value) * probability**value * (1.0 - probability) ** (n - value)
        for value in range(k + 1)
    )


def _bisect_binomial(*, k: int, n: int, target: float, upper_tail: bool) -> float:
    low, high = 0.0, 1.0
    for _ in range(90):
        midpoint = (low + high) / 2.0
        value = (
            1.0 - _binomial_cdf(k - 1, n, midpoint) if upper_tail else _binomial_cdf(k, n, midpoint)
        )
        if upper_tail:
            if value < target:
                low = midpoint
            else:
                high = midpoint
        elif value > target:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def clopper_pearson_interval(
    successes: int,
    n: int,
    *,
    confidence_level: float = 0.95,
    sidedness: str = "two-sided",
) -> dict:
    """Exact binomial interval obtained by inverting binomial tails."""
    if n <= 0 or successes < 0 or successes > n:
        raise StatisticsInputError(f"malformed binomial cell: {successes}/{n}")
    if not _valid_probability(confidence_level, open_interval=True):
        raise StatisticsInputError("confidence_level must be strictly between 0 and 1")
    if sidedness not in {"two-sided", "lower", "upper"}:
        raise StatisticsInputError("sidedness must be two-sided, lower, or upper")
    alpha = 1.0 - confidence_level
    tail = alpha / 2.0 if sidedness == "two-sided" else alpha
    lower = (
        0.0 if successes == 0 else _bisect_binomial(k=successes, n=n, target=tail, upper_tail=True)
    )
    upper = (
        1.0 if successes == n else _bisect_binomial(k=successes, n=n, target=tail, upper_tail=False)
    )
    if sidedness == "lower":
        upper = 1.0
    elif sidedness == "upper":
        lower = 0.0
    return {
        "method": "Clopper-Pearson exact",
        "confidence_level": confidence_level,
        "sidedness": sidedness,
        "lower": lower,
        "upper": upper,
    }


def _wilson_interval(
    successes: int, n: int, confidence: float, sidedness: str
) -> tuple[float, float]:
    if n <= 0:
        return math.nan, math.nan
    alpha = 1.0 - confidence
    z = NormalDist().inv_cdf(1.0 - (alpha / 2.0 if sidedness == "two-sided" else alpha))
    proportion = successes / n
    denominator = 1.0 + z * z / n
    center = (proportion + z * z / (2.0 * n)) / denominator
    half = z * math.sqrt(proportion * (1.0 - proportion) / n + z * z / (4.0 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def _risk_difference_interval(
    successes_treatment: int,
    n_treatment: int,
    successes_control: int,
    n_control: int,
    confidence: float,
    sidedness: str = "two-sided",
) -> dict:
    control = _wilson_interval(successes_control, n_control, confidence, sidedness)
    treatment = _wilson_interval(successes_treatment, n_treatment, confidence, sidedness)
    treatment_rate = successes_treatment / n_treatment
    control_rate = successes_control / n_control
    difference = treatment_rate - control_rate
    # Newcombe method 10 combines Wilson score distances without pooling.
    lower = difference - math.sqrt(
        (treatment_rate - treatment[0]) ** 2 + (control[1] - control_rate) ** 2
    )
    upper = difference + math.sqrt(
        (treatment[1] - treatment_rate) ** 2 + (control_rate - control[0]) ** 2
    )
    if sidedness == "lower":
        upper = None
    elif sidedness == "upper":
        lower = None
    return {
        "method": "Newcombe score",
        "confidence_level": confidence,
        "sidedness": sidedness,
        "lower": lower,
        "upper": upper,
    }


def _outcome_value(session: dict, protocol: dict) -> float | bool | None:
    outcome = protocol["outcome"]
    if outcome.get("source") == "nested_outcomes":
        values = [
            float(item["value"])
            for item in session.get("nested_outcomes", [])
            if item.get("metric") == outcome["field"]
        ]
        return fmean(values) if values else None
    if outcome["type"] == "binary":
        return session.get("outcome", {}).get(outcome["field"])
    return session.get("outcome", {}).get(outcome["field"])


def _validate_records(protocol: dict, records: dict) -> list[dict]:
    if not isinstance(records, dict):
        raise StatisticsInputError("records must be a JSON object")
    if records.get("schema_version") != RECORDS_SCHEMA:
        raise StatisticsInputError(f"records schema_version must be {RECORDS_SCHEMA}")
    for field in ("protocol_id", "campaign_id", "campaign_phase"):
        if records.get(field) != protocol[field]:
            raise StatisticsInputError(f"records {field} does not match frozen protocol")
    sessions = records.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise StatisticsInputError("records.sessions must be a non-empty list")

    ids: set[str] = set()
    arm_counts: Counter[str] = Counter()
    cost_schema: set[str] | None = None
    survival = protocol["survival"]
    event_time_field = survival["event_time_field"]
    censor_time_field = survival["censor_time_field"]
    required = {
        "session_id",
        "protocol_id",
        "campaign_id",
        "campaign_phase",
        "treatment",
        "agent_system",
        "temporal_block",
        "assignment_status",
        "lifecycle_status",
        "inclusion",
        "budget",
        "outcome",
        "costs",
        "exposure",
        "artifacts",
        "wall_s",
    }
    for index, session in enumerate(sessions):
        if not isinstance(session, dict):
            raise StatisticsInputError(f"session row {index} must be an object")
        missing = sorted(required - session.keys())
        if missing:
            raise StatisticsInputError(f"session row {index} missing fields: {', '.join(missing)}")
        session_id = session["session_id"]
        if not isinstance(session_id, str) or not session_id:
            raise StatisticsInputError(f"session row {index} has invalid session_id")
        if session_id in ids:
            raise StatisticsInputError(f"duplicate session_id: {session_id}")
        ids.add(session_id)
        for field in ("protocol_id", "campaign_id", "campaign_phase"):
            if session[field] != protocol[field]:
                raise StatisticsInputError(f"session {session_id} {field} differs from protocol")
        arm = session["treatment"]
        if arm not in protocol["treatment_arms"]:
            raise StatisticsInputError(f"session {session_id} treatment is outside frozen schema")
        arm_counts[arm] += 1
        if session["assignment_status"] != "randomized":
            raise StatisticsInputError(
                f"session-flow reconciliation failed: {session_id} is not a randomized assignment"
            )
        if session["lifecycle_status"] not in {
            "never_started",
            "started",
            "completed",
            "infrastructure_excluded",
            "censored",
        }:
            raise StatisticsInputError(f"session {session_id} has invalid lifecycle_status")
        if not session["agent_system"] or not session["temporal_block"]:
            raise StatisticsInputError(
                f"session {session_id} requires agent_system and temporal_block"
            )
        if any(not session.get(field) for field in protocol["strata"]):
            raise StatisticsInputError(f"session {session_id} lacks a predeclared stratum field")
        if not isinstance(session["budget"], dict) or not isinstance(
            session["budget"].get("censored"), bool
        ):
            raise StatisticsInputError(f"session {session_id} has malformed budget/censoring data")
        if session["budget"].get("censored") and not session["budget"].get("reason"):
            raise StatisticsInputError(f"censored session {session_id} requires a reason")
        if not isinstance(session["costs"], dict) or not session["costs"]:
            raise StatisticsInputError(f"session {session_id} must retain cost fields")
        row_cost_schema = set(session["costs"])
        if cost_schema is None:
            cost_schema = row_cost_schema
        elif row_cost_schema != cost_schema:
            raise StatisticsInputError(f"session {session_id} cost schema differs from campaign")
        if (
            not isinstance(session["outcome"], dict)
            or protocol["outcome"]["field"] not in session["outcome"]
        ):
            raise StatisticsInputError(f"session {session_id} outcome schema differs from protocol")
        if protocol["outcome"].get("source") == "nested_outcomes":
            nested = session.get("nested_outcomes")
            if not isinstance(nested, list) or any(
                not isinstance(item, dict)
                or not item.get("metric")
                or not _finite_number(item.get("value"))
                for item in nested
            ):
                raise StatisticsInputError(
                    f"session {session_id} nested outcome schema differs from protocol"
                )
        if event_time_field not in session["outcome"] and event_time_field not in session:
            raise StatisticsInputError(
                f"session {session_id} lacks declared survival event-time field"
            )
        if censor_time_field not in session["outcome"] and censor_time_field not in session:
            raise StatisticsInputError(
                f"session {session_id} lacks declared survival censor-time field"
            )
        event_time = session.get(event_time_field, session["outcome"].get(event_time_field))
        censor_time = session.get(censor_time_field, session["outcome"].get(censor_time_field))
        if event_time is not None and (not _finite_number(event_time) or event_time < 0):
            raise StatisticsInputError(f"session {session_id} has invalid survival event time")
        if not _finite_number(censor_time) or censor_time < 0:
            raise StatisticsInputError(f"session {session_id} has invalid survival censor time")
        if event_time is not None and event_time > censor_time:
            raise StatisticsInputError(f"session {session_id} event time exceeds censor time")
        if not isinstance(session["exposure"], dict) or any(
            field not in session["exposure"]
            for field in (
                protocol["zero_event"]["event_field"],
                protocol["zero_event"]["exposure_field"],
                "unit",
            )
        ):
            raise StatisticsInputError(
                f"session {session_id} exposure schema differs from protocol"
            )
        if not isinstance(session["artifacts"], list):
            raise StatisticsInputError(f"session {session_id} artifacts must be a list")
        if any(not isinstance(artifact, dict) for artifact in session["artifacts"]):
            raise StatisticsInputError(f"session {session_id} has malformed artifact observation")
        inclusion = session["inclusion"]
        if not isinstance(inclusion, dict) or not isinstance(inclusion.get("included"), bool):
            raise StatisticsInputError(f"session {session_id} has malformed inclusion status")
        if not inclusion["included"] and not inclusion.get("reason"):
            raise StatisticsInputError(f"excluded session {session_id} requires a reason")
        if inclusion["included"] and session["lifecycle_status"] in {
            "never_started",
            "infrastructure_excluded",
        }:
            raise StatisticsInputError(f"session-flow reconciliation failed for {session_id}")
        value = _outcome_value(session, protocol)
        if inclusion["included"] and value is None and not session["budget"].get("censored"):
            raise StatisticsInputError(f"included session {session_id} lacks outcome or censoring")
        if (
            protocol["outcome"]["type"] == "binary"
            and value is not None
            and not isinstance(value, bool)
        ):
            raise StatisticsInputError(f"session {session_id} binary outcome must be boolean")
        if (
            protocol["outcome"]["type"] == "continuous"
            and value is not None
            and not _finite_number(value)
        ):
            raise StatisticsInputError(f"session {session_id} continuous outcome must be finite")

    limit = protocol["stopping_rule"]["max_sessions_per_arm"]
    for arm, count in arm_counts.items():
        if count > limit:
            raise StatisticsInputError(
                f"{count} {arm} assignments exceed frozen stopping rule limit {limit}"
            )
    if sum(arm_counts.values()) != len(sessions):
        raise StatisticsInputError("session-flow count reconciliation failed")
    return sessions


def _flow_table(protocol: dict, sessions: list[dict]) -> dict:
    def counts(rows: list[dict]) -> dict:
        return {
            "randomized": len(rows),
            "started": sum(row["lifecycle_status"] != "never_started" for row in rows),
            "completed": sum(row["lifecycle_status"] == "completed" for row in rows),
            "included": sum(row["inclusion"]["included"] for row in rows),
            "infrastructure_excluded": sum(
                row["lifecycle_status"] == "infrastructure_excluded" for row in rows
            ),
            "censored": sum(
                row["inclusion"]["included"] and row["budget"].get("censored", False)
                for row in rows
            ),
            "analyzed": sum(
                row["inclusion"]["included"] and _outcome_value(row, protocol) is not None
                for row in rows
            ),
        }

    reasons = Counter(
        row["inclusion"]["reason"] for row in sessions if not row["inclusion"]["included"]
    )
    censor_reasons = Counter(
        row["budget"].get("reason")
        for row in sessions
        if row["inclusion"]["included"] and row["budget"].get("censored")
    )
    return {
        "overall": counts(sessions),
        "by_arm": {
            arm: counts([row for row in sessions if row["treatment"] == arm])
            for arm in protocol["treatment_arms"]
        },
        "reasons": dict(sorted(reasons.items())),
        "censor_reasons": dict(sorted(censor_reasons.items())),
    }


def _binary_summary(values: list[bool], confidence: float = 0.95) -> dict:
    successes = sum(values)
    n = len(values)
    return {
        "successes": successes,
        "n": n,
        "rate": successes / n,
        "interval": clopper_pearson_interval(
            successes, n, confidence_level=confidence, sidedness="two-sided"
        ),
    }


def _binary_effect(protocol: dict, sessions: list[dict], confidence: float = 0.95) -> dict:
    included = [row for row in sessions if row["inclusion"]["included"]]
    by_arm: dict[str, list[bool]] = {}
    for arm in protocol["treatment_arms"]:
        values = [
            bool(value)
            for row in included
            if row["treatment"] == arm and (value := _outcome_value(row, protocol)) is not None
        ]
        if not values:
            raise StatisticsInputError(f"no analyzed sessions in arm {arm}")
        by_arm[arm] = values
    control = by_arm[protocol["control_arm"]]
    treatment = by_arm[protocol["treatment_arm"]]
    interval = _risk_difference_interval(
        sum(treatment), len(treatment), sum(control), len(control), confidence
    )
    strata: dict[str, dict] = {}
    for stratum_field in protocol.get("strata", []):
        levels = sorted({str(row.get(stratum_field)) for row in included})
        for level in levels:
            rows = [row for row in included if str(row.get(stratum_field)) == level]
            arm_values = {
                arm: [
                    bool(value)
                    for row in rows
                    if row["treatment"] == arm
                    and (value := _outcome_value(row, protocol)) is not None
                ]
                for arm in protocol["treatment_arms"]
            }
            if all(arm_values.values()):
                strata[level] = {
                    "stratum_field": stratum_field,
                    "arms": {arm: _binary_summary(values) for arm, values in arm_values.items()},
                    "estimate": fmean(arm_values[protocol["treatment_arm"]])
                    - fmean(arm_values[protocol["control_arm"]]),
                    "interval": _risk_difference_interval(
                        sum(arm_values[protocol["treatment_arm"]]),
                        len(arm_values[protocol["treatment_arm"]]),
                        sum(arm_values[protocol["control_arm"]]),
                        len(arm_values[protocol["control_arm"]]),
                        confidence,
                    ),
                }
    return {
        "experimental_unit": "session",
        "contrast": f"{protocol['treatment_arm']} minus {protocol['control_arm']}",
        "session_points": [
            {
                "session_id": row["session_id"],
                "treatment": row["treatment"],
                "agent_system": row["agent_system"],
                "temporal_block": row["temporal_block"],
                "outcome": bool(value),
            }
            for row in included
            if (value := _outcome_value(row, protocol)) is not None
        ],
        "arms": {arm: _binary_summary(values, confidence) for arm, values in by_arm.items()},
        "estimate": fmean(treatment) - fmean(control),
        "interval": interval,
        "strata": strata,
    }


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def _bootstrap_difference(
    control: list[float],
    treatment: list[float],
    *,
    seed: int,
    replicates: int,
    confidence: float,
    sidedness: str = "two-sided",
) -> dict:
    rng = random.Random(seed)
    draws = []
    for _ in range(replicates):
        control_draw = [rng.choice(control) for _ in control]
        treatment_draw = [rng.choice(treatment) for _ in treatment]
        draws.append(fmean(treatment_draw) - fmean(control_draw))
    alpha = 1.0 - confidence
    lower: float | None = _quantile(draws, alpha / 2.0)
    upper: float | None = _quantile(draws, 1.0 - alpha / 2.0)
    if sidedness == "lower":
        lower = _quantile(draws, alpha)
        upper = None
    elif sidedness == "upper":
        lower = None
        upper = _quantile(draws, 1.0 - alpha)
    return {
        "method": "treatment-stratified seeded percentile session bootstrap",
        "resampling_unit": "session",
        "stratified_by": "treatment",
        "seed": seed,
        "replicates": replicates,
        "confidence_level": confidence,
        "sidedness": sidedness,
        "lower": lower,
        "upper": upper,
    }


def _continuous_values(protocol: dict, sessions: list[dict]) -> dict[str, list[float]]:
    by_arm: dict[str, list[float]] = {}
    for arm in protocol["treatment_arms"]:
        values = [
            float(value)
            for row in sessions
            if row["inclusion"]["included"]
            and row["treatment"] == arm
            and (value := _outcome_value(row, protocol)) is not None
        ]
        if not values:
            raise StatisticsInputError(f"no analyzed sessions in arm {arm}")
        by_arm[arm] = values
    return by_arm


def _continuous_effect(
    protocol: dict,
    sessions: list[dict],
    *,
    confidence: float = 0.95,
    sidedness: str = "two-sided",
) -> dict:
    by_arm = _continuous_values(protocol, sessions)
    control = by_arm[protocol["control_arm"]]
    treatment = by_arm[protocol["treatment_arm"]]
    bootstrap = protocol.get("bootstrap", {})
    interval = _bootstrap_difference(
        control,
        treatment,
        seed=protocol["analysis_seed"],
        replicates=int(bootstrap.get("replicates", 2000)),
        confidence=confidence,
        sidedness=sidedness,
    )
    aggregation = (
        "mean of nested observations within each session"
        if protocol["outcome"].get("source") == "nested_outcomes"
        else "one declared outcome value per session"
    )
    return {
        "experimental_unit": "session",
        "aggregation": aggregation,
        "contrast": f"{protocol['treatment_arm']} minus {protocol['control_arm']}",
        "session_points": [
            {
                "session_id": row["session_id"],
                "treatment": row["treatment"],
                "agent_system": row["agent_system"],
                "temporal_block": row["temporal_block"],
                "outcome": float(value),
            }
            for row in sessions
            if row["inclusion"]["included"] and (value := _outcome_value(row, protocol)) is not None
        ],
        "arms": {arm: {"n": len(values), "mean": fmean(values)} for arm, values in by_arm.items()},
        "estimate": fmean(treatment) - fmean(control),
        "bootstrap": interval,
        "interval": interval,
    }


def _artifact_outcomes(protocol: dict, sessions: list[dict]) -> list[dict]:
    cells: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for session in sessions:
        if not session["inclusion"]["included"]:
            continue
        for artifact in session.get("artifacts", []):
            if not isinstance(artifact.get("success"), bool) or not artifact.get("artifact_id"):
                raise StatisticsInputError(
                    f"session {session['session_id']} has malformed artifact observation"
                )
            cells[(str(artifact["artifact_id"]), session["treatment"])].append(artifact["success"])
    threshold = float(protocol.get("artifact_claim_threshold", 0.90))
    result = []
    for (artifact_id, arm), values in sorted(cells.items()):
        successes = sum(values)
        interval = clopper_pearson_interval(
            successes,
            len(values),
            confidence_level=1.0 - protocol["alpha"],
            sidedness="lower",
        )
        result.append(
            {
                "artifact_id": artifact_id,
                "treatment": arm,
                "successes": successes,
                "n": len(values),
                "rate": successes / len(values),
                "observation_unit": "nested artifact outcome",
                "treatment_replication": False,
                "interval": interval,
                "claim_threshold": threshold,
                "claim_above_threshold": interval["lower"] > threshold,
            }
        )
    return result


def _kaplan_meier(protocol: dict, sessions: list[dict]) -> dict:
    config = protocol.get("survival")
    if not isinstance(config, dict):
        raise StatisticsInputError("survival protocol declaration is required")
    event_field = config.get("event_time_field")
    censor_field = config.get("censor_time_field")
    if not event_field or not censor_field:
        raise StatisticsInputError("survival event and censor time fields are required")

    def field(row: dict, name: str) -> Any:
        return row.get(name, row.get("outcome", {}).get(name))

    result = {}
    for arm in protocol["treatment_arms"]:
        observations: list[tuple[float, bool]] = []
        for session in sessions:
            if not session["inclusion"]["included"] or session["treatment"] != arm:
                continue
            accepted = field(session, event_field)
            censor_time = field(session, censor_field)
            if accepted is None and not isinstance(censor_time, int | float):
                raise StatisticsInputError(
                    f"session {session['session_id']} lacks declared censor time"
                )
            observations.append(
                (
                    float(accepted if accepted is not None else censor_time),
                    accepted is not None,
                )
            )
        if not observations:
            result[arm] = {"n": 0, "events": 0, "censored": 0, "table": [], "points": []}
            continue
        survival = 1.0
        table = []
        points = []
        for time in sorted({item[0] for item in observations}):
            at_risk = sum(item_time >= time for item_time, _ in observations)
            events = sum(item_time == time and event for item_time, event in observations)
            censored = sum(item_time == time and not event for item_time, event in observations)
            if events:
                survival *= 1.0 - events / at_risk
            table.append({"time": time, "at_risk": at_risk, "events": events, "censored": censored})
            points.append({"time": time, "survival": survival})
        event_count = sum(event for _, event in observations)
        result[arm] = {
            "n": len(observations),
            "events": event_count,
            "censored": len(observations) - event_count,
            "table": table,
            "points": points,
        }
    return result


def _zero_event_bound(protocol: dict, sessions: list[dict]) -> dict:
    config = protocol.get("zero_event")
    if not isinstance(config, dict):
        raise StatisticsInputError("zero_event protocol declaration is required")
    event_field = config.get("event_field")
    exposure_field = config.get("exposure_field")
    expected_unit = config.get("exposure_unit")
    events = 0
    exposure = 0
    for session in sessions:
        if not session["inclusion"]["included"]:
            continue
        record = session.get("exposure")
        if not isinstance(record, dict):
            raise StatisticsInputError(f"session {session['session_id']} lacks exposure record")
        if record.get("unit") != expected_unit:
            raise StatisticsInputError("missing or mixed exposure unit")
        event_value = record.get(event_field)
        exposure_value = record.get(exposure_field)
        if (
            not isinstance(event_value, int)
            or isinstance(event_value, bool)
            or event_value < 0
            or not isinstance(exposure_value, int)
            or isinstance(exposure_value, bool)
            or exposure_value <= 0
            or event_value > exposure_value
        ):
            raise StatisticsInputError("malformed event/exposure counts")
        events += event_value
        exposure += exposure_value
    if exposure == 0:
        raise StatisticsInputError("missing exposure denominator")
    confidence = float(config.get("confidence_level", 0.95))
    interval = clopper_pearson_interval(
        events, exposure, confidence_level=confidence, sidedness="upper"
    )
    return {
        "events": events,
        "exposure": exposure,
        "exposure_unit": expected_unit,
        "confidence_level": confidence,
        "method": interval["method"],
        "sidedness": "upper",
        "upper": interval["upper"],
    }


def _decision(protocol: dict, sessions: list[dict]) -> dict:
    kind = protocol["decision"]["kind"]
    if kind == "superiority":
        return {"kind": "superiority", "decision": "not_evaluated_by_equivalence_rule"}
    margin = float(protocol["decision"]["margin"])
    direction = protocol["outcome"]["direction"]
    if kind == "equivalence":
        confidence = 1.0 - 2.0 * protocol["alpha"]
        sidedness = "two-sided"
    else:
        confidence = 1.0 - protocol["alpha"]
        sidedness = "lower" if direction == "higher" else "upper"

    if protocol["outcome"]["type"] == "continuous":
        interval = _continuous_effect(
            protocol, sessions, confidence=confidence, sidedness=sidedness
        )["interval"]
    else:
        values = _binary_effect(protocol, sessions, confidence=confidence)["arms"]
        treatment = values[protocol["treatment_arm"]]
        control = values[protocol["control_arm"]]
        interval = _risk_difference_interval(
            treatment["successes"],
            treatment["n"],
            control["successes"],
            control["n"],
            confidence,
            sidedness,
        )
    if kind == "equivalence":
        decision = interval["lower"] > -margin and interval["upper"] < margin
        rule = f"lower > {-margin:g} and upper < {margin:g}"
    elif direction == "higher":
        decision = interval["lower"] > -margin
        rule = f"one-sided lower > {-margin:g}"
    else:
        decision = interval["upper"] < margin
        rule = f"one-sided upper < {margin:g}"
    return {
        "kind": kind,
        "margin": margin,
        "alpha": protocol["alpha"],
        "direction": direction,
        "interval": interval,
        "rule": rule,
        "decision": decision,
    }


def analyze_campaign(
    protocol: dict,
    records: dict,
    *,
    input_hashes: dict[str, str] | None = None,
) -> dict:
    """Analyze raw retained sessions using only protocol-declared choices."""
    errors = validate_protocol(protocol, purpose="analysis")
    if errors:
        raise StatisticsInputError("invalid protocol", errors)
    sessions = _validate_records(protocol, records)
    flow = _flow_table(protocol, sessions)
    if flow["overall"]["randomized"] != len(sessions):
        raise StatisticsInputError("session-flow count reconciliation failed")

    missing_primary = sum(
        row["inclusion"]["included"] and _outcome_value(row, protocol) is None for row in sessions
    )
    warnings = (
        [
            f"{missing_primary} included censored session(s) are retained in flow/survival "
            "but excluded from the primary effect"
        ]
        if missing_primary
        else []
    )
    report = {
        "ok": True,
        "schema_version": RESULT_SCHEMA,
        "protocol_id": protocol["protocol_id"],
        "campaign_id": protocol["campaign_id"],
        "campaign_phase": protocol["campaign_phase"],
        "analysis_seed": protocol["analysis_seed"],
        "protocol_core_sha256": protocol_core_hash(protocol),
        "input_hashes": input_hashes
        or {
            "protocol_canonical_sha256": _canonical_hash(protocol),
            "records_canonical_sha256": _canonical_hash(records),
        },
        "assumptions": {
            "experimental_unit": "session",
            "nested_observations_are_not_independent_replicates": True,
            "interval_methods": {
                "binomial": "Clopper-Pearson exact",
                "risk_difference": "Newcombe score",
                "continuous": "treatment-stratified seeded session bootstrap",
                "survival": "Kaplan-Meier",
            },
            "limitations": [
                "analysis cannot repair biased tasks, treatment leakage, or informative failures",
                "small-session bootstrap intervals can be coarse",
                "power results depend on declared assumptions",
            ],
        },
        "warnings": warnings,
        "session_flow": flow,
        "artifact_outcomes": _artifact_outcomes(protocol, sessions),
        "survival": _kaplan_meier(protocol, sessions),
        "zero_event": _zero_event_bound(protocol, sessions),
    }
    if protocol["outcome"]["type"] == "binary":
        report["binary_effect"] = _binary_effect(
            protocol, sessions, confidence=1.0 - protocol["alpha"]
        )
    else:
        report["continuous_effect"] = _continuous_effect(
            protocol,
            sessions,
            confidence=protocol["bootstrap"]["confidence_level"],
        )
    report["decision"] = _decision(protocol, sessions)
    return report
