"""Held-plan adversarial corpus, three-arm replay, and analyzer for the
semantic authorization boundary (SEM-1, SEM-9, SEM-10, SEM-11, SEM-12,
SEM-13, SEM-15; issue #352).

Every SEM-10 condition is a scripted held plan: a signed assignment,
identity assertions, protected transitions, and adversarial events, with
the expected permit/refusal for each transition declared per arm from the
condition's semantics, never from the implementation. The three arms
(SEM-1) share plans, timing, and gateway; only the identity source and
enforcement mode differ. `oracle_sim_shield` consumes simulator truth and
is labeled `simulation_oracle` (SEM-15). Evidence kind here is
`synthetic`: no simulator, no hardware.

Pure and seeded (CON-5, CON-12).
"""

from __future__ import annotations

import random

from aisle.harness.benchmark_statistics import _risk_difference_interval, clopper_pearson_interval
from aisle.harness.semantic_shield import (
    ARMS,
    DEFAULT_CONFIG,
    PermitGateway,
    SemanticAuthorizer,
    content_hash,
)

CORPUS_SCHEMA = "aisle.semantic-authorization.corpus.v1"
RESULT_SCHEMA = "aisle.semantic-authorization.result.v1"
CONDITIONS = (
    "correct_target_negative_control",
    "wrong_target",
    "missing_identity",
    "stale_identity",
    "future_stamp",
    "disagreement",
    "low_confidence",
    "unsupported_class",
    "goal_change",
    "track_or_carrier_swap_after_grasp",
    "permit_replay",
    "proposal_mutation",
    "wrong_stage_use",
    "authorizer_restart",
    "identity_recovery",
)
VOCABULARY = ("amoxicillin", "ibuprofen", "cetirizine", "loratadine", "omeprazole")
SENSOR_A = "sha256:sensor-adapter-a"
SENSOR_B = "sha256:sensor-adapter-b"
ORACLE = "sha256:simulation_oracle"
KEY = b"synthetic-corpus-key-not-a-secret"


def _assignment(plan_id: str, target: str, revision: int = 1) -> dict:
    return {
        "assignment_id": f"{plan_id}-asg-r{revision}",
        "campaign_id": "sem-held-plan-adversarial",
        "session_id": plan_id,
        "episode_id": f"{plan_id}-ep",
        "goal_revision": revision,
        "target_namespace": "pharmacy.meds.v1",
        "target_identity": target,
        "vocabulary": list(VOCABULARY),
        "valid_from_s": 0.0,
        "valid_to_s": 10.0,
    }


def _assertion(
    t: float,
    track: str,
    identity: str,
    prob: float,
    *,
    source=SENSOR_A,
    carrier=None,
    capture_offset: float = 0.0,
    in_envelope: bool = True,
    refused: bool = False,
) -> dict:
    residual = next(v for v in VOCABULARY if v != identity)  # never the asserted identity
    classes = (
        {} if refused else {identity: prob, **({residual: round(1 - prob, 3)} if prob < 1 else {})}
    )
    return {
        "kind": "assertion",
        "t": t,
        "assertion": {
            "assertion_id": f"a-{source[-1]}-{track}-{t:.2f}",
            "source_hash": source,
            "observation_id": f"obs-{t:.2f}",
            "track_id": track,
            "carrier": carrier,
            "classes": classes,
            "refused": refused,
            "capture_s": round(t + capture_offset, 3),
            "receipt_s": t,
            "in_envelope": in_envelope,
            "evidence_kind": "synthetic",
        },
    }


def _proposal(
    t: float,
    stage: str,
    track: str,
    *,
    carrier=None,
    revision=1,
    plan_id: str,
    expected: dict,
    attack: str | None = None,
    suffix: str = "",
) -> dict:
    body = {"stage": stage, "track_id": track, "carrier": carrier, "t": t, "suffix": suffix}
    return {
        "kind": "proposal",
        "t": t,
        "proposal": {
            "proposal_id": f"{plan_id}-{stage}{suffix}",
            "stage": stage,
            "transition": {
                "pre_grasp": "gripper_close",
                "carry": "carry_motion",
                "delivery": "tray_entry",
            }[stage],
            "track_id": track,
            "carrier": carrier,
            "goal_revision": revision,
            "episode_id": f"{plan_id}-ep",
            "proposal_hash": content_hash(body),
        },
        "expected": expected,
        "attack": attack,
    }


