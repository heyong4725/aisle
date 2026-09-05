"""Hardware-independent SO-101 phase artifacts (HWP-1, HWP-2, HWP-3, HWP-5,
HWP-6, HWP-7, HWP-8, HWP-9, HWP-11, HWP-13, HWP-14, HWP-18, HWP-20; SPEC
520, issue #356).

Nothing here is physical evidence. The tests pin that placeholder or
simulated identities block physical execution, that every realized-hardware
field stays hardware_pending in the report, that doubles reproduce the
fixture scenarios deterministically, and that the fourteen-step checklist
cannot pass out of order.
"""

from __future__ import annotations

import copy
import json

import pytest
from cli_helpers import REPO_ROOT, run_module

from aisle.hardware.adapters import SCENARIOS, run_scenario
from aisle.harness import hardware_phase as hp

pytestmark = pytest.mark.unit

TEMPLATES = REPO_ROOT / "docs" / "hardware" / "so101"


def _station(pending: bool = False) -> dict:
    hpv = hp.HARDWARE_PENDING
    device = {
        "manufacturer": "TheRobotStudio",
        "model": "SO-101",
        "revision": "v1.1",
        "serial": "SN-0001",
    }
    return {
        "station_id": "station-a",
        "arm": hpv if pending else device,
        "gripper": device,
        "actuators": [{"id": f"m{i}", "model": "STS3215", "serial": f"A{i}"} for i in range(6)],
        "cameras": [
            {"role": "overhead", "model": "RealSense D435", "serial": "C1", "mode": "640x480@30"}
        ],
        "firmware_hashes": {"m0": "sha256:aa"},
        "bus_usb_topology": "usb-1.2",
        "power_supply": "12V 5A",
        "estop_hardware": "mushroom NC loop",
        "host": {"kernel": "6.8", "arch": "x86_64", "clock": "CLOCK_MONOTONIC"},
        "runtime_hashes": {"lock": "sha256:bb"},
        "workspace_fixture_ids": ["tray-fixture-v1"],
        "task_object_ids": ["box-amoxicillin"],
        "responsible_operators": ["operator-1"],
    }


def test_station_manifest_blocks_placeholders_and_marks_pending(tmp_path):
    """HWP-1: a fully pinned manifest allows physical execution; a
    hardware_pending device blocks it; a placeholder or simulated identity
    is refused outright."""
    assert hp.validate_station_manifest(_station())["physical_execution_allowed"] is True
    pending = hp.validate_station_manifest(_station(pending=True))
    assert pending["physical_execution_allowed"] is False and pending[
        "hardware_pending_fields"
    ] == ["arm"]
    fake = _station()
    fake["arm"]["serial"] = "simulated-0"
    with pytest.raises(hp.HardwarePhaseError, match="placeholder identity"):
        hp.validate_station_manifest(fake)
    template = json.loads((TEMPLATES / "station-manifest.template.json").read_text())
    template.pop("_instructions", None)
    template.pop("schema_version", None)
    assert hp.validate_station_manifest(template)["physical_execution_allowed"] is False


def _motor(station="station-a") -> dict:
    joints = {
        f"j{i}": {
            "zero": 0.0,
            "direction": 1,
            "range": [-1.5, 1.5],
            "encoder_conversion": 4096,
            "backlash_repeatability": 0.002,
            "safe_speed": 1.0,
            "safe_current": 2.0,
            "safe_temperature": 60,
        }
        for i in range(6)
    }
    body = {
        "station_id": station,
        "version": 1,
        "date": "2026-09-01",
        "method": "stop-to-stop x5",
        "instrument_identity": "dial-1",
        "operator": "operator-1",
        "home_pose": [0.0] * 6,
        "gripper_mapping": {"open": 0.0, "contact": 0.6, "closed": 1.0},
        "max_payload": 0.3,
        "tool_geometry": {"tcp_offset_m": 0.1},
        "joints": joints,
        "expires": "2026-10-01",
    }
    return {**body, "signature": "sig", "artifact_hash": hp.content_hash(body)}


def test_motor_calibration_refuses_before_torque_enable():
    """HWP-2: wrong station, expired, unsigned, hash-mismatched, or
    out-of-range artifacts refuse; a valid one allows torque enable."""
    assert hp.validate_motor_calibration(_motor(), station_id="station-a", today="2026-09-05")[
        "torque_enable_allowed"
    ]
    with pytest.raises(hp.HardwarePhaseError, match="another station"):
        hp.validate_motor_calibration(_motor(), station_id="station-b", today="2026-09-05")
    with pytest.raises(hp.HardwarePhaseError, match="expired"):
        hp.validate_motor_calibration(_motor(), station_id="station-a", today="2026-12-01")
    unsigned = _motor()
    unsigned["signature"] = ""
    with pytest.raises(hp.HardwarePhaseError, match="unsigned"):
        hp.validate_motor_calibration(unsigned, station_id="station-a", today="2026-09-05")
    tampered = _motor()
    tampered["joints"]["j0"]["zero"] = 0.1
    with pytest.raises(hp.HardwarePhaseError, match="hash mismatch"):
        hp.validate_motor_calibration(tampered, station_id="station-a", today="2026-09-05")
    out_of_range = _motor()
    out_of_range["joints"]["j0"]["zero"] = 2.0
    out_of_range["artifact_hash"] = hp.content_hash(
        {k: v for k, v in out_of_range.items() if k not in ("artifact_hash", "signature")}
    )
    with pytest.raises(hp.HardwarePhaseError, match="outside its measured range"):
        hp.validate_motor_calibration(out_of_range, station_id="station-a", today="2026-09-05")


