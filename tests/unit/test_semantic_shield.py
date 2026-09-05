"""Identity-aware semantic authorizer, permit gateway, held-plan corpus and
three-arm replay (SEM-1, SEM-2, SEM-3, SEM-4, SEM-5, SEM-6, SEM-7, SEM-9,
SEM-10, SEM-11, SEM-12, SEM-13, SEM-15; issue #352).

Synthetic evidence only: the corpus declares every expected decision from
the condition's semantics, and the tests pin that the mechanism meets
them, that permits are single-use and stage-bound, that evidence gaps
produce no permit, and that the oracle arm stays labeled as a ceiling.
"""

from __future__ import annotations

import copy
import json
import random

import pytest
from cli_helpers import run_module

from aisle.harness.semantic_corpus import (
    CONDITIONS,
    build_corpus,
    build_plan,
    replay_plan,
    run_corpus,
)
from aisle.harness.semantic_shield import (
    ARMS,
    DEFAULT_CONFIG,
    PermitGateway,
    SemanticAuthorizer,
    content_hash,
)

pytestmark = pytest.mark.unit

KEY = b"unit-key"
SOURCE = "sha256:adapter"


def _assignment(target="amoxicillin", revision=1) -> dict:
    return {
        "assignment_id": f"asg-{revision}",
        "campaign_id": "unit",
        "session_id": "s",
        "episode_id": "ep-1",
        "goal_revision": revision,
        "target_namespace": "pharmacy.meds.v1",
        "target_identity": target,
        "vocabulary": ["amoxicillin", "ibuprofen"],
        "valid_from_s": 0.0,
        "valid_to_s": 10.0,
    }


def _assertion(t, identity="amoxicillin", prob=0.95, *, carrier=None, track="box-1", **kw) -> dict:
    return {
        "assertion_id": f"a-{t}-{identity}",
        "source_hash": SOURCE,
        "observation_id": f"o-{t}",
        "track_id": track,
        "carrier": carrier,
        "classes": {
            identity: prob,
            "ibuprofen" if identity != "ibuprofen" else "amoxicillin": round(1 - prob, 3),
        },
        "refused": False,
        "capture_s": kw.get("capture_s", t),
        "receipt_s": t,
        "in_envelope": kw.get("in_envelope", True),
        "evidence_kind": "synthetic",
    }


def _proposal(stage, t, *, carrier=None, track="box-1", revision=1, episode="ep-1") -> dict:
    return {
        "proposal_id": f"p-{stage}-{t}",
        "stage": stage,
        "track_id": track,
        "carrier": carrier,
        "goal_revision": revision,
        "episode_id": episode,
        "proposal_hash": content_hash({"stage": stage, "t": t, "track": track}),
    }


def _ready() -> SemanticAuthorizer:
    auth = SemanticAuthorizer(KEY, {SOURCE})
    auth.on_assignment(_assignment())
    for t in (0.0, 0.1, 0.2, 0.3, 0.4):
        auth.on_assertion(_assertion(t))
    return auth