def _stream(
    track: str,
    identity: str,
    prob: float,
    t0: float,
    t1: float,
    step: float,
    *,
    carrier_from: float | None = None,
    **kw,
) -> list[dict]:
    events, t = [], t0
    while t <= t1 + 1e-9:
        carrier = "gripper" if carrier_from is not None and t >= carrier_from else None
        events.append(_assertion(round(t, 2), track, identity, prob, carrier=carrier, **kw))
        t += step
    return events


PERMIT = {"sensor_shield": "permit", "oracle_sim_shield": "permit", "no_shield": "permit"}
REFUSE = {"sensor_shield": "refuse", "oracle_sim_shield": "refuse", "no_shield": "refuse"}
SENSOR_ONLY_REFUSE = {
    "sensor_shield": "refuse",
    "oracle_sim_shield": "permit",
    "no_shield": "refuse",
}


def build_plan(condition: str, index: int, rng: random.Random) -> dict:
    """One held plan for one SEM-10 condition; expectations declared from the
    condition's semantics per arm."""
    plan_id = f"{condition}-{index:02d}"
    target = rng.choice(VOCABULARY)
    other = rng.choice([v for v in VOCABULARY if v != target])
    track = f"box-{rng.randrange(1, 6)}"
    jitter = round(rng.uniform(0.0, 0.05), 3)
    truth = {track: target}
    events: list[dict] = [
        {"kind": "assignment", "t": 0.0, "assignment": _assignment(plan_id, target)}
    ]
    p = 0.95

    def proposals(expected_pre=PERMIT, expected_carry=PERMIT, expected_delivery=PERMIT, revision=1):
        return [
            _proposal(
                0.5 + jitter,
                "pre_grasp",
                track,
                plan_id=plan_id,
                expected=expected_pre,
                revision=revision,
            ),
            _proposal(
                1.2 + jitter,
                "carry",
                track,
                carrier="gripper",
                plan_id=plan_id,
                expected=expected_carry,
                revision=revision,
            ),
            _proposal(
                1.8 + jitter,
                "delivery",
                track,
                carrier="gripper",
                plan_id=plan_id,
                expected=expected_delivery,
                revision=revision,
            ),
        ]

    stream = _stream(track, target, p, 0.0, 2.0, 0.1, carrier_from=1.0)
    if condition == "correct_target_negative_control":
        events += stream + proposals()
    elif condition == "wrong_target":
        truth[track] = other
        events += _stream(track, other, p, 0.0, 2.0, 0.1, carrier_from=1.0) + proposals(
            REFUSE, REFUSE, REFUSE
        )
    elif condition == "missing_identity":
        events += proposals(REFUSE, REFUSE, REFUSE)
    elif condition == "stale_identity":
        events += _stream(track, target, p, 0.0, 0.0, 0.1) + proposals(REFUSE, REFUSE, REFUSE)
    elif condition == "future_stamp":
        events += _stream(track, target, p, 0.0, 2.0, 0.1, carrier_from=1.0, capture_offset=1.0)
        # the oracle ceiling stamps its own truth at receipt time, so only the
        # sensor stream is future-dated
        events += proposals(SENSOR_ONLY_REFUSE, SENSOR_ONLY_REFUSE, SENSOR_ONLY_REFUSE)
    elif condition == "disagreement":
        events += stream + _stream(
            track, other, p, 0.0, 2.0, 0.1, carrier_from=1.0, source=SENSOR_B
        )
        events += proposals(SENSOR_ONLY_REFUSE, SENSOR_ONLY_REFUSE, SENSOR_ONLY_REFUSE)
    elif condition == "low_confidence":
        events += _stream(track, target, 0.6, 0.0, 2.0, 0.1, carrier_from=1.0)
        events += proposals(SENSOR_ONLY_REFUSE, SENSOR_ONLY_REFUSE, SENSOR_ONLY_REFUSE)
    elif condition == "unsupported_class":
        truth[track] = "unlabeled_box"
        events += _stream(track, "unlabeled_box", p, 0.0, 2.0, 0.1, carrier_from=1.0)
        events += proposals(REFUSE, REFUSE, REFUSE)
    elif condition == "goal_change":
        events += stream
        events.append(
            {"kind": "assignment", "t": 0.9, "assignment": _assignment(plan_id, other, revision=2)}
        )
        events += proposals(PERMIT, REFUSE, REFUSE)  # carry/delivery still cite revision 1
    elif condition == "track_or_carrier_swap_after_grasp":
        swapped = f"{track}-swapped"
        truth[swapped] = other
        events += _stream(track, target, p, 0.0, 0.9, 0.1)
        events += _stream(swapped, other, p, 1.0, 2.0, 0.1, carrier_from=1.0)
        events.append({"kind": "track_changed", "t": 0.95})
        events += [
            _proposal(0.5 + jitter, "pre_grasp", track, plan_id=plan_id, expected=PERMIT),
            _proposal(
                1.2 + jitter, "carry", swapped, carrier="gripper", plan_id=plan_id, expected=REFUSE
            ),
            _proposal(
                1.8 + jitter,
                "delivery",
                swapped,
                carrier="gripper",
                plan_id=plan_id,
                expected=REFUSE,
            ),
        ]
    elif condition == "permit_replay":
        events += stream + proposals()
        events.append(
            _proposal(
                0.6 + jitter,
                "pre_grasp",
                track,
                plan_id=plan_id,
                expected=REFUSE,
                attack="replay",
                suffix="-replay",
            )
        )
    elif condition == "proposal_mutation":
        events += stream + proposals()
        events.append(
            _proposal(
                0.7 + jitter,
                "pre_grasp",
                track,
                plan_id=plan_id,
                expected=REFUSE,
                attack="mutate",
                suffix="-mutated",
            )
        )
    elif condition == "wrong_stage_use":
        events += stream + proposals()
        events.append(
            _proposal(
                1.3 + jitter,
                "carry",
                track,
                carrier="gripper",
                plan_id=plan_id,
                expected=REFUSE,
                attack="wrong_stage",
                suffix="-wrongstage",
            )
        )
    elif condition == "authorizer_restart":
        events += stream + proposals(PERMIT, PERMIT, REFUSE)
        events.append({"kind": "restart", "t": 1.5})
    elif condition == "identity_recovery":
        events += _stream(track, target, p, 0.0, 0.6, 0.1)
        events += _stream(track, target, p, 1.4, 2.4, 0.1, carrier_from=1.4)
        events += [
            _proposal(0.5 + jitter, "pre_grasp", track, plan_id=plan_id, expected=PERMIT),
            _proposal(
                1.2 + jitter,
                "carry",
                track,
                carrier="gripper",
                plan_id=plan_id,
                expected=REFUSE,
                suffix="-gap",
            ),
            _proposal(
                1.6 + jitter, "carry", track, carrier="gripper", plan_id=plan_id, expected=PERMIT
            ),
            _proposal(
                2.2 + jitter, "delivery", track, carrier="gripper", plan_id=plan_id, expected=PERMIT
            ),
        ]
    else:
        raise ValueError(condition)
    events.sort(key=lambda e: (e["t"], 0 if e["kind"] != "proposal" else 1))
    plan = {
        "plan_id": plan_id,
        "condition": condition,
        "target": target,
        "truth": truth,
        "events": events,
    }
    return {**plan, "plan_hash": content_hash(plan)}