def test_workspace_calibration_requires_disjoint_split_and_measured_frames():
    """HWP-3: calibration and evaluation samples are disjoint; frames are
    measured, never placeholders."""
    artifact = {
        "station_id": "station-a",
        "version": 1,
        "camera_intrinsics": {"fx": 600},
        "overhead_extrinsics": [[1, 0, 0, 0]],
        "wrist_extrinsics": [[1, 0, 0, 0]],
        "depth_scale": 0.001,
        "frames": {"base": "measured-2026-09-01"},
        "target_regions": {"tray": [[0, 0], [0.2, 0.2]]},
        "timestamp_latency_mapping": {"offset_ns": 1000},
        "measurement_uncertainty": {"mm": 0.5},
        "repeatability": {"mm": 0.2},
        "split": {"calibration": [1, 2, 3], "evaluation": [4, 5]},
        "instrument_identities": ["board-1"],
        "operator": "operator-1",
    }
    assert hp.validate_workspace_calibration(artifact)["artifact_hash"].startswith("sha256:")
    overlap = copy.deepcopy(artifact)
    overlap["split"]["evaluation"] = [3, 4]
    with pytest.raises(hp.HardwarePhaseError, match="disjoint"):
        hp.validate_workspace_calibration(overlap)
    fake = copy.deepcopy(artifact)
    fake["frames"]["base"] = "placeholder"
    with pytest.raises(hp.HardwarePhaseError, match="placeholders"):
        hp.validate_workspace_calibration(fake)


def test_interface_map_requires_parity_or_declared_dependency():
    """HWP-5: one content-addressed driver and primitives; every parity
    dimension identical across treatments unless the monolithic surface is
    declared dependency_pending."""
    typed = {dim: f"{dim}-v1" for dim in hp.INTERFACE_DIMENSIONS}
    base = {
        "driver_hash": "sha256:d",
        "camera_adapter_hashes": {},
        "primitive_hashes": {},
        "gateway_hash": "sha256:g",
        "limits_hash": "sha256:l",
        "watchdog_lease_hash": "sha256:w",
        "containment_hash": "sha256:c",
    }
    assert hp.validate_interface_map(
        {**base, "treatments": {"typed": typed, "monolithic": {"status": "dependency_pending"}}}
    )
    assert hp.validate_interface_map(
        {**base, "treatments": {"typed": typed, "monolithic": dict(typed)}}
    )
    broken = {**base, "treatments": {"typed": typed, "monolithic": {**typed, "authority": "wider"}}}
    with pytest.raises(hp.HardwarePhaseError, match="parity failed on authority"):
        hp.validate_interface_map(broken)


def test_telemetry_rows_bind_one_receipt_and_carry_clock_domains():
    """HWP-6 / HWP-7: every row carries the ids, stamps, clock domain, and
    alignment uncertainty; a held proposal has no receipt; receipts and host
    timestamps never duplicate or regress."""
    rows = run_scenario("command_receipt_correlation")["rows"]
    summary = hp.validate_telemetry_stream(rows)
    assert (
        summary["rows"] == 12
        and summary["receipts"] == 12
        and summary["evidence_kind"] == "loopback"
    )
    bad = copy.deepcopy(rows)
    bad[0]["gateway_decision"], bad[0]["receipt_id"] = "hold", "rcpt-x"
    with pytest.raises(hp.HardwarePhaseError, match="cannot carry a driver receipt"):
        hp.validate_telemetry_stream(bad)
    dup = copy.deepcopy(rows)
    dup[1]["receipt_id"] = dup[0]["receipt_id"]
    with pytest.raises(hp.HardwarePhaseError, match="two proposals"):
        hp.validate_telemetry_stream(dup)


