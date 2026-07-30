# H3 findings — skill-accumulation campaign (design doc §11.5, hypothesis §6 H3)

**Status: all six scenarios recorded. Direction: H3 NOT MET under these
budgets — arm L (the library arm) never achieved a single dev success
on either verdict tier, which fixes the outcome regardless of arm W's
pending rerun.** The formal verdict remains `pending` in the analyzer
only because the W/S2 and W/S3 cells are `wipe_leak`-contaminated and
excluded; the queued `--attempt 2` rerun completes the record but
cannot change the direction (a tier where L never succeeds is not-met
by construction, ADR-h3 §7).

Protocol: `tools/h3_campaign.py` per
`docs/decisions/ADR-h3-campaign-protocol.md` (campaign 2 + resume,
commit `03da7469`, model `claude-fable-5`, dev seeds 0..49, held-out
100..107). Arm L's S2 originally aborted on a Fable 5 quota 429 and was
resumed same-model after the worktree residue clear (ADR resume
amendment); the aborted 3.4-min telemetry is preserved separately
(`records/arm_L/S2/token_samples-aborted-429.jsonl`).

**Reproduce the whole table:**
`uv run python tools/h3_analysis.py --dir analysis/h3/records`
(self-contained: both aggregates + per-cell `scenario.json` +
`token_samples.jsonl`; `runs/` is gitignored).

## The records (assembled table)

| Arm | Tier | Held-out pass@1 | Session end | First success (min) | Tokens@1st | Precision fails (holdout) | Flags |
|---|---|---|---|---|---|---|---|
| W | S1 | 0.375 | agent_done | 146.7 | 165k | 0 | — |
| L | S1 | 0.500 | token_budget | 100.8 | 661k | 0 | — |
| W | S2 | 0.333 (partial) | token_budget | 101.4 | 716k | misplaced 1 | wipe_leak, holdout_partial |
| W | S3 | 0.000 | token_budget | 48.9 | 536k | wrong_slot 7 | wipe_leak |
| L | S2 | 0.000 | token_budget | — | — | wrong_slot 3 | — |
| L | S3 | 0.000 | token_budget | — | — | wrong_slot 3 | — |

## The headline: the library arm collapsed on the verdict tiers

L/S2 and L/S3 each burned their full 750k-token budget **without
completing a single dev rollout** — the held-out scoring run is the
only rollout in both records, and `first_success_wall_s` is null. The
sessions ended by token kill at 1.4h and 1.0h respectively: the fastest
burns of the campaign, all context and no measurement. Meanwhile the
wiped arm — even discounting its contamination — reached dev successes
on both tiers. Whatever the mechanism, "a persistent library cuts
time-to-success ≥2x" (H3) cannot hold when the library arm records no
successes at all.

Two observations that sharpen the mechanism question:

- **The transfer channel worked; the transfer didn't.** L/S2 started
  with an empty library (L/S1 registered nothing) but RE-CREATED and
  registered `s1-driver-v2` from the read-only idea tree (D3) — the
  weak channel carried. L/S3 then started with that skill in its
  library (`prior_skills: [s1-driver-v2]`)… and never wired it into a
  deliverable (`skill_reuse_in_deliverable: []`), never succeeded.
  Library *presence* did not produce library *use*.
- **The budgets were plausibly under water.** L/S1's
  tokens-to-first-success was **661k** — the verdict tiers' entire
  budget is 750k, with S2/S3 being *harder* scenarios. The D2
  sub-budget split (1M for S1, 0.75M for S2/S3) assumed later
  scenarios get cheaper via accumulation; the data says the assumption
  did the gating. "NOT MET under these budgets" is therefore the
  honest claim — the campaign does not distinguish "libraries don't
  help" from "0.75M is below the entry cost of S2/S3."

## H5 in the retail suite: breached

14 held-out precision-class failures across the six cells
(13 `wrong_slot`, 1 `misplaced`); `wrong_object` stays 0 but is
vacuous in retail (desk-only label). Dev-side adds W/S1's 2
`extra_item`. The structural-safety claim survives only in its
narrow desk form; the retail precision guarantee did not hold.

## The one clean pair (S1) — replicated caution, not transfer

Both arms start library-empty on S1, so the pair measures run-to-run
variance of the same condition: L 0.5 vs W 0.375 held-out, L faster in
wall time (101 vs 147 min) but ~4x the tokens to first success (661k vs
165k). n=1 per cell; wide single-session variance is itself a finding
the eventual verdict must weigh.

## What remains for the formal record

1. **W/S2 + W/S3 rerun** under the hardened runner (`--arms W
   --scenarios S2,S3 --attempt 2`, fixed wipe [PR #57], rerun
   allowlist + slot rotation [PR #61]) — replaces the `wipe_leak`
   cells; the analyzer then computes `met: false` formally from clean
   cells. Queued for machine time; cannot change the direction.
2. Any budget-corrected follow-up campaign (e.g. equal 1M budgets per
   scenario) would be a NEW experiment answering the confound above —
   a protocol decision, not a rerun.

IDs: design doc §11.5, §6 H3/H5, §1 (10x precision asymmetry);
ADR-h3-campaign-protocol §7/§9 + campaign-2 and resume amendments;
RS-7; CON-5.
