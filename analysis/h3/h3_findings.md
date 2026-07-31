# H3 findings — skill-accumulation campaign (design doc §11.5, hypothesis §6 H3)

**Status: COMPLETE. Formal verdict: H3 NOT MET** — computed by
`tools/h3_analysis.py` from clean cells only, after the three flagged
cells were replaced by `--attempt 2` reruns under the hardened runner.
`met: false` because the S2 tier fails (neither clean arm ever
succeeded); the S3 tier is formally TRUE — the library arm (with
`s1-driver-v2` carried by the residue guard's allowlist) reached dev
first-success at 23 minutes while the clean wiped arm never succeeded
on either verdict tier — the experiment's one positive transfer
signal, at n=1, with held-out pass@1 still 0.0 for both. The verdict
criterion (`met = all tiers`) and the budget confound below cap the
honest claim at: **no accumulation benefit demonstrated under these
budgets; one mechanism-level hint that the library shortened
time-to-first-success on S3.**

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

## The rerun cells (attempt 2, hardened runner)

| Cell | Session | Held-out | Notes |
|---|---|---|---|
| W/S2-r2 | token-killed 752k @ 33 min | **0.0 (no deliverable)** | zero rollouts; the truly-wiped arm never got off the ground — scored as an outcome, not an infra partial |
| W/S3-r2 | token-killed 751k @ 92 min | 0.0 (7 missing_item, 1 wrong_slot) | produced a deliverable + registered `s3-driver-v2`; no dev success |
| L/S3-r2 | **agent_done** 384k @ 61 min | 0.0 (8 wrong_slot) | the ONLY voluntary finish of the whole experiment; dev first-success at 23 min with `s1-driver-v2` in its library (allowlist-carried); `skill_reuse_in_deliverable` still empty |

The clean-vs-contaminated contrast is now measured: contaminated W/S2
(with the leaked skill) reached dev success at 101 min; truly-wiped
W/S2-r2 produced nothing at all — the campaign-2 leak was
load-bearing for the wiped arm's apparent capability, which is exactly
why flagged cells were excluded and rerun.

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
- **Placement-class, held-out: 23** (22 `wrong_slot`, 1 `misplaced`,
  now including the rerun cells — L/S3-r2 alone contributed 8/8
  `wrong_slot`) — placement quality was poor wherever S2/S3 systems
  ran.
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

## Closed questions and remaining follow-ups

The formal record is complete: reproduce with
`uv run python tools/h3_analysis.py --dir analysis/h3/records`
(all nine cells, four aggregates, and the preserved aborted-session
telemetry are in the bundle). A "no deliverable" holdout is scored as
0.0 (an outcome — there was nothing to run), distinct from an expired
scoring window (infra partial); the analyzer encodes this and the
verdict computes `met: false` from clean cells.

Remaining follow-ups (separate experiments, not part of this verdict):
issue #71's back-to-back attested determinism pair for `expert_s1`;
any budget-corrected accumulation campaign (a NEW protocol decision);
and the S3 transfer hint above, which merits a designed replication
before it is called a finding.

IDs: design doc §11.5, §6 H3/H5, §1 (10x asymmetry); ADR-h3 §7/§9 +
campaign-2 and resume amendments; RS-7 (delivery class), §11.3
(placement family); CON-5.