def test_permit_binds_stage_proposal_and_is_consumed_once():
    """SEM-4: a permit carries assignment, assertions, track, stage, proposal
    hash, expiry, and MAC; the gateway consumes it once and rejects replay,
    expiry, wrong stage, wrong proposal, wrong carrier, and tampering."""
    auth = _ready()
    gate = PermitGateway(KEY)
    proposal = _proposal("pre_grasp", 0.45)
    permit = auth.request(proposal, 0.45)["permit"]
    assert {
        "assignment_id",
        "assertion_ids",
        "track_id",
        "stage",
        "proposal_hash",
        "expires_s",
        "mac",
    } <= set(permit)
    assert gate.check(permit, proposal, auth.state.assignment, 0.46)["permit_valid"] is True
    assert gate.check(permit, proposal, auth.state.assignment, 0.47)["reason"] == "replayed_permit"
    fresh = auth.request(_proposal("pre_grasp", 0.48), 0.48)["permit"]
    assert (
        gate.check(fresh, _proposal("pre_grasp", 0.48), auth.state.assignment, 0.48 + 1.0)["reason"]
        == "expired_permit"
    )
    fresh = auth.request(_proposal("pre_grasp", 0.49), 0.49)["permit"]
    assert (
        gate.check(fresh, _proposal("carry", 0.49), auth.state.assignment, 0.49)["reason"]
        == "wrong_stage"
    )
    assert (
        gate.check(fresh, _proposal("pre_grasp", 0.499), auth.state.assignment, 0.49)["reason"]
        == "wrong_proposal"
    )
    tampered = {**fresh, "track_id": "box-9"}
    assert (
        gate.check(
            tampered, _proposal("pre_grasp", 0.49, track="box-9"), auth.state.assignment, 0.49
        )["reason"]
        == "malformed_permit"
    )
    assert gate.check(None, proposal, auth.state.assignment, 0.5)["reason"] == "missing_permit"
    assert (
        gate.check(fresh, _proposal("pre_grasp", 0.49), _assignment(revision=2), 0.49)["reason"]
        == "wrong_goal_revision"
    )


def test_three_stages_and_pre_grasp_alone_never_authorizes_carry_or_delivery():
    """SEM-5: pre-grasp, post-closure carry against a carrier association,
    and delivery entry are separate permits; carry needs the carried-object
    association and delivery needs a fresh carry renewal."""
    auth = _ready()
    assert (
        auth.request(_proposal("carry", 0.45, carrier="gripper"), 0.45)["reason"]
        == "no_pre_grasp_permit"
    )
    assert auth.request(_proposal("pre_grasp", 0.45), 0.45)["permit"]
    # evidence without a carrier association cannot authorize carry
    assert (
        auth.request(_proposal("carry", 0.46, carrier="gripper"), 0.46)["reason"]
        == "carrier_mismatch"
    )
    auth.on_assertion(_assertion(0.5, carrier="gripper"))
    assert auth.request(_proposal("carry", 0.5, carrier="gripper"), 0.5)["permit"]
    auth.on_assertion(_assertion(1.8, carrier="gripper"))
    assert (
        auth.request(_proposal("delivery", 1.8, carrier="gripper"), 1.8)["reason"]
        == "carry_permit_stale"
    )
    assert auth.request(_proposal("carry", 1.8, carrier="gripper"), 1.8)["permit"]
    assert auth.request(_proposal("delivery", 1.9, carrier="gripper"), 1.9)["permit"]


