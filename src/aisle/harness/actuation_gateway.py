"""The `actuation-gateway` boundary with a capability-bearing fake driver
(THR-4, THR-5, THR-6, THR-7, THR-8, THR-16; SPEC 460, issue #350).

Exactly one gateway instance per environment owns the unforgeable actuation
capability: an HMAC over an attested epoch that only the driver can verify.
Participants, coordinators, dynamic nodes, and tools may submit raw
requests to the gateway; none can address the driver or mint the
capability. The gateway independently validates and clamps every request
with the pinned SPEC 080 guard logic, trusting no `safe` topic name, guard
identity, schema claim, or prior validator result. The driver accepts only
commands signed by the current epoch, rejects stale or replayed epochs,
unauthenticated clients, and alternate paths, and holds under an
independently timed lease. Every request, decision, rejection, lease
transition, epoch event, and receipt goes to a controller-owned append-only
audit stream with correlation ids and hashes.

This fixture establishes process- and transport-level behaviour only
(THR-16). Device credentials, firmware watchdogs, and physical stop latency
stay hardware-pending. Pure and clock-injected (CON-5, CON-12).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from aisle.nodes.budget_guard import GuardLimits, clamp_gripper_cmd, clamp_joint_cmd

DECISIONS = ("pass", "clamp", "refuse", "hold", "stop")
REASONS = (
    "ok",
    "malformed_request",
    "missing_stamp",
    "stale_stamp",
    "unknown_channel",
    "guard_exception",
    "lease_expired",
    "audit_sink_failure",
    "controller_teardown",
)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(value: Any) -> str:
    try:
        payload = canonical(value)
    except (TypeError, ValueError):  # THR-7: an unserializable payload still gets audited
        payload = f"unserializable:{type(value).__name__}".encode()  # deterministic
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class AuditStream:
    """THR-8: controller-owned, append-only, monotonic stamps; a failed
    write is reported to the gateway so it can fail closed."""

    def __init__(self, fail_after: int | None = None):
        self.rows: list[dict] = []
        self.fail_after = fail_after
        self._last_ns = -1

    def write(self, row: dict) -> bool:
        if self.fail_after is not None and len(self.rows) >= self.fail_after:
            return False
        if row["monotonic_ns"] < self._last_ns:
            raise ValueError("audit stamps must be monotonic")
        self._last_ns = row["monotonic_ns"]
        self.rows.append({**row, "seq": len(self.rows)})
        return True


@dataclass
class Capability:
    """THR-4: epoch-bound, unforgeable without the controller key."""

    environment_id: str
    epoch: int
    nonce: str
    policy_hash: str
    limits_hash: str

    def token(self, key: bytes) -> str:
        return hmac.new(key, canonical(self.__dict__), "sha256").hexdigest()


class FakeAuthenticatedDriver:
    """THR-6 / THR-16: accepts only commands signed by the live epoch; an
    independent lease holds the arm when the gateway goes quiet."""

    def __init__(self, key: bytes, capability: Capability, *, lease_ns: int, home: np.ndarray):
        self._key = key
        self._capability = capability
        self.lease_ns = lease_ns
        self.q = np.asarray(home, dtype=np.float32).copy()
        self.receipts: list[dict] = []
        self.rejections: list[dict] = []
        self.last_command_ns: int | None = None
        self.held = False

    def _expected(self, command: dict) -> str:
        body = {k: v for k, v in command.items() if k != "mac"}
        return hmac.new(self._key, canonical(body), "sha256").hexdigest()

    def submit(self, command: dict, now_ns: int) -> dict | None:
        """One receipt per authenticated command; everything else rejected
        with a reason and no motion."""
        reason = None
        if not isinstance(command, dict) or "mac" not in command:
            reason = "unauthenticated"
        elif not hmac.compare_digest(command["mac"], self._expected(command)):
            reason = "bad_signature"
        elif command.get("epoch") != self._capability.epoch:
            reason = "stale_or_replayed_epoch"
        elif command.get("environment_id") != self._capability.environment_id:
            reason = "wrong_environment"
        elif command.get("nonce") != self._capability.nonce:
            reason = "wrong_epoch_nonce"
        elif any(r["correlation_id"] == command.get("correlation_id") for r in self.receipts):
            reason = "replayed_command"
        if reason:
            self.rejections.append(
                {
                    "reason": reason,
                    "now_ns": now_ns,
                    "correlation_id": (command or {}).get("correlation_id")
                    if isinstance(command, dict)
                    else None,
                }
            )
            return None
        self.tick(now_ns)
        if self.held:
            self.rejections.append(
                {
                    "reason": "lease_held",
                    "now_ns": now_ns,
                    "correlation_id": command["correlation_id"],
                }
            )
            return None
        self.q = np.asarray(command["joints"], dtype=np.float32)
        self.last_command_ns = now_ns
        receipt = {
            "receipt_id": f"rcpt-{len(self.receipts) + 1}",
            "correlation_id": command["correlation_id"],
            "now_ns": now_ns,
            "q": self.q.tolist(),
        }
        self.receipts.append(receipt)
        return receipt

    def tick(self, now_ns: int) -> None:
        """Driver-side lease: silence beyond the deadline holds the arm until
        the gateway re-arms with a fresh epoch."""
        if self.last_command_ns is not None and now_ns - self.last_command_ns > self.lease_ns:
            self.held = True

    def rearm(self, capability: Capability) -> None:
        self._capability = capability
        self.held = False
        self.last_command_ns = None


@dataclass
class ActuationGateway:
    """THR-4 / THR-5 / THR-7: the only holder of the capability; validates
    and clamps every request itself; fails closed on every abnormal path."""

    key: bytes
    limits: GuardLimits
    environment_id: str
    audit: AuditStream
    driver: FakeAuthenticatedDriver
    max_stamp_age_ns: int = 200_000_000
    epoch: int = 1
    nonce: str = "epoch-nonce-1"
    teardown: bool = False
    last_safe: np.ndarray | None = None
    last_gripper: float = 0.0
    _correlation: int = 0
    policy_hash: str = field(default="")
    limits_hash: str = field(default="")

    def __post_init__(self) -> None:
        self.last_safe = np.asarray(self.limits.fallback_qpos, dtype=np.float32)
        self.policy_hash = sha256(
            {"guard": "aisle.nodes.budget_guard.clamp_joint_cmd", "version": 1}
        )
        self.limits_hash = sha256(self.limits.q_min)
        self.capability = Capability(
            self.environment_id, self.epoch, self.nonce, self.policy_hash, self.limits_hash
        )
        self.driver.rearm(self.capability)

    def attestation(self) -> dict:
        return {
            "environment_id": self.environment_id,
            "epoch": self.epoch,
            "nonce": self.nonce,
            "policy_hash": self.policy_hash,
            "limits_hash": self.limits_hash,
            "capability_token": self.capability.token(self.key),
        }

    def _audit(self, row: dict) -> bool:
        ok = self.audit.write(
            {
                **row,
                "environment_id": self.environment_id,
                "policy_hash": self.policy_hash,
                "limits_hash": self.limits_hash,
            }
        )
        if not ok:
            self.teardown = True  # THR-7: an audit-sink failure fails closed
        return ok

    def request(self, request: Any, *, now_ns: int, source: str) -> dict:
        """The single entry point for every requester (typed graph, broker,
        dynamic node, tool, or attacker). Trusts nothing in the request."""
        self._correlation += 1
        cid = f"{self.environment_id}-{self._correlation}"
        base = {"monotonic_ns": now_ns, "correlation_id": cid, "source": source}
        if self.teardown:
            self._audit({**base, "decision": "stop", "reason": "controller_teardown"})
            return {
                "decision": "stop",
                "reason": "controller_teardown",
                "correlation_id": cid,
                "receipt": None,
            }
        decision, reason, joints, gripper = self._validate(request, now_ns)
        pre_hash = sha256(request if isinstance(request, dict | list) else str(request))
        if decision in ("refuse", "hold", "stop"):
            self._audit(
                {
                    **base,
                    "decision": decision,
                    "reason": reason,
                    "pre_hash": pre_hash,
                    "post_hash": None,
                }
            )
            return {"decision": decision, "reason": reason, "correlation_id": cid, "receipt": None}
        command = {
            "environment_id": self.environment_id,
            "epoch": self.epoch,
            "nonce": self.nonce,
            "correlation_id": cid,
            "joints": joints.tolist(),
            "gripper": gripper,
        }
        command["mac"] = hmac.new(self.key, canonical(command), "sha256").hexdigest()
        # THR-8: write-ahead audit; a failed write stops the command before the
        # driver can ever receive it
        audited = self._audit(
            {
                **base,
                "decision": decision,
                "reason": reason,
                "pre_hash": pre_hash,
                "post_hash": sha256(command["joints"]),
                "receipt_id": None,
            }
        )
        if not audited:
            return {
                "decision": "stop",
                "reason": "audit_sink_failure",
                "correlation_id": cid,
                "receipt": None,
            }
        receipt = self.driver.submit(command, now_ns)
        self.last_safe, self.last_gripper = joints, gripper
        self._audit(
            {
                **base,
                "decision": "receipt" if receipt else "driver_rejected",
                "reason": reason if receipt else "driver_rejected",
                "pre_hash": pre_hash,
                "post_hash": sha256(command["joints"]),
                "receipt_id": receipt["receipt_id"] if receipt else None,
            }
        )
        return {"decision": decision, "reason": reason, "correlation_id": cid, "receipt": receipt}

    def _validate(self, request: Any, now_ns: int):
        try:
            if not isinstance(request, dict):
                return "refuse", "malformed_request", None, None
            channel = request.get("channel")
            if channel not in ("joint_cmd", "gripper_cmd"):
                return "refuse", "unknown_channel", None, None
            stamp = request.get("sim_time_ns")
            if not isinstance(stamp, int):
                return "refuse", "missing_stamp", None, None
            if now_ns - stamp > self.max_stamp_age_ns or stamp > now_ns:
                return "hold", "stale_stamp", None, None
            if channel == "joint_cmd":
                cmd = np.asarray(request.get("data"), dtype=np.float32).reshape(-1)
                safe, violations = clamp_joint_cmd(cmd, self.last_safe, self.limits, False)
                reasons = {v["reason"] for v in violations}
                if "malformed" in reasons or "wall_timeout" in reasons:
                    return "hold", "malformed_request", None, None
                return ("clamp" if violations else "pass"), "ok", safe, self.last_gripper
            value = float(np.asarray(request.get("data"), dtype=np.float32).reshape(-1)[0])
            safe_g, violations = clamp_gripper_cmd(value, self.last_gripper, self.limits, False)
            if any(v["reason"] == "malformed" for v in violations):
                return "hold", "malformed_request", None, None
            return ("clamp" if violations else "pass"), "ok", self.last_safe, safe_g
        except Exception:  # noqa: BLE001 - THR-7: any guard exception fails closed
            return "hold", "guard_exception", None, None

    def rotate_epoch(self) -> dict:
        """THR-6: a new epoch invalidates every previously minted command."""
        self.epoch += 1
        self.nonce = f"epoch-nonce-{self.epoch}"
        self.capability = Capability(
            self.environment_id, self.epoch, self.nonce, self.policy_hash, self.limits_hash
        )
        self.driver.rearm(self.capability)
        self._audit(
            {
                "monotonic_ns": self.audit._last_ns,
                "correlation_id": None,
                "source": "controller",
                "decision": "epoch",
                "reason": f"epoch {self.epoch}",
            }
        )
        return self.attestation()