def test_doubles_reproduce_every_fixture_scenario_deterministically():
    """HWP-8: injectable bus, camera, clock, estop, and sink doubles cover
    the required scenarios with the expected observable and are seeded."""
    expect = {
        "actuator_lag": lambda o: o["receipts"] == 12,
        "saturation": lambda o: o["receipts"] == 12,
        "stale_state": lambda o: o["receipts"] == 12,
        "missing_state": lambda o: o["max_current_a"] == 0.0,
        "disconnect_reconnect": lambda o: o["refusals"] == 3 and o["receipts"] == 9,
        "dropped_frames": lambda o: o["frames_dropped"] == 4,
        "clock_skew": lambda o: not o["device_clock_regressed"],
        "clock_reset": lambda o: o["device_clock_regressed"],
        "calibration_mismatch": lambda o: o["holds"] == 12 and o["halted"],
        "overcurrent": lambda o: o["halted"] and o["max_current_a"] > 3.0,
        "overtemperature": lambda o: o["halted"] and o["max_temperature_c"] > 70.0,
        "lease_expiry": lambda o: o["lease_expired_ticks"] > 0 and o["halted"],
        "estop": lambda o: o["holds"] == 8 and o["halted"],
        "evidence_sink_failure": lambda o: o["sink_failures"] == 6,
        "command_receipt_correlation": lambda o: o["receipts"] == 12,
    }
    assert set(expect) == set(SCENARIOS)
    for name, check in expect.items():
        result = run_scenario(name, seed=3)
        assert result["evidence_kind"] == "loopback"
        assert check(result["observations"]), (name, result["observations"])
        assert run_scenario(name, seed=3) == result


def test_safety_case_intervention_scorer_and_protocol_validators():
    """HWP-9 / HWP-11 / HWP-13 / HWP-14: the committed templates validate
    structurally where they are complete, an unsigned safety case blocks,
    interventions need stamps and consequences, the scorer refuses simulator
    inputs, and the protocol refuses oracle perception."""
    case = json.loads((TEMPLATES / "safety-case.template.json").read_text())
    case.pop("schema_version")
    with pytest.raises(hp.HardwarePhaseError, match="unsigned"):
        hp.validate_safety_case(case)
    case["signatures"] = [{"role": "safety_operator", "signed_at": "2026-09-05T00:00:00Z"}]
    assert hp.validate_safety_case(case)["preflight_all_passed"] is False
    protocol = json.loads((TEMPLATES / "reset-intervention-protocol.json").read_text())
    records = [
        {
            "kind": "reset",
            "start_ns": 10,
            "end_ns": 20,
            "actor": "trial_conductor",
            "reason": "object fell",
            "scoring_consequence": "attempt counted",
        }
    ]
    assert hp.validate_intervention_records(records, protocol)["by_kind"]["reset"] == 1
    with pytest.raises(hp.HardwarePhaseError, match="ends before"):
        hp.validate_intervention_records([{**records[0], "end_ns": 5}], protocol)
    scorer = json.loads((TEMPLATES / "scorer-contract.json").read_text())
    scorer.pop("schema_version")
    assert hp.validate_scorer_contract(scorer)["fidelity"] == hp.HARDWARE_PENDING
    with pytest.raises(hp.HardwarePhaseError, match="simulator state"):
        hp.validate_scorer_contract({**scorer, "sensor_inputs": ["oracle_state"]})
    physical = json.loads((TEMPLATES / "physical-protocol.template.json").read_text())
    physical.pop("schema_version")
    assert hp.validate_physical_protocol(physical)["frozen"] is False
    with pytest.raises(hp.HardwarePhaseError, match="non-oracle"):
        hp.validate_physical_protocol({**physical, "perception": "oracle pose"})


def test_checklist_is_ordered_and_report_stays_hardware_pending(tmp_path):
    """HWP-20 / HWP-18: fourteen steps all pending by default; a later step
    cannot pass before earlier ones; the report keeps every realized-hardware
    field hardware_pending and never merges loopback rows with physical rows."""
    checklist = hp.execution_checklist()
    assert len(checklist["steps"]) == 14 and not checklist["all_passed"]
    assert checklist["blocker"].startswith("no SO-101 station")
    with pytest.raises(hp.HardwarePhaseError, match="cannot pass before"):
        hp.execution_checklist({3: "passed"})
    assert hp.execution_checklist({1: "passed", 2: "passed"})["steps"][1]["status"] == "passed"
    rows = run_scenario("command_receipt_correlation")["rows"]
    report = hp.hardware_report(
        station=None,
        motor_calibration=None,
        workspace_calibration=None,
        telemetry=rows,
        interventions=None,
    )
    for key in (
        "station",
        "scorer_fidelity",
        "trial_flow",
        "outcomes",
        "safety_exposure",
        "sim_to_hardware_deltas",
    ):
        assert report[key] == hp.HARDWARE_PENDING, key
    assert report["telemetry_reconciliation"]["physical"] == hp.HARDWARE_PENDING
    assert report["telemetry_reconciliation"]["simulated_or_loopback"]["rows"] == 12
    assert report["telemetry_reconciliation"]["merged"] is False
    proc = run_module("aisle.harness.cli", "hardware", "report")
    assert (
        proc.returncode == 0
        and json.loads(proc.stdout)["execution_checklist"]["all_passed"] is False
    )
    proc = run_module("aisle.harness.cli", "hardware", "dry-run", "--seed", "1")
    out = json.loads(proc.stdout)
    assert (
        proc.returncode == 0
        and out["evidence_kind"] == "loopback"
        and set(out["scenarios"]) == set(SCENARIOS)
    )