def test_evidence_gaps_produce_no_permit_and_revocations_apply():
    """SEM-6 / SEM-7: missing, stale, future-dated, time-regressing,
    out-of-envelope, below-threshold, unsupported, disagreeing, or
    unregistered evidence yields no permit; goal change, carrier loss, and
    restart revoke outstanding permits; thresholds are the frozen config."""
    assert DEFAULT_CONFIG["confidence_min"] == 0.8 and DEFAULT_CONFIG["max_age_s"] == 0.5
    auth = SemanticAuthorizer(KEY, {SOURCE, "sha256:b"})
    auth.on_assignment(_assignment())
    assert auth.request(_proposal("pre_grasp", 0.1), 0.1)["reason"] == "missing_or_stale_identity"
    auth.on_assertion(_assertion(0.0))
    assert auth.request(_proposal("pre_grasp", 0.9), 0.9)["reason"] == "missing_or_stale_identity"
    auth.on_assertion(_assertion(1.0, capture_s=2.0))
    assert auth.request(_proposal("pre_grasp", 1.0), 1.0)["reason"] == "future_dated_identity"
    assert auth.on_assertion(_assertion(0.5)) == "time_regressing_identity"
    assert (
        auth.on_assertion({**_assertion(1.1), "source_hash": "sha256:rogue"})
        == "unregistered_source"
    )
    auth.on_assertion(_assertion(1.2, in_envelope=False))
    assert auth.request(_proposal("pre_grasp", 1.2), 1.2)["reason"] == "out_of_envelope"
    auth.on_assertion(_assertion(1.3, prob=0.6))
    assert auth.request(_proposal("pre_grasp", 1.3), 1.3)["reason"] == "below_threshold"
    auth.on_assertion(_assertion(1.4, identity="mystery"))
    assert auth.request(_proposal("pre_grasp", 1.4), 1.4)["reason"] == "unsupported_class"
    auth.on_assertion(_assertion(1.5))
    auth.on_assertion(
        {**_assertion(1.5, identity="ibuprofen"), "source_hash": "sha256:b", "assertion_id": "b"}
    )
    assert auth.request(_proposal("pre_grasp", 1.5), 1.5)["reason"] == "disagreement"
    auth.on_assertion({**_assertion(1.6), "source_hash": "sha256:b", "assertion_id": "b2"})
    auth.on_assertion(_assertion(1.6))
    permit = auth.request(_proposal("pre_grasp", 1.6), 1.6)["permit"]
    auth.on_assignment(_assignment(target="ibuprofen", revision=2))
    assert auth.state.issued[permit["permit_id"]]["revoked"] == "goal_change"
    assert auth.request(_proposal("pre_grasp", 1.61), 1.61)["reason"] == "goal_revision_mismatch"
    auth.restart()
    assert auth.state.grasp_track is None and auth.state.restarts == 1


def test_wrong_target_is_refused_with_a_controlled_halt_in_motion():
    """SEM-6 / SEM-11: a wrong-target transition is refused; in-motion
    stages request a controlled halt rather than inventing identity."""
    auth = SemanticAuthorizer(KEY, {SOURCE})
    auth.on_assignment(_assignment())
    auth.on_assertion(_assertion(0.0, identity="ibuprofen", carrier="gripper"))
    pre = auth.request(_proposal("pre_grasp", 0.0), 0.0)
    assert pre["reason"] == "wrong_target" and pre["halt_requested"] is False
    carry = auth.request(_proposal("carry", 0.01, carrier="gripper"), 0.01)
    assert carry["reason"] == "wrong_target" and carry["halt_requested"] is True


def test_corpus_covers_every_condition_with_declared_expectations():
    """SEM-9 / SEM-10: deterministic corpus, every condition present, each
    protected transition declares the expected decision per arm and the
    transition at risk independently of implementation output."""
    corpus = build_corpus(seed=3, per_condition=2)
    again = build_corpus(seed=3, per_condition=2)
    assert corpus["corpus_hash"] == again["corpus_hash"]
    assert {p["condition"] for p in corpus["plans"]} == set(CONDITIONS)
    assert corpus["authorization_parameters"] == DEFAULT_CONFIG
    for plan in corpus["plans"]:
        for event in plan["events"]:
            if event["kind"] == "proposal":
                assert set(event["expected"]) == set(ARMS)
                assert event["proposal"]["transition"]
    legal = build_plan("correct_target_negative_control", 0, random.Random(1))
    assert all(
        e["expected"]["sensor_shield"] == "permit"
        for e in legal["events"]
        if e["kind"] == "proposal"
    )


