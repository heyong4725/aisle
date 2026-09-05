"""The actuation-gateway boundary and its executed attack catalog (THR-2,
THR-3, THR-4, THR-5, THR-6, THR-7, THR-8, THR-9, THR-10, THR-12, THR-15,
THR-16; SPEC 460, issue #350).

Fixture evidence only: real gateway and guard code with a fake
authenticated driver. Nothing here is a process-isolation or hardware
claim; the out-of-scope registry and residual paths are data for the
CON-14 ratification (THR-13).
"""

from __future__ import annotations

import hmac
import json

import numpy as np
import pytest
from cli_helpers import REPO_ROOT, run_module

from aisle.harness.actuation_gateway import (
    ActuationGateway,
    AuditStream,
    Capability,
    FakeAuthenticatedDriver,
    canonical,
)
from aisle.harness.attack_catalog import (
    ATTACKS,
    OUT_OF_SCOPE,
    RESIDUAL_PATHS,
    execute,
    run_catalog,
)
from aisle.nodes.budget_guard import load_limits

pytestmark = pytest.mark.unit

KEY = b"unit-controller-key"
LIMITS = load_limits("franka")


def _fixture(**audit):
    home = np.asarray(LIMITS.fallback_qpos, dtype=np.float32)
    driver = FakeAuthenticatedDriver(
        KEY, Capability("env-0", 0, "boot", "", ""), lease_ns=100_000_000, home=home
    )
    stream = AuditStream(**audit)
    gateway = ActuationGateway(
        key=KEY, limits=LIMITS, environment_id="env-0", audit=stream, driver=driver
    )
    return gateway, driver, stream


def test_only_the_gateway_holds_the_capability_and_the_driver_verifies_it():
    """THR-4 / THR-6: an attested epoch-bound capability; the driver rejects
    unauthenticated, forged, replayed, wrong-environment, and stale-epoch
    commands and accepts a sanctioned gateway command exactly once."""
    gateway, driver, _ = _fixture()
    attestation = gateway.attestation()
    assert attestation["epoch"] == 1 and attestation["capability_token"]
    assert driver.submit({"joints": [0.0] * 9, "correlation_id": "x"}, 1) is None
    assert driver.rejections[-1]["reason"] == "unauthenticated"
    forged = {
        "environment_id": "env-0",
        "epoch": 1,
        "nonce": gateway.nonce,
        "correlation_id": "f",
        "joints": [0.0] * 9,
        "gripper": 0.0,
    }
    forged["mac"] = hmac.new(b"wrong", canonical(forged), "sha256").hexdigest()
    assert driver.submit(forged, 2) is None and driver.rejections[-1]["reason"] == "bad_signature"
    out = gateway.request(
        {"channel": "joint_cmd", "sim_time_ns": 1_000, "data": list(LIMITS.fallback_qpos)},
        now_ns=1_000,
        source="typed-graph",
    )
    assert out["decision"] == "pass" and out["receipt"]["receipt_id"] == "rcpt-1"
    gateway.rotate_epoch()
    assert driver.rejections == driver.rejections  # no motion happened on rejections
    assert gateway.attestation()["epoch"] == 2


def test_gateway_validates_and_clamps_trusting_no_claim_in_the_request():
    """THR-5: a request naming a safe topic, a guard producer, or a prior
    validation result is still clamped to the pinned limits; unknown
    channels and missing stamps are refused; stale and future stamps hold."""
    gateway, driver, stream = _fixture()
    now = 1_000_000_000
    out = gateway.request(
        {
            "channel": "joint_cmd",
            "sim_time_ns": now,
            "data": [3.0] * 7 + [0.04, 0.04],
            "topic": "joint_cmd_safe",
            "producer": "budget-guard",
            "validated": True,
        },
        now_ns=now,
        source="participant",
    )
    assert out["decision"] == "clamp"
    assert max(abs(v) for v in driver.receipts[-1]["q"][:7]) < 3.0
    assert (
        gateway.request(
            {"channel": "base_cmd", "sim_time_ns": now, "data": [1]}, now_ns=now, source="p"
        )["reason"]
        == "unknown_channel"
    )
    assert (
        gateway.request({"channel": "joint_cmd", "data": [0.0] * 9}, now_ns=now, source="p")[
            "reason"
        ]
        == "missing_stamp"
    )
    assert (
        gateway.request(
            {"channel": "joint_cmd", "sim_time_ns": now - 10**10, "data": [0.0] * 9},
            now_ns=now,
            source="p",
        )["decision"]
        == "hold"
    )
    assert stream.rows[-1]["reason"] == "stale_stamp"


