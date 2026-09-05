# Independent perception audit (SPEC 490 BND-5 to BND-7, issue #346)

`envelope.json` is the frozen perception envelope for the pharmacy L2 rung
(BND-6): identity vocabulary, margin floor 0.01 (the L2 node's measured
floor), localization tolerance 0.03 m, latency ceiling 5 s, refusal
availability limit 0.5, accuracy floor 0.90 per stratum, strata by target
class, seed parity, and sensor, synchronization by shared sim stamp, and
the tuning rule (calibration split only).

`harness perception audit --run <recorded run> --envelope envelope.json`
builds the corpus from captured frames (`AISLE_FRAME_CAPTURE_PERIOD_S` at
rollout time), the oracle_state trace (audit-only truth), the goals, and
the bridge calibration; runs the same OWLv2 identity and depth
back-projection the L2 node runs, with the truth opened only after each
prediction is fixed; and reports per-stratum accuracy with exact lower
bounds against the floor, refusal rates, latency, and the failure taxonomy
(BND-5, BND-7). Every stratum must pass; an aggregate never masks one.

No corpus has been audited yet: the recorded sessions in this repository
were captured without frames. The first audit run is queued behind the
fault-bank calibration campaign on the simulator host. Until it exists,
BND-7 eligibility for both candidates is open.
