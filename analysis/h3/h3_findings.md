# H3 findings — skill-accumulation campaign (design doc §11.5, hypothesis §6 H3)

**Status: INCOMPLETE (4 of 6 scenarios). No H3 verdict is drawable
yet.** This document records what the partial run supports and names
exactly what must still run. Protocol: `tools/h3_campaign.py` per
`docs/decisions/ADR-h3-campaign-protocol.md` (campaign 2, commit
`03da7469`, model `claude-fable-5`, dev seeds 0..49, held-out 100..107).
Table + raw records: `h3_results_table.md`, `records/`. Assembled by
`tools/h3_analysis.py` — **reproduce the whole table with**
`uv run python tools/h3_analysis.py --dir analysis/h3/records` (the
`records/` bundle is self-contained: `h3_results.json` +
`arm_*/S*/scenario.json` + `token_samples.jsonl` per cell; runs/ itself
is gitignored).

## Why the campaign is incomplete

Arm W ran all three scenarios; arm L completed S1; **arm L's S2 session
aborted 3.4 minutes in on a Fable 5 account usage limit** (HTTP 429,
`"You've reached your Fable 5 limit"`) — an infra abort, not an agent
outcome, correctly logged with no scenario record written. Arm L's S3
never started. Finishing the campaign validly requires L/S2 and L/S3 on
the **same** model (ADR-h3 §9 confound control: same model + CLI version
both arms); a mid-campaign model switch would make arm L incomparable to
arm W and to its own S1.

## The records (assembled table)

| Arm | Tier | Held-out pass@1 | Session end | First success (min) | Tokens@1st | Flags |
|---|---|---|---|---|---|---|
| W | S1 | 0.375 | agent_done | 146.7 | 165k | — |
| L | S1 | 0.500 | token_budget | 100.8 | 661k | — |
| W | S2 | 0.333 (partial) | token_budget | 101.4 | 716k | wipe_leak, holdout_partial |
| W | S3 | 0.000 | token_budget | 48.9 | 536k | wipe_leak |
| L | S2 | — | — | — | — | infra abort (Fable 5 429) |
| L | S3 | — | — | — | — | not run |

## H5 in the retail suite — the precision guarantee did NOT hold clean