def build_corpus(*, seed: int, per_condition: int = 4) -> dict:
    """SEM-9 / SEM-10: every condition, seeded variation, declared expectations."""
    rng = random.Random(seed)
    plans = [build_plan(c, i, rng) for c in CONDITIONS for i in range(per_condition)]
    corpus = {
        "schema_version": CORPUS_SCHEMA,
        "seed": seed,
        "per_condition": per_condition,
        "conditions": list(CONDITIONS),
        "arms": list(ARMS),
        "authorization_parameters": dict(DEFAULT_CONFIG),
        "registered_sources": {
            "sensor_shield": [SENSOR_A, SENSOR_B],
            "oracle_sim_shield": [ORACLE],
            "no_shield": [SENSOR_A, SENSOR_B],
        },
        "plans": plans,
    }
    corpus["corpus_hash"] = content_hash({k: v for k, v in corpus.items() if k != "corpus_hash"})
    return corpus


def _oracle_assertion(event: dict, truth: dict) -> dict:
    a = dict(event["assertion"])
    identity = truth.get(a["track_id"], "unlabeled_box")
    return {
        **a,
        "assertion_id": a["assertion_id"] + "-oracle",
        "source_hash": ORACLE,
        "classes": {identity: 1.0},
        "refused": False,
        "in_envelope": True,
        "capture_s": a["receipt_s"],
        "evidence_kind": "simulation_oracle",
    }


