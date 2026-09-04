# SPEC 520 — SO-101 hardware phase evidence gate

Status: PROPOSED, HUMAN-REVIEW GATED. Issue: #356. This contract supplements
the accepted ADR-phase6-prep and does not relabel its loopback tests or SO-101
simulation as physical evidence. Hardware-independent schemas, adapters,
fixtures, dry runs, safety procedure, calibration procedure, analyzer, and
checklist may be implemented before equipment arrives. Every realized-hardware
field and observation remains `hardware_pending` until measured on the pinned
station.

Physical comparison use depends on the issue #344 shared control surface, issue
#345 statistics, issue #346 selected non-oracle tasks, issue #350 actuation
boundary, issue #351 exposure ledger, issue #352 semantic-authorization
disposition, and issue #354 instrument audit. A live-fault cell additionally
depends on issues #348/#349. Passing a unit or loopback gate satisfies none of
those empirical dependencies.

## Station identity and calibration

- HWP-1: A machine-readable station manifest MUST pin manufacturer/model,
  hardware revision and serial for arm, gripper and each actuator; camera/depth
  device model, serial, lens, mount, resolution and mode; firmware/configuration
  hashes; bus and USB topology; power supply; emergency-stop hardware; host,
  kernel, architecture and clock sources; LeRobot/runtime/driver/container/lock
  hashes; workspace fixture and task-object ids; and responsible operators. An
  absent device MUST be `hardware_pending`; placeholder or simulated identities
  MUST block physical execution.
- HWP-2: A versioned motor calibration artifact MUST retain measured zero,
  direction, range, encoder conversion, backlash/repeatability, home pose,
  gripper open/close/contact mapping, maximum payload, safe speed/current/
  temperature limits, tool geometry, date, method, instrument identity and
  operator for every joint. The driver MUST refuse a missing, wrong-station,
  expired, out-of-range, incomplete, unsigned, or hash-mismatched artifact
  before torque enable.
- HWP-3: A versioned workspace/perception calibration artifact MUST retain
  camera intrinsics/distortion, overhead camera-to-base and wrist camera-to-tool
  extrinsics, depth scale/alignment where used, robot-base/workspace/tray/object
  frames, target regions, timestamp/latency mapping, measurement uncertainty,
  repeatability records, calibration/evaluation split, instrument identities,
  and operator. It MUST use the VER-8 conventions but MUST NOT use
  self-validation alone as accuracy evidence; independent held-out targets and
  poses MUST pass frozen reprojection, localization, timing and repeatability
  tolerances.
- HWP-4: The calibration procedure MUST freeze fixtures, target geometry,
  measurement tools, environmental conditions, sample counts/poses, order,
  equations, tolerances, failure/retry/expiry rules, raw-record schema and exact
  commands before station results. Raw images, joint/encoder samples,
  measurements and residuals MUST regenerate the signed artifacts and report;
  hand-entered accepted values without raw observations are invalid.

## Driver, primitives, clocks, and telemetry

- HWP-5: Typed and monolithic artifacts MUST use one content-addressed SO-101
  driver, camera adapters, robot primitives, actuation gateway, limits,
  watchdog/lease and emergency containment. A machine-readable interface map
  MUST prove identical semantic observations, actions, cadence, receipts and
  authority; representation metadata MAY differ only as declared by #344.
  Direct participant serial, camera, motor, GPIO or power access is forbidden.
- HWP-6: Every command proposal and gateway decision MUST bind a stable id to
  zero or one driver receipt and subsequent measured state. Driver telemetry
  MUST retain requested and transmitted positions/gripper command, clamp or
  refusal, joint/encoder state, current/temperature/voltage when available,
  connection and torque state, lease/watchdog state, device error, dropped/
  delayed sample counters, and process lifecycle. Missing hardware fields MUST
  be `unavailable` or `unmeasured`, never synthesized as zero.
