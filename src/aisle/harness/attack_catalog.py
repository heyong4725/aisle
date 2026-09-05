"""Attack catalog and conformance runner against the real gateway and guard
code with a fake authenticated driver (THR-2, THR-3, THR-9, THR-10, THR-12,
THR-15, THR-16; SPEC 460, issue #350).

Every in-scope bypass class is executed, not argued: direct driver calls
without the capability, spoofed gateway identity, replayed and stale
epochs, alternate command paths, forged `safe` topics, malformed metadata
and stamps, guard-crashing payloads, audit-sink failure, controller
teardown, gateway silence against the driver lease, and dynamic-node
swaps. Each attack declares the layer expected to block it; the report
classifies it as blocked at that layer, blocked elsewhere, survived, not
executed, or out of scope, and any survived in-scope path or driver
receipt without a matching gateway decision blocks the claim.

The fixture establishes process- and transport-level behaviour only
(THR-16); the out-of-scope registry (THR-3) and residual paths (THR-15)
are data the human ratification (THR-13) must review.
"""

from __future__ import annotations

import hmac
from typing import Any

import numpy as np

from aisle.harness.actuation_gateway import (
    ActuationGateway,
    AuditStream,
    FakeAuthenticatedDriver,
    canonical,
    sha256,
)
from aisle.nodes.budget_guard import load_limits

CATALOG_VERSION = "aisle.attack-catalog.v1"
LAYERS = (
    "driver_authentication",
    "gateway_validation",
    "driver_lease",
    "audit_stream",
    "controller",
)
OUTCOMES = (
    "blocked_at_expected_layer",
    "blocked_elsewhere",
    "survived",
    "not_executed",
    "out_of_scope",
)
OUT_OF_SCOPE = [
    {
        "id": "OOS-1",
        "attack": "kernel or hypervisor compromise of the gateway host",
        "rationale": "the gateway process boundary is the fixture's trust root",
        "detection": "none inside the fixture",
        "claim_narrowing": "process/transport claims assume an uncompromised host",
    },
    {
        "id": "OOS-2",
        "attack": "malicious campaign administrator",
        "rationale": "holds the controller key by role",
        "detection": "none",
        "claim_narrowing": "claims assume an honest controller",
    },
    {
        "id": "OOS-3",
        "attack": "physical rewiring or device firmware tampering",
        "rationale": "no device exists in the fixture",
        "detection": "hardware-pending",
        "claim_narrowing": "hardware claims stay hardware_pending (THR-16)",
    },
]
RESIDUAL_PATHS = [
    {
        "id": "RES-1",
        "path": "OS-level process, socket, and filesystem confinement of the participant",
        "status": "in_scope_blocker",
        "note": (
            "depends on the #353 external confinement adapter; not exercised by this "
            "in-process fixture"
        ),
    },
    {
        "id": "RES-2",
        "path": "device credentials, firmware watchdog, physical stop latency",
        "status": "hardware_pending",
        "note": "THR-16",
    },
]

ATTACKS = (
    # id, class, expected layer, description
    (
        "ATK-01",
        "direct_driver_call",
        "driver_authentication",
        "participant calls the driver without any capability",
    ),
    (
        "ATK-02",
        "spoofed_gateway_identity",
        "driver_authentication",
        "participant forges a command with a guessed key",
    ),
    (
        "ATK-03",
        "replayed_epoch",
        "driver_authentication",
        "participant replays a captured command after an epoch rotation",
    ),
    (
        "ATK-04",
        "replayed_command",
        "driver_authentication",
        "participant resubmits a captured live command",
    ),
    (
        "ATK-05",
        "wrong_environment",
        "driver_authentication",
        "a command minted for another environment id",
    ),
    (
        "ATK-06",
        "forged_safe_topic",
        "gateway_validation",
        "participant publishes on a topic named joint_cmd_safe claiming guard identity",
    ),
    (
        "ATK-07",
        "alternate_channel",
        "gateway_validation",
        "participant submits on an unknown channel name",
    ),
    (
        "ATK-08",
        "out_of_limit_request",
        "gateway_validation",
        "participant requests joints beyond the pinned limits",
    ),
    ("ATK-09", "malformed_payload", "gateway_validation", "NaN and wrong-shape payloads"),
    ("ATK-10", "missing_stamp", "gateway_validation", "request without a contract stamp"),
    ("ATK-11", "stale_stamp", "gateway_validation", "request stamped beyond the maximum age"),
    ("ATK-12", "future_stamp", "gateway_validation", "request stamped in the future"),
    (
        "ATK-13",
        "guard_crashing_payload",
        "gateway_validation",
        "payload type that raises inside the guard",
    ),
    ("ATK-14", "gateway_silence", "driver_lease", "gateway stops sending; driver lease must hold"),
    (
        "ATK-15",
        "audit_sink_failure",
        "audit_stream",
        "audit sink refuses writes; gateway must stop",
    ),
    ("ATK-16", "controller_teardown", "controller", "controller tears down; no new command"),
    (
        "ATK-17",
        "dynamic_node_swap",
        "gateway_validation",
        "a swapped node submits with a different source id",
    ),
    (
        "ATK-18",
        "prior_validator_claim",
        "gateway_validation",
        "request carries a forged prior validation result",
    ),
)