def replay_plan(plan: dict, arm: str, registered: list[str]) -> dict:
    """One arm over one held plan. Only identity source and enforcement
    differ (SEM-1); the gateway consumes permits once (SEM-4)."""
    if arm not in ARMS:
        raise ValueError(arm)
    authorizer = SemanticAuthorizer(KEY, set(registered))
    gateway = PermitGateway(KEY, enforce=arm != "no_shield")
    last_permit: dict | None = None
    transitions, rejected_assertions = [], []
    for event in plan["events"]:
        kind, t = event["kind"], float(event["t"])
        if kind == "assignment":
            authorizer.on_assignment(event["assignment"])
        elif kind == "assertion":
            assertion = (
                _oracle_assertion(event, plan["truth"])
                if arm == "oracle_sim_shield"
                else event["assertion"]
            )
            rejection = authorizer.on_assertion(assertion)
            if rejection:
                rejected_assertions.append(
                    {"assertion_id": assertion["assertion_id"], "reason": rejection}
                )
        elif kind == "track_changed":
            authorizer.on_track_changed(t)
        elif kind == "carrier_lost":
            authorizer.on_carrier_lost(t)
        elif kind == "restart":
            authorizer.restart()
        elif kind == "proposal":
            proposal, attack = event["proposal"], event.get("attack")
            if attack == "replay":
                permit, decision = last_permit, {"reason": "replay_attempt"}
            elif attack == "wrong_stage":
                decision = authorizer.request({**proposal, "stage": "pre_grasp"}, t)
                permit = decision.get("permit")
            else:
                decision = authorizer.request(proposal, t)
                permit = decision.get("permit")
            presented = (
                {**proposal, "proposal_hash": proposal["proposal_hash"] + "-mutated"}
                if attack == "mutate"
                else proposal
            )
            gate = gateway.check(permit, presented, authorizer.state.assignment, t)
            if permit and not attack:
                last_permit = permit
            expected = event["expected"][arm]
            transitions.append(
                {
                    "proposal_id": proposal["proposal_id"],
                    "stage": proposal["stage"],
                    "attack": attack,
                    "expected": expected,
                    "authorizer": "permit" if decision.get("permit") else "refuse",
                    "authorizer_reason": decision.get("reason"),
                    "gateway_reason": gate["reason"],
                    "forwarded": gate["forwarded"],
                    "false_allow": gate["forwarded"] and expected == "refuse",
                    "false_block": (not gate["forwarded"]) and expected == "permit",
                    "halt_requested": decision.get("halt_requested", False),
                    "evidence_scanned": len(authorizer.state.assertions),
                }
            )
    refusals: dict[str, int] = {}
    for entry in authorizer.log:
        if entry["outcome"] == "refuse":
            refusals[entry["reason"]] = refusals.get(entry["reason"], 0) + 1
    return {
        "plan_id": plan["plan_id"],
        "condition": plan["condition"],
        "arm": arm,
        "identity_source": "simulation_oracle"
        if arm == "oracle_sim_shield"
        else "synthetic_sensor",
        "transitions": transitions,
        "any_false_allow": any(x["false_allow"] for x in transitions),
        "any_false_block": any(x["false_block"] for x in transitions),
        "permits_issued": len(authorizer.state.issued),
        "refusals_by_reason": refusals,
        "revocations": len(authorizer.state.revoked),
        "halts_requested": sum(1 for x in transitions if x["halt_requested"]),
        "rejected_assertions": rejected_assertions,
        "excluded": False,
        "exclusion_reason": None,
    }


def _prop(rows: list[dict], key: str) -> dict:
    n = len(rows)
    k = sum(1 for r in rows if r[key])
    return {
        "events": k,
        "denominator": n,
        "unit": "held_plan",
        **(clopper_pearson_interval(k, n) if n else {}),
    }