- HWP-7: Every RGB/depth frame, robot state, command, receipt, intervention,
  scorer observation and outcome MUST carry source sequence, device timestamp
  when available, host monotonic receipt timestamp, clock-domain id and frozen
  alignment transform/uncertainty. The hardware-compatible topic field may
  retain a monotonic nanosecond value for schema parity but MUST be labeled
  `hardware_monotonic`, never described as simulator time. Regressions,
  duplicate ids, gaps beyond tolerance or unresolved cross-device alignment
  MUST fail reconciliation.
- HWP-8: Hardware-independent adapters MUST include injectable bus, camera,
  clock, estop and telemetry interfaces plus deterministic loopback/replay
  doubles. Fixtures MUST cover command/receipt correlation, actuator lag,
  saturation, stale/missing state, disconnect/reconnect, dropped frames, clock
  skew/reset, calibration mismatch, overcurrent/overtemperature, lease expiry,
  held commands, estop activation, scorer refusal and evidence-sink failure.
  These records MUST carry `hardware_dry_run`, not `hardware`.

## Safety ownership and intervention/reset protocol

- HWP-9: A station safety case and signed preflight checklist MUST name the
  physical safety operator, trial conductor and software controller; map who may
  enable torque, start/stop a trial, operate the physical emergency stop, reset
  it and restore power; and declare workspace/keep-out zones, anchoring,
  payloads, pinch/collision hazards, speed/current/temperature limits, guards,
  inspection, communications and abort criteria. Participant agents MUST NOT
  own, mask, reset or delay emergency-stop authority.
- HWP-10: Before scoring, no-load and representative-load limit checks,
  watchdog/lease timeout checks, command-silence/held-command checks, physical
  emergency-stop drills, controlled power-loss recovery and evidence-sink
  failure MUST pass at the pinned station hashes. Each drill MUST retain command
  and state traces, operator action, stop latency and final state. A loopback
  drill validates only control logic and cannot satisfy this physical gate.
- HWP-11: Reset and human-intervention protocols MUST freeze allowed behavioral
  reset steps, task/randomization setup, readiness checks, maximum attempts,
  timeout, roles, permitted tools, and scoring/exclusion consequences. Every
  human workspace entry, touch, object move, cable/power action, estop, rescue,
  reset and annotation MUST have start/end stamps, actor, reason, affected
  trial, action and outcome; hidden assistance or an unlogged reset invalidates
  the affected block.
- HWP-12: The participant process MUST receive sensor-derived perception only.
  Human setup records, target truth, fiducials used solely by the independent
  audit, scorer intermediates, calibration evaluation targets and held-out task
  assignment MUST remain controller-private. The final #352 disposition MUST
  state whether semantic authorization is deployed, unavailable or
  hardware-pending; the kinematic guard and verifier MUST NOT be credited with
  identity prevention.

## Physical scorer and study design

- HWP-13: The physical scorer MUST be frozen outside participant authority and
  consume recorded physical sensors rather than simulator state. Its success,
  failure-class and wrong-object rules, observation window, thresholds,
  refusal/missing-data behavior and output schema MUST be pre-registered. A
  separate audit source or independent annotated measurement MUST estimate
  false accept, false reject, agreement and timing error over a disjoint frozen
  evaluation set with sample size and uncertainty; unresolved fidelity or a
  critical #354 mutation blocks scored trials.
- HWP-14: A machine-readable physical protocol MUST freeze selected typed and
  monolithic artifact hashes, tasks and physical instance/randomization bank,
  non-oracle perception, station/calibration/scorer/safety hashes, block order,
  trial unit, primary/secondary endpoints, smallest effects or precision target,
  power/sample size, stopping/exclusion/rerun rules, reset/intervention rules,
  budgets, analysis seed and exact commands before the first scored trial.
  Repeated frames, commands or episodes within a physical task trial MUST NOT
  inflate the independent sample.
