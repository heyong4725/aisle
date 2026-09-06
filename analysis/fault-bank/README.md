# Sealed fault bank (SPEC 450, issue #348)

Only commitments and schema versions are tracked here (FLT-4). The bank
itself, its calibration outcomes, the assignment seed, and the reveal key
live outside every worktree, git namespace, and tool root under
`~/aisle-private/fault-bank/v1/`.

## Public machinery

- `src/aisle/harness/fault_injector.py`: bank manifest schema and
  mechanical coverage validation (FLT-1 to FLT-3), the FLT-11 target
  allowlist with frozen-asset exclusions, the generic content-addressed
  injector that materializes allowlisted targets into an evaluator-owned
  staging tree and applies exactly one sham, single, or coupled transaction
  atomically with pre/postimage hashes (FLT-7), deterministic HMAC
  assignment and the pre-collection commitment (FLT-5), the sealed ledger
  row, reveal, and exact replay (FLT-13, FLT-14), and the monotonic
  lifecycle (FLT-16).
- `src/aisle/harness/fault_calibration.py`: FLT-9 paired scoring on the
  seed-pair unit and the FLT-10 `excluded_pilot` calibration report.
- `harness fault validate|calibrate|assign`.
- `tests/unit/test_fault_injector.py` and `tests/unit/test_fault_calibration.py`
  use a synthetic canary bank only (FLT-15).

## Staging mechanics

A rung runs in a detached git worktree at the bank's clean commit with the
injected target overwritten, and `harness rollout --root <worktree>` so
VAL-2 resolves node paths against the staged tree. The shared environment
is pinned with `UV_PROJECT_ENVIRONMENT`; the `aisle` package import path
stays the repository's, only node entry files come from the worktree.

## Participant-visible parity (FLT-8) and residency (FLT-4)

Injector v2 (`aisle.fault-injector.v2`) pads every staged target — sham or
fault — with a trailing comment to exactly `PAD_BYTES` more than its
preimage, copies the preimage's mode bits, and stamps one fixed mtime, so
the size, mode and timestamp a participant can see without opening the file
are identical across cells; a replacement that would outgrow the pad is
refused. Each receipt records that `visible_metadata`. The leakage probe is
frozen in `leakage-probe.json` before the v2 round (feature allowlist,
leave-one-out nearest-centroid classifier, seeded permutation null, alpha
0.05, at least three cells per class) and runs with
`harness fault leakage --report <calibration-report> --probe leakage-probe.json`;
a classifier that beats chance keeps the bank blocked. v1 receipts carry no
visible metadata, so the probe cannot be applied to flt-cal-v1 retroactively.

`harness fault validate --bank ... --root . [--tool-root ...]` now also
refuses a bank that resides inside the participant worktree, any git
worktree or object namespace of the repository, a HOME cache view, the temp
directory, or a declared tool root. That is the residency half of FLT-4;
the denial proof (a participant session actually unable to reach the
bytes) waits on the issue #353 confinement adapter.

## Commitment

`commitment.json` binds the private manifest hash, clean baseline hash,
clean commit, injector version, and counts by family, coupled, sham,
intermittent, and novel-repair class. It withholds instance ids, targets,
operators, severities, activation rules, calibration outcomes, the
assignment seed, and the reveal key. The bank is a draft: uncalibrated,
unsealed. A commitment is not evidence.

Registration: `analysis/freeze/flt-bank-calibration-v2/` (pending CON-14
approval of SPEC 450, the FLT-4 confinement proof, and the FLT-8 sham
leakage probe).

## Calibration (campaign flt-cal-v1, excluded_pilot)

Fifteen rungs (eleven instances, two of them with a two-rung severity
ladder) ran on `graphs/expert_t1.yaml` against the retained clean run
`sfe-exposure-pilot-01` (seeds 0..7, 8/8), each in a detached worktree at
the clean commit. Frozen rule: paired clean-minus-fault difference at
least 0.25, at least three discordant seed pairs, exact 95% lower bound
on the clean-only share of discordant pairs above 0.5; a wrong-object
episode under a fault rejects the instance (FLT-11).

| family | rungs | outcome |
|---|---|---|
| perception (2 instances, 3 rungs) | 8/8, 8/8, 8/8 | rejected: no degradation |
| decision (2 rungs) | 8/8, 8/8 | rejected |
| motion (2 instances, 4 rungs) | 7/8 (one collision) twice, 8/8 twice | rejected: 0.125 with one discordant pair |
| schema_metadata | 0/7, consumer refuses the payload, run ends in wall clamps | selected |
| clocking | 8/8 | rejected |
| runtime | 0/7, node exits mid-run | selected |
| coupled (2) | 7/8, 8/8 | rejected |
| sham | 8/8 | parity (control) |

Disposition: **bank v1 cannot be sealed.** Only two families have an
effective instance, so the FLT-2 coverage gate fails after calibration,
and the sham parity observed here is not the FLT-8 leakage probe. A v2
candidate round with stronger severities is required before any scored
use; calibration outcomes select or reject severities and never enter a
confirmatory estimate (FLT-9, FLT-10). `calibration-summary.json` carries
opaque ids, families, paired effects, and decisions; targets, operators,
and severity values stay private. Raw runs are retained under
`~/aisle-private/raw/fault-calibration/`.

## Calibration round v2 (campaign flt-cal-v2, excluded_pilot)

Bank v2 (`aisle-fault-bank-v2-draft`, injector v2, 14 instances: 2
perception, 2 decision, 2 motion, 1 schema, 1 clocking, 1 runtime, 2
coupled, 3 shams) ran 18 rungs on `graphs/expert_t1.yaml` against the
fresh clean run `flt-clean-v2` (seeds 0..7, 8/8; `records/flt-clean-v2/`),
each in a detached worktree at the clean commit. Same frozen rule as v1.

| family | rungs | outcome |
|---|---|---|
| perception (2 instances, 3 rungs) | 0/8, 0/8, 8/8 | persistent bias selected at its least severe rung; the intermittent instance is recovered by in-episode retries and rejected |
| decision (2 instances, 3 rungs) | 1/8, 1/8, 0/8 | both selected |
| motion (2 instances, 4 rungs) | 8/8, 8/8, 5/8, 0/8 | loose IK tolerance rejected at both severities; low transfer selected at its second rung only |
| schema_metadata | 0/8 (executor crash, wall clamps) | selected; crash-class, labelled as such |
| clocking | 0/8 (no pose ever pairs) | selected |
| runtime | 0/8 (crash at frame 400, wall clamps) | selected; crash-class |
| coupled (2) | 0/8, 1/8 | both selected |
| sham (3) | 8/8, 8/8, 8/8 | parity with clean |

Coverage after calibration: every family has at least one selected
instance (`calibration-summary-v2.json`; nine of eleven real instances).
The FLT-8 leakage probe over the 18 materialization receipts (3 sham, 15
fault) does not beat chance: balanced accuracy 0.37, permutation p 0.80
(`leakage-v2.json`). `commitment-v2.json` binds the v2 manifest hash,
clean commit and counts. The bank is still not sealed: FLT-9/FLT-10
sealing waits on CON-14 approval of SPEC 450 and the FLT-4 denial proof
(issue #353). Registration: `analysis/freeze/flt-bank-calibration-v6/`.
Raw runs: `~/aisle-private/raw/fault-calibration-v2/`.