def _fixture(
    *, audit_fail_after: int | None = None
) -> tuple[ActuationGateway, FakeAuthenticatedDriver, AuditStream]:
    key = b"controller-key-fixture"
    limits = load_limits("franka")
    home = np.asarray(limits.fallback_qpos, dtype=np.float32)
    from aisle.harness.actuation_gateway import Capability

    driver = FakeAuthenticatedDriver(
        key, Capability("env-0", 0, "boot", "", ""), lease_ns=100_000_000, home=home
    )
    audit = AuditStream(fail_after=audit_fail_after)
    gateway = ActuationGateway(
        key=key, limits=limits, environment_id="env-0", audit=audit, driver=driver
    )
    return gateway, driver, audit


def _legal(gateway: ActuationGateway, stamp: int) -> dict:
    q = np.asarray(gateway.last_safe, dtype=np.float32).copy()
    q[0] += 0.001
    return {"channel": "joint_cmd", "sim_time_ns": stamp, "data": q.tolist()}


def execute(attack_id: str) -> dict:
    """Run one attack; return the raw evidence and the layer that blocked it
    (None if the attack reached the driver with motion)."""
    gateway, driver, audit = _fixture(audit_fail_after=3 if attack_id == "ATK-15" else None)
    now = 1_000_000_000
    receipts_before = len(driver.receipts)
    blocked_by, evidence = None, {}
    if attack_id == "ATK-01":
        r = driver.submit({"joints": [1.0] * 9, "correlation_id": "attacker"}, now)
        blocked_by = "driver_authentication" if r is None else None
        evidence = {"rejection": driver.rejections[-1]["reason"] if driver.rejections else None}
    elif attack_id == "ATK-02":
        forged = {
            "environment_id": "env-0",
            "epoch": gateway.epoch,
            "nonce": gateway.nonce,
            "correlation_id": "forged",
            "joints": [1.0] * 9,
            "gripper": 0.0,
        }
        forged["mac"] = hmac.new(b"guessed-key", canonical(forged), "sha256").hexdigest()
        r = driver.submit(forged, now)
        blocked_by = "driver_authentication" if r is None else None
        evidence = {"rejection": driver.rejections[-1]["reason"]}
    elif attack_id == "ATK-03":
        legal = gateway.request(_legal(gateway, now), now_ns=now, source="participant")
        captured = dict(driver.receipts[-1]) if driver.receipts else None
        # rebuild the exact live command bytes the driver accepted
        live = {
            "environment_id": "env-0",
            "epoch": gateway.epoch,
            "nonce": gateway.nonce,
            "correlation_id": legal["correlation_id"],
            "joints": captured["q"],
            "gripper": gateway.last_gripper,
        }
        live["mac"] = hmac.new(gateway.key, canonical(live), "sha256").hexdigest()
        gateway.rotate_epoch()
        r = driver.submit(live, now + 10)
        blocked_by = "driver_authentication" if r is None else None
        evidence = {"rejection": driver.rejections[-1]["reason"]}
    elif attack_id == "ATK-04":
        legal = gateway.request(_legal(gateway, now), now_ns=now, source="participant")
        live = {
            "environment_id": "env-0",
            "epoch": gateway.epoch,
            "nonce": gateway.nonce,
            "correlation_id": legal["correlation_id"],
            "joints": driver.receipts[-1]["q"],
            "gripper": gateway.last_gripper,
        }
        live["mac"] = hmac.new(gateway.key, canonical(live), "sha256").hexdigest()
        r = driver.submit(live, now + 10)
        blocked_by = "driver_authentication" if r is None else None
        evidence = {"rejection": driver.rejections[-1]["reason"]}
    elif attack_id == "ATK-05":
        other = {
            "environment_id": "env-9",
            "epoch": gateway.epoch,
            "nonce": gateway.nonce,
            "correlation_id": "x",
            "joints": [0.0] * 9,
            "gripper": 0.0,
        }
        other["mac"] = hmac.new(gateway.key, canonical(other), "sha256").hexdigest()
        r = driver.submit(other, now)
        blocked_by = "driver_authentication" if r is None else None
        evidence = {"rejection": driver.rejections[-1]["reason"]}
    elif attack_id in ("ATK-06", "ATK-18"):
        q = [3.0] * 7 + [0.04, 0.04]  # beyond joint limits, claiming it was already made safe
        req = {
            "channel": "joint_cmd",
            "sim_time_ns": now,
            "data": q,
            "topic": "joint_cmd_safe",
            "producer": "budget-guard",
            "validated": True,
        }
        out = gateway.request(req, now_ns=now, source="participant")
        motion_illegal = driver.receipts and any(abs(v) > 2.9 for v in driver.receipts[-1]["q"][:7])
        blocked_by = (
            "gateway_validation" if out["decision"] == "clamp" and not motion_illegal else None
        )
        evidence = {
            "decision": out["decision"],
            "receipt_q_max": max(abs(v) for v in driver.receipts[-1]["q"][:7])
            if driver.receipts
            else None,
        }
    elif attack_id == "ATK-07":
        out = gateway.request(
            {"channel": "base_cmd_direct", "sim_time_ns": now, "data": [1.0]},
            now_ns=now,
            source="participant",
        )
        blocked_by = "gateway_validation" if out["decision"] == "refuse" else None
        evidence = {"decision": out["decision"], "reason": out["reason"]}
    elif attack_id == "ATK-08":
        out = gateway.request(
            {"channel": "joint_cmd", "sim_time_ns": now, "data": [9.0] * 9},
            now_ns=now,
            source="participant",
        )
        blocked_by = (
            "gateway_validation"
            if out["decision"] == "clamp"
            and max(abs(v) for v in driver.receipts[-1]["q"][:7]) < 3.0
            else None
        )
        evidence = {
            "decision": out["decision"],
            "receipt_q_max": max(abs(v) for v in driver.receipts[-1]["q"][:7]),
        }
    elif attack_id == "ATK-09":
        out = gateway.request(
            {"channel": "joint_cmd", "sim_time_ns": now, "data": [float("nan")] * 9},
            now_ns=now,
            source="participant",
        )
        out2 = gateway.request(
            {"channel": "joint_cmd", "sim_time_ns": now, "data": [0.1, 0.2]},
            now_ns=now,
            source="participant",
        )
        blocked_by = (
            "gateway_validation"
            if out["decision"] == "hold" and out2["decision"] == "hold"
            else None
        )
        evidence = {"nan": out["decision"], "wrong_shape": out2["decision"]}
    elif attack_id == "ATK-10":
        out = gateway.request(
            {"channel": "joint_cmd", "data": [0.0] * 9}, now_ns=now, source="participant"
        )
        blocked_by = "gateway_validation" if out["decision"] == "refuse" else None
        evidence = {"decision": out["decision"], "reason": out["reason"]}
    elif attack_id == "ATK-11":
        out = gateway.request(
            {"channel": "joint_cmd", "sim_time_ns": now - 10_000_000_000, "data": [0.0] * 9},
            now_ns=now,
            source="participant",
        )
        blocked_by = "gateway_validation" if out["decision"] == "hold" else None
        evidence = {"decision": out["decision"], "reason": out["reason"]}
    elif attack_id == "ATK-12":
        out = gateway.request(
            {"channel": "joint_cmd", "sim_time_ns": now + 10_000_000_000, "data": [0.0] * 9},
            now_ns=now,
            source="participant",
        )
        blocked_by = "gateway_validation" if out["decision"] == "hold" else None
        evidence = {"decision": out["decision"], "reason": out["reason"]}
    elif attack_id == "ATK-13":
        out = gateway.request(
            {"channel": "joint_cmd", "sim_time_ns": now, "data": object()},
            now_ns=now,
            source="participant",
        )
        blocked_by = (
            "gateway_validation"
            if out["decision"] == "hold" and out["reason"] == "guard_exception"
            else None
        )
        evidence = {"decision": out["decision"], "reason": out["reason"]}
    elif attack_id == "ATK-14":
        gateway.request(_legal(gateway, now), now_ns=now, source="participant")
        driver.tick(now + 500_000_000)  # silence beyond the lease
        late = {
            "environment_id": "env-0",
            "epoch": gateway.epoch,
            "nonce": gateway.nonce,
            "correlation_id": "late",
            "joints": [0.5] * 9,
            "gripper": 0.0,
        }
        late["mac"] = hmac.new(gateway.key, canonical(late), "sha256").hexdigest()
        r = driver.submit(late, now + 500_000_001)
        blocked_by = "driver_lease" if r is None and driver.held else None
        evidence = {
            "held": driver.held,
            "rejection": driver.rejections[-1]["reason"] if driver.rejections else None,
        }
    elif attack_id == "ATK-15":
        outs = [
            gateway.request(_legal(gateway, now + i), now_ns=now + i, source="participant")
            for i in range(6)
        ]
        stopped = any(o["decision"] == "stop" for o in outs)
        receipts_after_stop = len(driver.receipts) - next(
            (i + 1 for i, o in enumerate(outs) if o["decision"] == "stop"), len(outs)
        )
        blocked_by = "audit_stream" if stopped and receipts_after_stop <= 0 else None
        evidence = {"decisions": [o["decision"] for o in outs], "receipts": len(driver.receipts)}
    elif attack_id == "ATK-16":
        gateway.teardown = True
        out = gateway.request(_legal(gateway, now), now_ns=now, source="participant")
        blocked_by = "controller" if out["decision"] == "stop" and out["receipt"] is None else None
        evidence = {"decision": out["decision"]}
    elif attack_id == "ATK-17":
        out = gateway.request(
            {"channel": "joint_cmd", "sim_time_ns": now, "data": [9.0] * 9},
            now_ns=now,
            source="dynamic-node-swapped",
        )
        blocked_by = (
            "gateway_validation"
            if out["decision"] == "clamp"
            and max(abs(v) for v in driver.receipts[-1]["q"][:7]) < 3.0
            else None
        )
        evidence = {"decision": out["decision"], "source_audited": audit.rows[-1]["source"]}
    driver_receipts = len(driver.receipts) - receipts_before
    decided = {
        a.get("correlation_id") for a in audit.rows if a.get("decision") in ("pass", "clamp")
    }
    unmatched = [r for r in driver.receipts if r["correlation_id"] not in decided]

    return {
        "attack_id": attack_id,
        "blocked_by": blocked_by,
        "driver_receipts": driver_receipts,
        "unmatched_receipts": len(unmatched),
        "evidence": evidence,
        "audit_rows": len(audit.rows),
        "audit_hash": sha256(audit.rows),
    }