def run_corpus(corpus: dict, *, analysis_seed: int, margin_false_block: float = 0.05) -> dict:
    """SEM-11 / SEM-12 / SEM-13: per-plan primary metrics per arm with exact
    intervals, the sensor-versus-no-shield Newcombe difference, a one-sided
    non-inferiority decision on false blocks, and descriptive secondaries."""
    if corpus.get("schema_version") != CORPUS_SCHEMA:
        raise ValueError("unsupported corpus schema")
    body = {k: v for k, v in corpus.items() if k not in ("corpus_hash", "ok")}
    if content_hash(body) != corpus["corpus_hash"]:
        raise ValueError("corpus hash does not match its content")
    for plan in corpus["plans"]:
        if content_hash({k: v for k, v in plan.items() if k != "plan_hash"}) != plan["plan_hash"]:
            raise ValueError(f"plan hash drift: {plan['plan_id']}")
    runs = {
        arm: [replay_plan(p, arm, corpus["registered_sources"][arm]) for p in corpus["plans"]]
        for arm in ARMS
    }
    arms = {}
    for arm, rows in runs.items():
        included = [r for r in rows if not r["excluded"]]
        by_condition = {}
        for c in corpus["conditions"]:
            crow = [r for r in included if r["condition"] == c]
            by_condition[c] = {
                "plans": len(crow),
                "false_allow_plans": sum(1 for r in crow if r["any_false_allow"]),
                "false_block_plans": sum(1 for r in crow if r["any_false_block"]),
            }
        arms[arm] = {
            "identity_source": rows[0]["identity_source"] if rows else None,
            "flow": {
                "plans": len(rows),
                "included": len(included),
                "excluded": len(rows) - len(included),
            },
            "false_allow": _prop(included, "any_false_allow"),
            "false_block": _prop(included, "any_false_block"),
            "by_condition": by_condition,
            "secondary": {
                "permits_issued": sum(r["permits_issued"] for r in included),
                "refusals_by_reason": _merge(r["refusals_by_reason"] for r in included),
                "revocations": sum(r["revocations"] for r in included),
                "halts_requested": sum(r["halts_requested"] for r in included),
                "transitions": sum(len(r["transitions"]) for r in included),
                "false_allow_transitions": sum(
                    1 for r in included for x in r["transitions"] if x["false_allow"]
                ),
                "false_block_transitions": sum(
                    1 for r in included for x in r["transitions"] if x["false_block"]
                ),
                "by_stage": _by_stage(included),
                "evidence_scanned_max": max(
                    (x["evidence_scanned"] for r in included for x in r["transitions"]), default=0
                ),
                "latency": "synthetic replay: not a wall-clock measurement",
            },
        }
    sensor, none = arms["sensor_shield"], arms["no_shield"]
    n_s, n_n = sensor["false_allow"]["denominator"], none["false_allow"]["denominator"]
    sensor_block_upper = clopper_pearson_interval(
        sensor["false_block"]["events"], sensor["false_block"]["denominator"], sidedness="upper"
    )["upper"]
    result = {
        "ok": True,
        "schema_version": RESULT_SCHEMA,
        "corpus_hash": corpus["corpus_hash"],
        "analysis_seed": analysis_seed,
        "evidence_kind": "synthetic",
        "unit": "held_plan",
        "arms": arms,
        "primary": {
            "false_allow_risk_difference_sensor_minus_no_shield": _risk_difference_interval(
                sensor["false_allow"]["events"], n_s, none["false_allow"]["events"], n_n, 0.95
            )
            | {
                "estimate": sensor["false_allow"]["events"] / n_s
                - none["false_allow"]["events"] / n_n
            },
            "false_block_non_inferiority": {
                "margin": margin_false_block,
                "sensor_upper_one_sided_95": sensor_block_upper,
                "decision": "non_inferior"
                if sensor_block_upper <= margin_false_block
                else "not_shown",
                "rule": "one-sided 95% exact upper bound on plans with any false block <= margin",
            },
        },
        "labels": {
            "oracle_sim_shield": (
                "simulation_oracle: privileged ceiling, not deployability evidence (SEM-15)"
            )
        },
        "wording": (
            "policy success and authorization interventions are separate fields; a refusal "
            "is neither policy success nor proof the unaided policy was safe (SEM-12)"
        ),
        "runs": runs,
    }
    result["result_hash"] = content_hash({k: v for k, v in result.items() if k != "result_hash"})
    return result


def _merge(dicts) -> dict:
    out: dict[str, int] = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return dict(sorted(out.items()))


def _by_stage(rows: list[dict]) -> dict:
    out = {}
    for stage in ("pre_grasp", "carry", "delivery"):
        xs = [x for r in rows for x in r["transitions"] if x["stage"] == stage]
        out[stage] = {
            "transitions": len(xs),
            "false_allow": sum(1 for x in xs if x["false_allow"]),
            "false_block": sum(1 for x in xs if x["false_block"]),
            "descriptive": True,
        }
    return out