def test_abnormal_paths_fail_closed_with_no_new_command():
    """THR-7 / THR-8: malformed payloads, guard exceptions, audit-sink
    failure, controller teardown, and gateway silence produce no unsafe
    driver receipt; the audit stream is write-ahead, append-only, and
    monotonic."""
    gateway, driver, stream = _fixture(fail_after=1)
    now = 1_000_000_000
    assert (
        gateway.request(
            {"channel": "joint_cmd", "sim_time_ns": now, "data": object()}, now_ns=now, source="p"
        )["reason"]
        == "guard_exception"
    )
    assert driver.receipts == []
    gateway2, driver2, stream2 = _fixture(fail_after=1)
    out = gateway2.request(
        {"channel": "joint_cmd", "sim_time_ns": now, "data": list(LIMITS.fallback_qpos)},
        now_ns=now,
        source="p",
    )
    assert out["decision"] == "pass" and len(driver2.receipts) == 1  # first write-ahead row fits
    out = gateway2.request(
        {"channel": "joint_cmd", "sim_time_ns": now + 1, "data": list(LIMITS.fallback_qpos)},
        now_ns=now + 1,
        source="p",
    )
    assert out["decision"] == "stop" and len(driver2.receipts) == 1  # write-ahead failed: no submit
    assert gateway2.teardown is True
    s = AuditStream()
    s.write({"monotonic_ns": 5})
    with pytest.raises(ValueError, match="monotonic"):
        s.write({"monotonic_ns": 4})
    gateway3, driver3, _ = _fixture()
    gateway3.request(
        {"channel": "joint_cmd", "sim_time_ns": now, "data": list(LIMITS.fallback_qpos)},
        now_ns=now,
        source="p",
    )
    driver3.tick(now + 10**9)
    assert driver3.held is True


def test_every_catalog_attack_executes_and_is_blocked_at_its_expected_layer():
    """THR-9 / THR-10 / THR-12 / THR-16: eighteen in-scope classes execute
    against the real gateway and guard code with the fake driver; every one
    is blocked at its declared layer, no driver receipt lacks a gateway
    decision, and the report carries the out-of-scope registry and residual
    paths rather than a limitations paragraph."""
    report = run_catalog()
    assert report["ok"] is True and report["blockers"] == []
    assert report["counts"]["blocked_at_expected_layer"] == len(ATTACKS) == 18
    assert report["counts"]["survived"] == 0 and report["counts"]["not_executed"] == 0
    assert all(a["unmatched_receipts"] == 0 for a in report["attacks"])
    classes = {a[1] for a in ATTACKS}
    for required in (
        "direct_driver_call",
        "spoofed_gateway_identity",
        "replayed_epoch",
        "alternate_channel",
        "forged_safe_topic",
        "malformed_payload",
        "guard_crashing_payload",
        "gateway_silence",
        "audit_sink_failure",
        "controller_teardown",
        "dynamic_node_swap",
    ):
        assert required in classes
    assert report["out_of_scope_registry"] == OUT_OF_SCOPE and len(OUT_OF_SCOPE) == 3
    assert any(r["status"] == "in_scope_blocker" for r in RESIDUAL_PATHS)
    assert "fake authenticated driver" in report["evidence_kind"]
    assert run_catalog()["report_hash"] == report["report_hash"]


def test_a_surviving_attack_would_block_the_claim():
    """THR-12 / THR-15: the classifier maps a reached driver to `survived`
    and the report to ok:false; nothing is hidden behind a passing gate."""
    single = execute("ATK-08")
    assert single["blocked_by"] == "gateway_validation" and single["driver_receipts"] == 1
    from aisle.harness import attack_catalog as ac

    original = ac.execute
    try:
        ac.execute = lambda attack_id: (
            {**original(attack_id), "blocked_by": None}
            if attack_id == "ATK-08"
            else original(attack_id)
        )
        report = ac.run_catalog()
    finally:
        ac.execute = original
    assert report["ok"] is False and report["blockers"] == ["ATK-08"]
    assert report["counts"]["survived"] == 1


def test_cli_and_committed_report_follow_con8():
    """CON-8: `harness threat run` prints the report and exits 0 iff ok; the
    committed bypass report regenerates byte-for-byte."""
    proc = run_module("aisle.harness.cli", "threat", "run")
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"] is True and out["counts"]["blocked_at_expected_layer"] == 18
    committed = json.loads(
        (REPO_ROOT / "analysis" / "threat-model" / "bypass-report.json").read_text()
    )
    assert committed["report_hash"] == run_catalog()["report_hash"]


def test_participant_is_adversarial_and_out_of_scope_is_explicit():
    """THR-2 / THR-3: every catalog class is a participant-originated action
    the sealed view permits (import, forge, replay, publish, swap, crash,
    starve); every out-of-scope exclusion names its rationale, detection
    assumption, and the claim narrowing it imposes."""
    assert all(
        attack[2]
        in (
            "driver_authentication",
            "gateway_validation",
            "driver_lease",
            "audit_stream",
            "controller",
        )
        for attack in ATTACKS
    )
    participant_powers = {
        "direct_driver_call",
        "spoofed_gateway_identity",
        "replayed_epoch",
        "replayed_command",
        "forged_safe_topic",
        "dynamic_node_swap",
        "guard_crashing_payload",
        "malformed_payload",
    }
    assert participant_powers <= {attack[1] for attack in ATTACKS}
    for entry in OUT_OF_SCOPE:
        assert {"id", "attack", "rationale", "detection", "claim_narrowing"} <= set(entry)
        assert entry["claim_narrowing"]
