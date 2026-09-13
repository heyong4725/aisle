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

## Second audit: `records/bnd-perception-corpus-03/` (2026-09-13)

Regenerated with the hardened auditor under the unchanged envelope, as the
v10 registration required. Corpus: `graphs/expert_t1_l2.yaml`, tier T1, rung
L2, Franka, seeds 0..31 (development seeds), frames captured every 0.1 s on
both cameras; episode outcomes 28/32 (seeds 1, 6, 11 and 31 never grasped).
Split by seed parity: 5428 calibration and 4577 evaluation unique frames, of
which 320 evaluation frames fall inside the 2 s operating window and are
scored (14735 rows are retained in `raw-predictions.json.gz`, all of them,
so `report_hash` can be recomputed). Result:
**not eligible**, 22 of 22 strata fail. Ibuprofen refuses 68 of 80 evaluation
frames (accuracy lower bound 0.010); the other four targets score 50 to 57 of
60 with lower bounds 0.734 to 0.876 against the 0.90 floor; the overhead sensor
stratum is 214/320 (0.669, lower bound 0.623). Failure taxonomy: 214 correct, 75 refused,
31 localization errors, 0 wrong identity, 0 missing data. Latency median 0.77 s,
max 0.96 s, within the 5 s ceiling. More seeds did not change the picture: the
seed strata are still 20 correlated frames each (the operating window bounds
them), and the target strata fail on accuracy, not sample size. No threshold
was changed (BND-10). `eligibility.json` is the `tools/perception_eligibility.py`
record over this report: it recomputes `report_hash` from the retained raw
predictions, matches the envelope hash and the run's graph, and derives every
stratum's eligibility from its lower bound and refusal rate against the
envelope, never from the report's own pass flags.

```bash
AISLE_FRAME_CAPTURE_PERIOD_S=0.1 uv run harness rollout --graph graphs/expert_t1_l2.yaml \
  --tier T1 --embodiment franka --perception L2 --episodes 32 --seeds 0..31 \
  --no-idea-gate --env-baseline local --run-id bnd-perception-corpus-03
uv run harness perception audit --run runs/bnd-perception-corpus-03 \
  --envelope analysis/perception-audit/envelope.json \
  --output /tmp/bnd-perception-corpus-03-report.json
uv run python tools/perception_record.py \
  --report /tmp/bnd-perception-corpus-03-report.json \
  --run runs/bnd-perception-corpus-03 \
  --output analysis/perception-audit/records/bnd-perception-corpus-03
uv run python tools/perception_eligibility.py \
  --report analysis/perception-audit/records/bnd-perception-corpus-03/report.json \
  --envelope analysis/perception-audit/envelope.json \
  --candidate t1-l2-expert-graph --role short_composition \
  --graph graphs/expert_t1_l2.yaml \
  --output analysis/perception-audit/records/bnd-perception-corpus-03/eligibility.json
```

Raw run evidence (1.1 GB of traces) is retained privately under
`~/aisle-private/raw/bnd-perception-corpus-03`.

## Auditor integrity revision (#346)

The hardened auditor passes only the assigned target to the localizer; oracle
positions, seeds, and scorer strata remain outside that callback's inputs. It
enforces the declared confidence and latency limits, rejects duplicate records
and invalid limits, retains missing observations in accuracy denominators, and
requires evaluation coverage of every declared target class. These API checks
do not establish process isolation or independence of correlated frames.

The corpus-02 report above remains historical evidence from the previous
auditor; corpus-03 is the hardened-auditor regeneration that BND v7 through
v10 required, registered as v11. No threshold, seed
commitment, or eligibility rule has changed. Replays must use a new output
path rather than overwrite the retained report.

A further geometry revision rejects non-finite or non-xyz localization output
as localization error. Missing scene-object truth or malformed oracle positions
produce missing-data failures, with attempted predictions retained. This prevents
NaN comparisons and array broadcasting from certifying invalid coordinates.
BND v8 requires a fresh audit under these checks and remains unfrozen.

The CLI also records the content digest of the exact identity-model lock entry
passed to the pinned loader. That entry binds the model repository/revision and
individual snapshot-file hashes, which the loader verifies before use. Missing
or descriptive model-hash fields fail the auditor. Existing historical reports
are not rewritten with provenance they did not record.
