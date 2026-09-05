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

## First audit: `records/bnd-perception-corpus-02/`

Corpus: `graphs/expert_t1_l2.yaml`, tier T1, rung L2, Franka, seeds 0..7,
frames captured every 0.1 s of sim time on both cameras (2234 overhead
frames); episode outcomes 6/8 (seeds 1 and 6 never grasped). Split by
seed content: even seeds calibrate, odd seeds evaluate. Envelope: overhead
camera, operating window 2.0 s after reset (the L2 node's first estimate),
margin floor 0.01, localization tolerance 0.03 m, accuracy floor 0.90 per
stratum, refusal limit 0.5. Raw frames retained privately with their
hashes in the corpus.

| stratum | n | correct | refused | localization error | accuracy | exact 95% lower bound |
|---|---|---|---|---|---|---|
| overhead, all evaluation | 80 | 57 | 20 | 3 | 0.713 | 0.618 |
| target amoxicillin (seed 3) | 20 | 19 | 0 | 1 | 0.95 | 0.784 |
| target cetirizine (seed 5) | 20 | 19 | 0 | 1 | 0.95 | 0.784 |
| target omeprazole (seed 7) | 20 | 19 | 0 | 1 | 0.95 | 0.784 |
| target ibuprofen (seed 1) | 20 | 0 | 20 | 0 | 0.00 | 0.000 |

Wrong identity: 0 of 80. Latency: median 0.69 s, max 0.77 s per frame on
the campaign Mac (descriptive). 4312 captured frames were outside the
envelope (wrist camera, or after the operating window) and carry no
prediction.

Eligibility (BND-7): **not eligible**. The ibuprofen stratum refuses every
frame (the node's margin gate, whose floor was set from the measured
wrong-pick signature, never clears for this target), and 20 correlated
frames from one seed cannot reach a 0.90 lower bound even at 19/20. The
short_composition candidate therefore does not enter the task band on this
envelope; the role stays open for a new candidate round (BND-10). No
threshold was tuned on these evaluation records.

```bash
AISLE_FRAME_CAPTURE_PERIOD_S=0.1 uv run harness rollout --graph graphs/expert_t1_l2.yaml \
  --tier T1 --embodiment franka --perception L2 --episodes 8 --seeds 0..7 \
  --no-idea-gate --env-baseline local --run-id bnd-perception-corpus-02
uv run harness perception audit --run runs/bnd-perception-corpus-02 \
  --envelope analysis/perception-audit/envelope.json \
  --output analysis/perception-audit/records/bnd-perception-corpus-02/report.json
```