- HWP-15: Typed and control artifacts MUST run paired physical task instances in
  randomized temporal blocks with the same driver/primitives, perception,
  task information, authority, limits, operator protocol and budgets. The
  analyzer MUST report attempts, successes, failures, interventions, exclusions,
  safety exposures, resource/time outcomes and uncertainty by artifact and
  block. Negative, null, unsafe, and control-favoring results remain reportable
  and MUST NOT trigger unregistered tuning.
- HWP-16: At least one retained successful non-oracle physical task result is a
  necessary gate for any physical-AI main-track claim, but one success MUST NOT
  be presented as reliability evidence. Claim scope and uncertainty follow the
  full powered/precision set. If there is no qualifying success, physical claims
  remain `hardware_pending`, `weakened` or `rejected` as appropriate rather than
  substituting a simulation or dry-run result.
- HWP-17: If autonomous operation/fault recovery remains a headline claim, at
  least one pre-registered blinded physical live-fault session MUST use a safe
  allowlisted fault that cannot alter the driver, gateway, limits, estop,
  scorer, evidence sink or physical truth. It MUST follow #348/#349 sealing,
  diagnosis-before-repair, no-fault-control, intervention and retention rules.
  Hardware safety review may reject every candidate; in that case the physical
  operation claim is dropped rather than running an unsafe fault.

## Analysis, retained evidence, and blocked-state checklist

- HWP-18: A CON-8 analyzer MUST regenerate scorer-fidelity, station/calibration
  validity, trial flow, outcomes/uncertainty, intervention and safety-exposure
  tables, synchronized telemetry reconciliation, and simulation-to-hardware
  deltas from named raw records. Matched simulation and physical rows MUST remain
  distinct and report success, quality, timing, intervention and failure-class
  changes with denominators; missing hardware fields, mixed evidence kinds or
  unpaired task definitions MUST fail rather than be silently pooled.
- HWP-19: Release evidence MUST retain manifests, calibration raw data/artifacts,
  safety case/checklists/drills, firmware/software/container hashes, protocols,
  randomization, raw synchronized sensor/command/receipt/state/intervention/
  scorer/outcome records, videos, operator/reset logs, failed/aborted trials,
  analyzer/fixtures/tables, sim-hardware comparison, deviations, claim status
  and exact regeneration commands. Secrets and personal video regions may be
  deterministically redacted with retained private originals and logged hashes.
- HWP-20: Until hardware is acquired, the public status MUST name the exact
  blocker and show this execution checklist with each step `pending`, `passed`
  or `failed`: (1) acquire and inventory station; (2) inspect/anchor workspace
  and verify physical estop; (3) pin firmware/software/power/USB; (4) measure and
  independently verify motor/workspace/camera/time calibration; (5) run torque-
  disabled adapter/telemetry rehearsal; (6) run no-load then loaded primitive and
  limit checks; (7) execute watchdog, held-command, estop, power-loss and sink-
  failure drills; (8) audit and freeze the physical scorer; (9) rehearse reset
  and intervention logging; (10) freeze and externally timestamp powered task
  protocol; (11) execute randomized blocks without unregistered changes; (12)
  close/reveal any live-fault cell; (13) reconcile and archive raw evidence; (14)
  regenerate analysis and update #358. No later step may pass when a prerequisite
  failed, and preparatory dry runs MUST NOT mark physical steps complete.

## Required hardware-independent artifacts and limitations

Before acquisition, implementations MUST provide schemas/validators for every
manifest and raw record above; injectable adapters; deterministic loopback and
replay fixtures; calibration template plus reduction/check commands; safety and
operator checklist templates; scorer/audit fixtures; protocol/power/freeze
templates; telemetry and sim-delta analyzers; dry-run commands; and the blocked-
state checklist. Tests validate preparedness only. Realized device behavior,
latency, current/thermal limits, calibration error, scorer fidelity, stop
performance, task success and sim-to-real deltas are unknowable until measured.
