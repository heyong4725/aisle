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
