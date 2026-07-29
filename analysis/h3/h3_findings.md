# H3 findings — skill-accumulation campaign (design doc §11.5, hypothesis §6 H3)

**Status: INCOMPLETE (4 of 6 scenarios). No H3 verdict is drawable
yet.** This document records what the partial run supports and names
exactly what must still run. Protocol: `tools/h3_campaign.py` per
`docs/decisions/ADR-h3-campaign-protocol.md` (campaign 2, commit
`03da7469`, model `claude-fable-5`, dev seeds 0..49, held-out 100..107).
Table + raw records: `h3_results_table.md`, `records/`. Assembled by
`tools/h3_analysis.py` (the verdict is machine-derived, not asserted).

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

## What the records do NOT support

- **The H3 transfer verdict.** H3's criterion lives on S2/S3 (later
  scenarios, where a persisted library should pay off). Every S2/S3
  cell is unusable: W/S2 and W/S3 are `wipe_leak`-flagged (the campaign-2
  wipe carried `s1-driver-v2` through, so the "memory-wiped" arm was not
  wiped — [PR #57] fixed the wipe but these records predate it), W/S2 is
  additionally a partial holdout, and both L cells are missing. The
  analyzer returns `met: null`, correctly.

- **A skill-accumulation signal at all.** Arm L — the *library* arm —
  finished S1 with `skills_after: []`: it registered no skill despite the
  identical D5 nudge. So even once L/S2+S3 run, the S1→S2 hop has no
  evalcarded skill to reuse; any transfer would have to come through the
  read-only idea tree (D3), a weaker channel than the ASPIRE mechanism
  the hypothesis names. This is itself a finding: a single session did
  not choose to distill under these budgets.

## What the records DO support

- **S1, the one clean pair, is not a transfer measurement.** S1 is the
  first scenario for both arms, so both start library-empty; the two
  cells are two draws from the *same* from-scratch condition, not
  treatment vs control. They diverge sharply — L 0.5 vs W 0.375 held-out,
  and L reached first success faster in wall time (101 vs 147 min) but
  spent ~4x the tokens to get there (661k vs 165k). With n=1 per cell,
  this is best read as the run-to-run variance of the S1 condition — a
  caution that single-session cells carry wide uncertainty, which the
  eventual verdict must weigh.

- **W/S3 overfit sharply** (recorded, flag notwithstanding): 1.0 on dev
  seeds collapsing to 0.0 held-out with 7/8 `wrong_slot` failures — the
  signature of a solution keyed to dev-seed slot assignments rather than
  the planogram. A cautionary datapoint for the rerun, independent of the
  wipe-leak contamination.

- **H5 holds across every recorded episode.** `wrong_object_total: 0`
  over all four scenarios (and their held-out scoring) — zero
  wrong-object under free motion-policy iteration, consistent with H1/H2.

## What must run to complete H3

1. **L/S2 and L/S3 on Fable 5** (same model), when the account quota is
   available: `uv run python tools/h3_campaign.py --arms L --scenarios
   S2,S3 --commit 03da7469`.
2. **W/S2 and W/S3 rerun under the fixed wipe** ([PR #57], `--attempt 2`
   → `S2-r2`/`S3-r2`), replacing the `wipe_leak` cells; the analyzer
   already prefers the highest-attempt clean cell.
3. Re-run `tools/h3_analysis.py`; the verdict becomes drawable once every
   S2/S3 cell is clean and present.

Until then: no accumulation claim, positive or negative, is on the
record — only that the pipeline, the integrity flags, and H5 held.

IDs: design doc §11.5 (transfer curve), §6 H3/H5; ADR-h3-campaign-protocol
(§7 verdict, §9 confound control, campaign-2 amendment); CON-5.
