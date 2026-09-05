"""Identity-aware semantic authorizer and permit-consuming gateway
(SEM-2, SEM-3, SEM-4, SEM-5, SEM-6, SEM-7, SEM-8; issue #352).

The kinematic guard knows nothing about medicine identity and the verifier
sees a wrong-object outcome only after it happens. This module is the
separate mechanism SPEC 480 asks to evaluate: a `SemanticAuthorizer` that
issues short-lived, single-stage, single-use permits binding a signed task
assignment, a set of identity assertions, a carried-object track, and one
proposal hash; and a `PermitGateway` that authenticates and consumes each
permit once, refusing missing, malformed, replayed, expired, wrong-stage,
wrong-proposal, wrong-episode, wrong-goal-revision, and wrong-carrier
permits. Missing, refused, stale, future-dated, time-regressing,
out-of-envelope, below-threshold, unregistered, or disagreeing identity
evidence produces no permit; goal changes, carrier loss, track changes, and
restarts revoke.

Pure and clock-injected (CON-5, CON-12): every time is a contract time
passed by the caller. Keys are caller-provided bytes; nothing here reads
the environment. Simulation-oracle identity is a privileged ceiling and is
labeled as such by its source kind (SEM-15).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

STAGES = ("pre_grasp", "carry", "delivery")
ARMS = ("no_shield", "oracle_sim_shield", "sensor_shield")
DEFAULT_CONFIG = {
    "confidence_min": 0.80,
    "max_age_s": 0.50,
    "renewal_s": 1.00,
    "permit_ttl_s": 0.30,
    "disagreement_rule": "every registered source in the window must share the argmax",
    "fusion_rule": "none: one assertion per source, latest by receipt",
}
REFUSALS = (
    "no_assignment",
    "assignment_expired",
    "goal_revision_mismatch",
    "episode_mismatch",
    "missing_or_stale_identity",
    "future_dated_identity",
    "time_regressing_identity",
    "unregistered_source",
    "out_of_envelope",
    "below_threshold",
    "unsupported_class",
    "disagreement",
    "wrong_target",
    "no_pre_grasp_permit",
    "carrier_mismatch",
    "track_mismatch",
    "carry_permit_stale",
    "unknown_stage",
)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def content_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


@dataclass
class AuthorizerState:
    assignment: dict | None = None
    assertions: list[dict] = field(default_factory=list)
    last_receipt_s: float | None = None
    grasp_track: str | None = None
    carrier: str | None = None
    carry_renewed_s: float | None = None
    issued: dict[str, dict] = field(default_factory=dict)
    revoked: list[dict] = field(default_factory=list)
    restarts: int = 0


class SemanticAuthorizer:
    """SEM-2 trusted component: signed assignment source, registered identity
    adapters, carrier association, authorization state machine, protected
    key, monotonic contract clock."""

    def __init__(self, key: bytes, registered_sources: set[str], config: dict | None = None):
        self._key = key
        self._sources = set(registered_sources)
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.state = AuthorizerState()
        self.log: list[dict] = []

    # -- inputs ---------------------------------------------------------
    def on_assignment(self, assignment: dict) -> None:
        """SEM-3 / SEM-6: a new assignment or goal revision revokes permits."""
        previous = self.state.assignment
        self.state.assignment = assignment
        if previous is not None and previous != assignment:
            self._revoke("goal_change")

    def on_assertion(self, assertion: dict) -> str | None:
        """SEM-3: bind source, observation, track, carrier, class distribution
        or refusal, stamps, envelope. Returns a rejection reason or None."""
        required = {
            "assertion_id",
            "source_hash",
            "observation_id",
            "track_id",
            "carrier",
            "classes",
            "refused",
            "capture_s",
            "receipt_s",
            "in_envelope",
            "evidence_kind",
        }
        if not required <= set(assertion):
            return "malformed_assertion"
        if assertion["source_hash"] not in self._sources:
            return "unregistered_source"
        receipt = float(assertion["receipt_s"])
        if self.state.last_receipt_s is not None and receipt < self.state.last_receipt_s:
            return "time_regressing_identity"
        self.state.last_receipt_s = receipt
        self.state.assertions.append(assertion)
        return None

    def on_carrier_lost(self, now_s: float) -> None:
        self._revoke("carrier_loss")
        self.state.grasp_track, self.state.carrier, self.state.carry_renewed_s = None, None, None

    def on_track_changed(self, now_s: float) -> None:
        self._revoke("track_change")
        self.state.grasp_track, self.state.carry_renewed_s = None, None

    def restart(self) -> None:
        """SEM-6: a restart forgets every permit and every carried association."""
        self._revoke("authorizer_restart")
        assignment = self.state.assignment
        self.state = AuthorizerState(assignment=assignment, restarts=self.state.restarts + 1)

    def _revoke(self, reason: str) -> None:
        for permit in self.state.issued.values():
            if not permit.get("revoked"):
                permit["revoked"] = reason
                self.state.revoked.append({"permit_id": permit["permit_id"], "reason": reason})

    # -- decision -------------------------------------------------------
    def _current_evidence(self, proposal: dict, now_s: float) -> tuple[dict | None, str | None]:
        """Latest usable assertion per source for the proposal's track."""
        cfg = self.config
        latest: dict[str, dict] = {}
        rejection = None
        for a in self.state.assertions:
            if a["track_id"] != proposal["track_id"] or float(a["receipt_s"]) > now_s:
                continue
            if float(a["capture_s"]) > now_s:
                rejection = "future_dated_identity"
                continue
            if now_s - float(a["capture_s"]) > cfg["max_age_s"]:
                continue
            if not a.get("in_envelope", False):
                rejection = "out_of_envelope"
                continue
            if a.get("refused") or not a.get("classes"):
                rejection = "missing_or_stale_identity"
                continue
            latest[a["source_hash"]] = a
        if not latest:
            return None, rejection or "missing_or_stale_identity"
        argmaxes = set()
        for a in latest.values():
            identity, prob = max(a["classes"].items(), key=lambda kv: kv[1])
            if prob < cfg["confidence_min"]:
                return None, "below_threshold"
            if identity not in self.state.assignment["vocabulary"]:
                return None, "unsupported_class"
            argmaxes.add(identity)
        if len(argmaxes) > 1:
            return None, "disagreement"
        chosen = sorted(latest.values(), key=lambda a: a["receipt_s"])[-1]
        return {
            "identity": argmaxes.pop(),
            "assertions": sorted(latest.values(), key=lambda a: a["assertion_id"]),
            "carrier": chosen.get("carrier"),
        }, None

    def request(self, proposal: dict, now_s: float) -> dict:
        """SEM-4 / SEM-5: one permit for one stage, one proposal hash, one
        track; or a refusal with its reason. Every request is logged."""
        state = self.state
        decision = self._decide(proposal, now_s)
        if decision.get("permit"):
            permit = decision["permit"]
            state.issued[permit["permit_id"]] = permit
            if proposal["stage"] == "pre_grasp":
                state.grasp_track = proposal["track_id"]
                state.carrier = proposal.get("carrier")
            elif proposal["stage"] == "carry":
                state.carry_renewed_s = now_s
        self.log.append(
            {
                "t_s": now_s,
                "proposal_id": proposal["proposal_id"],
                "stage": proposal["stage"],
                "outcome": "permit" if decision.get("permit") else "refuse",
                "reason": decision.get("reason"),
                "halt_requested": decision.get("halt_requested", False),
            }
        )
        return decision

    def _decide(self, proposal: dict, now_s: float) -> dict:
        state, cfg = self.state, self.config
        stage = proposal["stage"]
        if stage not in STAGES:
            return {"reason": "unknown_stage"}
        assignment = state.assignment
        if assignment is None:
            return {"reason": "no_assignment"}
        if not float(assignment["valid_from_s"]) <= now_s <= float(assignment["valid_to_s"]):
            return {"reason": "assignment_expired"}
        if proposal.get("episode_id") != assignment["episode_id"]:
            return {"reason": "episode_mismatch"}
        if proposal.get("goal_revision") != assignment["goal_revision"]:
            return {"reason": "goal_revision_mismatch"}
        evidence, reason = self._current_evidence(proposal, now_s)
        halting = stage != "pre_grasp"  # motion already in progress: refusal = controlled halt
        if evidence is None:
            return {"reason": reason, "halt_requested": halting}
        if evidence["identity"] != assignment["target_identity"]:
            return {"reason": "wrong_target", "halt_requested": halting}
        if stage in ("carry", "delivery"):
            if state.grasp_track is None:
                return {"reason": "no_pre_grasp_permit", "halt_requested": True}
            if proposal["track_id"] != state.grasp_track:
                return {"reason": "track_mismatch", "halt_requested": True}
            if evidence["carrier"] != proposal.get("carrier") or proposal.get("carrier") is None:
                return {"reason": "carrier_mismatch", "halt_requested": True}
        if stage == "delivery":
            renewed = state.carry_renewed_s
            if renewed is None or now_s - renewed > cfg["renewal_s"]:
                return {"reason": "carry_permit_stale", "halt_requested": True}
        body = {
            "assignment_id": assignment["assignment_id"],
            "episode_id": assignment["episode_id"],
            "goal_revision": assignment["goal_revision"],
            "assertion_ids": [a["assertion_id"] for a in evidence["assertions"]],
            "track_id": proposal["track_id"],
            "carrier": proposal.get("carrier"),
            "stage": stage,
            "transition": proposal.get("transition", stage),
            "proposal_hash": proposal["proposal_hash"],
            "issued_s": now_s,
            "expires_s": now_s + cfg["permit_ttl_s"],
            "authorizer_state": content_hash(
                {
                    "grasp_track": state.grasp_track,
                    "restarts": state.restarts,
                    "n": len(state.issued),
                }
            ),
        }
        permit_id = content_hash(body)
        mac = hmac.new(self._key, canonical(body) + permit_id.encode(), "sha256").hexdigest()
        return {"permit": {**body, "permit_id": permit_id, "mac": mac}}