def test_three_arms_meet_declared_expectations_and_oracle_is_labeled():
    """SEM-1 / SEM-11 / SEM-12 / SEM-13 / SEM-15: sensor and oracle arms
    produce zero false allows and zero false blocks on the corpus; no_shield
    forwards everything and shows the false allows; the oracle arm is
    labeled simulation_oracle; policy success and interventions stay
    separate; a drifted corpus is refused."""
    corpus = build_corpus(seed=7, per_condition=2)
    result = run_corpus(corpus, analysis_seed=1)
    arms = result["arms"]
    assert arms["sensor_shield"]["false_allow"]["events"] == 0
    assert arms["sensor_shield"]["false_block"]["events"] == 0
    assert arms["oracle_sim_shield"]["false_allow"]["events"] == 0
    assert arms["oracle_sim_shield"]["identity_source"] == "simulation_oracle"
    assert arms["no_shield"]["false_allow"]["events"] == 2 * (len(CONDITIONS) - 1)
    assert arms["no_shield"]["false_block"]["events"] == 0
    assert result["primary"]["false_allow_risk_difference_sensor_minus_no_shield"]["upper"] < 0
    assert result["primary"]["false_block_non_inferiority"]["decision"] in (
        "non_inferior",
        "not_shown",
    )
    assert "simulation_oracle" in result["labels"]["oracle_sim_shield"]
    assert "neither policy success" in result["wording"]
    assert arms["sensor_shield"]["secondary"]["by_stage"]["carry"]["descriptive"] is True
    assert run_corpus(corpus, analysis_seed=1)["result_hash"] == result["result_hash"]
    drifted = copy.deepcopy(corpus)
    drifted["plans"][0]["target"] = "changed"
    with pytest.raises(ValueError, match="corpus hash"):
        run_corpus(drifted, analysis_seed=1)


def test_attacks_are_rejected_by_the_gateway_in_every_shield_arm():
    """SEM-4 / SEM-10: replayed permit, mutated proposal, and wrong-stage use
    are refused at the gateway with their reasons."""
    corpus = build_corpus(seed=5, per_condition=1)
    reasons = {}
    for plan in corpus["plans"]:
        if plan["condition"] in ("permit_replay", "proposal_mutation", "wrong_stage_use"):
            run = replay_plan(plan, "sensor_shield", corpus["registered_sources"]["sensor_shield"])
            attacked = [x for x in run["transitions"] if x["attack"]]
            assert attacked and not any(x["forwarded"] for x in attacked)
            reasons[plan["condition"]] = attacked[0]["gateway_reason"]
    assert reasons == {
        "permit_replay": "replayed_permit",
        "proposal_mutation": "wrong_proposal",
        "wrong_stage_use": "wrong_stage",
    }


def test_cli_corpus_and_run_follow_con8(tmp_path):
    """CON-8: JSON on stdout, exit 0 iff ok, bulky plans and runs elided."""
    corpus_path = tmp_path / "corpus.json"
    proc = run_module(
        "aisle.harness.cli",
        "semantic",
        "corpus",
        "--seed",
        "1",
        "--per-condition",
        "1",
        "--output",
        str(corpus_path),
    )
    assert proc.returncode == 0, proc.stderr
    proc = run_module(
        "aisle.harness.cli", "semantic", "run", "--corpus", str(corpus_path), "--analysis-seed", "2"
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"] is True and "runs" not in out and out["unit"] == "held_plan"


def test_assertion_binding_and_tcb_record_are_explicit():
    """SEM-3 / SEM-2: an assertion must bind source, observation, track,
    carrier, class distribution or refusal, stamps, envelope, and evidence
    kind, else it is rejected as malformed; the retained TCB record names
    the separate trusted components and the participant authority."""
    auth = SemanticAuthorizer(KEY, {SOURCE})
    auth.on_assignment(_assignment())
    partial = {k: v for k, v in _assertion(0.0).items() if k != "carrier"}
    assert auth.on_assertion(partial) == "malformed_assertion"
    assert auth.on_assertion(_assertion(0.0)) is None
    from cli_helpers import REPO_ROOT

    tcb = json.loads(
        (
            REPO_ROOT
            / "analysis/semantic-authorization/records/sem-held-plan-adversarial-v2/tcb.json"
        ).read_text()
    )
    base = tcb["trusted_computing_base"]
    assert {
        "task_assignment_source",
        "identity_adapters",
        "carrier_association",
        "authorization_state_machine",
        "protected_key",
        "clock",
        "permit_verifier",
    } <= set(base)
    assert tcb["principals_and_authority"]["participant_write_authority_over_tcb"].startswith(
        "none"
    )
    assert tcb["evidence_kind"] == "synthetic"
