# H3 findings — skill-accumulation campaign (design doc §11.5, hypothesis §6 H3)

**Status: all six scenarios ran; five produced usable-or-flagged
records and one (L/S3) is excluded for a residue leak. Direction: H3
NOT MET under these budgets** — arm L's one *clean* verdict-tier cell
(L/S2) burned its full budget without a single dev success, which fixes
the S2 tier at not-met and therefore caps the campaign verdict at
not-met. The analyzer's formal verdict stays `pending` until the three
queued reruns replace the flagged cells (W/S2, W/S3: `wipe_leak`;
L/S3: `residue_leak`); no rerun outcome can flip the direction.

Protocol: `tools/h3_campaign.py` per
`docs/decisions/ADR-h3-campaign-protocol.md` (campaign 2 + same-model
resume after a Fable 5 quota 429, commit `03da7469`,
`claude-fable-5`, dev seeds 0..49, held-out 100..107). The aborted
3.4-min L/S2 telemetry is preserved separately
(`records/arm_L/S2/token_samples-aborted-429.jsonl`).

**Reproduce everything:**
`uv run python tools/h3_analysis.py --dir analysis/h3/records` — cells,
flags (all machine-derived from the records: `wipe_leak` from
prior_skills on the wiped arm, `residue_leak` from the aggregates'
wipes lists, `holdout_partial` from the holdout status), verdict, and
the delivery/placement failure totals. The bundle carries both
aggregates, per-cell `scenario.json` + `token_samples.jsonl`, and the
one cited dev-evidence file.

## The records (assembled table)

| Arm | Tier | Held-out pass@1 | First success (min) | Tokens@1st | Delivery fails | Placement fails | Flags |
|---|---|---|---|---|---|---|---|
| W | S1 | 0.375 | 146.7 | 165k | 0 | 0 | — |
| L | S1 | 0.500 | 100.8 | 661k | 0 | 0 | — |
| W | S2 | 0.333 (partial) | 101.4 | 716k | 0 | misplaced 1 | wipe_leak, holdout_partial |
| W | S3 | 0.000 | 48.9 | 536k | 0 | wrong_slot 7 | wipe_leak |
| L | S2 | 0.000 | — | — | 0 | wrong_slot 3 | — |
| L | S3 | 0.000 | — | — | 0 | wrong_slot 3 | residue_leak |

**L/S3's exclusion (PR #67 review):** the resume ran the pre-guard
runner (the arm-L residue guard [PR #61] merged after launch), and its
aggregate records `wipes: []` — L/S3 inherited L/S2's working state
(`graphs/agent_campaign.yaml`, unregistered skill code, debug logs),
violating the residue-clear protocol. The `residue_leak` flag is
derived from the aggregates by the analyzer, and the cell is excluded
from the verdict like every flagged cell.

## The headline: the library arm's clean verdict-tier cell never succeeded

L/S2 — clean, residue-cleared, same model — burned its full 750k-token
budget **without completing a single dev rollout**: the held-out
scoring run is the only rollout in its record, `first_success_wall_s`
is null, held-out pass@1 is 0. A tier where arm L never succeeds is
not-met by construction (ADR-h3 §7), and `met = all(tiers)`, so the
campaign verdict is capped at NOT MET before any rerun. (The excluded
L/S3 behaved identically — 752k tokens, no dev rollout, 0.0 — but
carries the flag and is not evidence.)

Two observations that sharpen the mechanism question:

- **The weak transfer channel worked; transfer didn't.** L/S2 started
  library-empty (L/S1 registered nothing) yet RE-CREATED and registered
  `s1-driver-v2` from the read-only idea tree (D3). Library *presence*
  never became library *use*: no deliverable ever wired a prior skill
  (`skill_reuse_in_deliverable: []` in every cell).
- **The budgets were plausibly under water.** L/S1 needed **661k**
  tokens to its first success; the *harder* verdict tiers got 750k
  (D2's split assumed accumulation would make them cheaper). The
  campaign cannot distinguish "libraries don't help" from "0.75M is
  below the S2/S3 entry cost" — "NOT MET under these budgets" is the
  claim, and a budget-corrected campaign would be a new experiment.

## H5: delivery precision held on committed held-out records; placement quality did not

**Correction (PR #67 review — an earlier draft conflated these).**
H5's 10x-asymmetric claim is about the wrong THING delivered
(`wrong_object` desk / `extra_item` retail, RS-7). Placement-quality
failures (`misplaced`, `wrong_slot` — §11.3's placement family) are a
different, lesser claim. Split accordingly, on committed records only:

- **Delivery-class, held-out: 0 across all 48 held-out episodes**
  (every cell). On the evidence we score by, the wrong-thing-delivered
  guarantee held.
- **Placement-class, held-out: 14** (13 `wrong_slot`, 1 `misplaced`) —
  placement quality was poor wherever S2/S3 systems ran.
- **Dev-side, committed evidence:** 2 `extra_item` in W/S1's baseline
  run (`records/arm_W/S1/dev/20260728-001009-b4d1fd.episodes.jsonl`) —
  the expert-copy baseline before the agent's fix, on the agent's own
  seeds. A real delivery-class occurrence, outside the scored surface.
  (An earlier draft claimed a larger dev tally by double-counting
  holdout runs through the rollouts lists; only this file is committed
  dev evidence.)

## The one clean pair (S1) — replicated caution, not transfer

Both arms start library-empty on S1: two draws from the same
condition. L 0.5 vs W 0.375 held-out; L faster in wall time (101 vs
147 min) but ~4x the tokens to first success (661k vs 165k). n=1 per
cell — single-session variance is wide, which any eventual verdict
must weigh.

## What remains for the formal record

Three reruns under the hardened runner (fixed wipe [PR #57], arm-L
guard + rerun allowlist + slot rotation [PR #61]), all `--attempt 2`:

1. `--arms W --scenarios S2,S3 --attempt 2` — replaces the `wipe_leak`
   cells.
2. `--arms L --scenarios S3 --attempt 2` — replaces the `residue_leak`
   cell (the guard limits its library to L/S3's original
   `prior_skills`, i.e. `s1-driver-v2`).

The analyzer then computes the verdict from clean cells; given clean
L/S2's null first-success, the only reachable outcome is
`met: false`.

IDs: design doc §11.5, §6 H3/H5, §1 (10x asymmetry); ADR-h3 §7/§9 +
campaign-2 and resume amendments; RS-7 (delivery class), §11.3
(placement family); CON-5.
