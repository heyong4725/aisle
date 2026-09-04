# ADR-58 — Hardware evidence begins at a pinned physical station

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #356.

## Context

ADR-phase6-prep supplied a loopback-tested SO-101 driver, simulation profile and
calibration template. Issue #13 established 50/50 simulated profile success.
Those are useful prerequisites, but neither is an observation of motors,
cameras, emergency stopping, physical scoring, reset labor, safety exposure or
task transfer.

## Decision

AISLE will treat the pinned physical station—not a device class name—as the
hardware evidence boundary. The station manifest binds every robot/camera
serial, firmware, bus, power, host, runtime, safety device, fixture and operator.
Measured motor, workspace, camera and clock calibration must be independently
checked from retained raw data before torque-enabled scoring.

Typed and monolithic artifacts share one driver, camera adapters, primitives,
gateway, limits, watchdog and emergency containment. A physical safety operator
owns the hardware estop and power; participant agents cannot own or reset that
authority. Every command/receipt/state, sensor frame, reset, human intervention,
scorer observation and outcome is synchronized and retained.

The physical scorer is measured against a disjoint independent audit. Trial
count comes from a pre-scoring power or precision analysis, and matched artifact
order is randomized in temporal blocks. Simulation and hardware rows stay
separate. At least one non-oracle physical success is necessary but never
sufficient for a reliability claim. A blinded live fault runs only if operation
remains headline scope and a safety review admits an allowlisted instance.

Until acquisition, all realized fields remain `hardware_pending`. Loopback,
replay and synthetic fixtures validate schemas and refusal behavior only. The
fourteen-step checklist in SPEC 520 is the exact handoff for equipment day; no
preparatory artifact may mark a physical step complete.

## Gate

SPEC 520 is implemented tests-first after this spec-change. Hardware-independent
implementation may proceed while empirical dependencies remain open, but no
torque-enabled or scored physical execution is authorized until every named
prerequisite, safety drill, calibration, scorer audit and timestamped protocol
passes. #356 remains open until real raw evidence satisfies all physical
criteria, or explicitly hardware-pending with the checklist and blocker current.