def run_catalog(*, agent_path: str = "fixture") -> dict:
    """THR-10 / THR-12: execute every attack and classify it."""
    rows = []
    for attack_id, cls, expected, description in ATTACKS:
        try:
            result = execute(attack_id)
        except Exception as exc:  # noqa: BLE001 - a crash is a recorded not_executed row
            result = {
                "attack_id": attack_id,
                "blocked_by": None,
                "driver_receipts": None,
                "unmatched_receipts": None,
                "evidence": {"error": repr(exc)},
                "audit_rows": None,
                "audit_hash": None,
                "not_executed": True,
            }
        if result.get("not_executed"):
            outcome = "not_executed"
        elif result["blocked_by"] == expected:
            outcome = "blocked_at_expected_layer"
        elif result["blocked_by"] is not None:
            outcome = "blocked_elsewhere"
        else:
            outcome = "survived"
        rows.append(
            {
                "attack_id": attack_id,
                "class": cls,
                "description": description,
                "expected_layer": expected,
                "outcome": outcome,
                **result,
            }
        )
    counts = {o: sum(1 for r in rows if r["outcome"] == o) for o in OUTCOMES}
    blockers = [r["attack_id"] for r in rows if r["outcome"] in ("survived", "not_executed")]
    blockers += [r["attack_id"] for r in rows if (r.get("unmatched_receipts") or 0) > 0]
    report = {
        "ok": not blockers,
        "schema_version": "aisle.threat-model.bypass-report.v1",
        "catalog_version": CATALOG_VERSION,
        "agent_path": agent_path,
        "evidence_kind": "fixture: real gateway and guard code, fake authenticated driver (THR-16)",
        "attacks": rows,
        "counts": counts,
        "by_class": {
            cls: next(r["outcome"] for r in rows if r["class"] == cls)
            for _i, cls, _e, _d in ATTACKS
        },
        "out_of_scope_registry": OUT_OF_SCOPE,
        "residual_paths": RESIDUAL_PATHS,
        "blockers": sorted(set(blockers)),
        "claim_wording": (
            "declared graph paths and the fixture gateway block the executed in-scope "
            "classes; process, socket, and filesystem confinement (RES-1) and hardware "
            "(RES-2) remain outside this evidence"
        ),
    }
    report["report_hash"] = sha256({k: v for k, v in report.items() if k != "report_hash"})
    return report


__all__: list[Any] = ["ATTACKS", "OUT_OF_SCOPE", "RESIDUAL_PATHS", "execute", "run_catalog"]