class PermitGateway:
    """SEM-4 actuation-gateway permit verifier: authenticates and consumes
    each permit once, logging independently of policy and verifier."""

    def __init__(self, key: bytes, *, enforce: bool = True):
        self._key = key
        self.enforce = enforce
        self.consumed: set[str] = set()
        self.log: list[dict] = []

    def check(
        self, permit: dict | None, proposal: dict, assignment: dict | None, now_s: float
    ) -> dict:
        reason = self._reason(permit, proposal, assignment, now_s)
        if reason is None:
            self.consumed.add(permit["permit_id"])
        forwarded = reason is None or not self.enforce
        entry = {
            "t_s": now_s,
            "proposal_id": proposal["proposal_id"],
            "stage": proposal["stage"],
            "permit_valid": reason is None,
            "reason": reason,
            "forwarded": forwarded,
            "enforced": self.enforce,
        }
        self.log.append(entry)
        return entry

    def _reason(self, permit, proposal, assignment, now_s) -> str | None:
        if permit is None:
            return "missing_permit"
        required = {
            "permit_id",
            "mac",
            "stage",
            "proposal_hash",
            "expires_s",
            "episode_id",
            "goal_revision",
            "carrier",
            "track_id",
        }
        if not isinstance(permit, dict) or not required <= set(permit):
            return "malformed_permit"
        body = {k: v for k, v in permit.items() if k not in ("permit_id", "mac", "revoked")}
        expected_id = content_hash(body)
        expected_mac = hmac.new(
            self._key, canonical(body) + expected_id.encode(), "sha256"
        ).hexdigest()
        if permit["permit_id"] != expected_id or not hmac.compare_digest(
            permit["mac"], expected_mac
        ):
            return "malformed_permit"
        if permit["permit_id"] in self.consumed:
            return "replayed_permit"
        if permit.get("revoked"):
            return "revoked_permit"
        if now_s > float(permit["expires_s"]):
            return "expired_permit"
        if permit["stage"] != proposal["stage"]:
            return "wrong_stage"
        if permit["proposal_hash"] != proposal["proposal_hash"]:
            return "wrong_proposal"
        if assignment is None or permit["episode_id"] != assignment["episode_id"]:
            return "wrong_episode"
        if permit["goal_revision"] != assignment["goal_revision"]:
            return "wrong_goal_revision"
        if (
            permit["carrier"] != proposal.get("carrier")
            or permit["track_id"] != proposal["track_id"]
        ):
            return "wrong_carrier"
        return None