**Correction (PR #60 review).** H5 is the *precision* claim: the wrong
thing is never delivered/placed (design doc §1, the 10x-asymmetric
penalty). In the DESK suite that failure is `wrong_object`; in the
RETAIL suite that label never fires — the precision failures are
`extra_item` (delivered an unordered item — the campaign notes call it
"the 10x class", RS-7), `misplaced`, and `wrong_slot`. The scenario
records' `wrong_object_total: 0` is therefore **vacuous for retail**, and
reporting it as "H5 holds" (as an earlier draft of this doc did) was
wrong.

Counting the retail precision classes across every recorded episode
(dev + held-out):

| Cell | precision-class failures |
|---|---|
| W/S1 | 2 `extra_item` (dev) |
| W/S2 | 1 `misplaced` (dev) + 1 `misplaced` (holdout) |
| W/S3 | 7 `wrong_slot` (dev) + 7 `wrong_slot` (holdout) |
| L/S1 | none |
| **total** | **18** (2 extra_item, 2 misplaced, 14 wrong_slot) |

So the retail precision guarantee was **breached repeatedly** in the
recorded runs — most sharply W/S3's 7/8 held-out `wrong_slot`. H5's
status in retail is *open and currently negative*; the eventual H3/H5
writeup must score the retail precision classes, not `wrong_object`.
(The `s1-driver-v2` skill's own design targets `extra_item=0` by
skipping the L0 picks that snag neighbours — see below — which is why
L/S1 and the v2 dev runs show none, but W/S2/S3 with different
deliverables did not.)

## What the records do NOT support

- **The H3 transfer verdict.** H3's criterion lives on S2/S3. Every
  S2/S3 cell is unusable: W/S2 and W/S3 are `wipe_leak`-flagged (the
  campaign-2 wipe carried `s1-driver-v2` through — [PR #57] fixed the
  wipe but these records predate it), W/S2 is additionally a partial
  holdout, and both L cells are missing. `tools/h3_analysis.py` returns
  `met: null`, correctly.

- **Seed-keyed overfitting as the cause of W/S3's 1.0→0.0.** An earlier
  draft called this "memorized slot assignments." **Correction (PR #60
  review): the records do not demonstrate that.** The v2-family driver
  skips L0 (bottom-shelf) picks by design (they jam on the L1 board and
  snag neighbours → `extra_item`), so an order is fulfillable only if
  every line has L1 stock; `metformin` has zero L1 slots. The agent
  validated on self-selected, L1-fulfillable dev seeds ({0,1,4,6,7,8,9,
  10}), while the held-out seeds 100..107 were unfiltered and contain
  L0-dependent orders. The 1.0-dev → 0.0-holdout gap is therefore a
  **dev-seed selection / distribution-shift** effect (the agent measured
  on a favourable subset), not demonstrated seed-keyed memorization. The
  `wrong_slot` failures are consistent with attempting L0-dependent
  orders the driver cannot fulfil, not with a slot lookup keyed to seeds.

- **A skill-accumulation signal at all.** Arm L — the *library* arm —
  finished S1 with `skills_after: []`: it registered no skill despite the
  identical D5 nudge. Any S1→S2 transfer would have to come through the
  read-only idea tree (D3), a weaker channel than the ASPIRE mechanism
  the hypothesis names. A single session did not choose to distill under
  these budgets — itself a finding.

## What the records DO support

- **S1, the one clean pair, is not a transfer measurement.** S1 is the
  first scenario for both arms, so both start library-empty; the two
  cells are two draws from the *same* from-scratch condition, not
  treatment vs control. They diverge sharply — L 0.5 vs W 0.375 held-out,
  L first-success faster in wall time (101 vs 147 min) but ~4x the tokens
  (661k vs 165k). With n=1 per cell, this is the run-to-run variance of
  the S1 condition — a caution that single-session cells carry wide
  uncertainty, which the eventual verdict must weigh.

## What must run to complete H3

1. **L/S2 and L/S3 on Fable 5** (same model), when the account quota is
   available. Arm L keeps only its *defined* library — registered skills
   + read-only idea tree (D3). L/S1 registered nothing, but its session
   left **untracked non-library residue** in `worktree_L`
   (`graphs/agent_campaign.yaml`, an unregistered `skills/s1-driver-v2/`).
   Before resuming, that residue MUST be cleared so arm L does not carry
   unregistered working state into S2 (PR #60 review):
   `git -C runs/h3/worktree_L clean -fdx -e runs` (preserves runs/ =
   ledger + idea tree), then
   `uv run python tools/h3_campaign.py --arms L --scenarios S2,S3
   --commit 03da7469`. This residue-leak affects the arm-L path in
   general (arm L never wipes); a runner guard is filed as a follow-up.
2. **W/S2 and W/S3 rerun under the fixed wipe** ([PR #57], `--attempt 2`
   → `S2-r2`/`S3-r2`), replacing the `wipe_leak` cells; the analyzer
   already prefers the highest-attempt clean cell.
3. Re-run `tools/h3_analysis.py`; the verdict becomes drawable once every
   S2/S3 cell is clean and present, and the retail precision classes are
   scored for H5.

Until then: no accumulation claim, positive or negative, is on the
record — and the recorded retail runs show the precision guarantee (H5)
was not clean.

IDs: design doc §11.5 (transfer curve), §6 H3/H5, §1 (10x precision
asymmetry); ADR-h3-campaign-protocol (§7 verdict, §9 confound control,
campaign-2 amendment); RS-7 (retail precision failure classes); CON-5.
