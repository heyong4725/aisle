# H2 findings — first measured T1 campaign (design doc §8.3 item 6, hypothesis §6 H2)

Protocol: `tools/campaign.py` per ADR-h2-campaign-protocol. One session,
treatment pinned at `e8f163ab` (claude-fable-5, Claude Code 2.1.214),
tier T1, dev seeds 0..49, held-out seeds 100..107, ceilings 5M
new-tokens / 40 h wall (harness/budget.toml). Raw record:
`h2_campaign.json` (copy; runs/ is gitignored).

## Headline: H2 met and exceeded

| Metric | Result | H2 target |
|---|---|---|
| Held-out pass@1 | **1.0** (8/8) | ≥ 0.90 |
| Held-out pass@8 | **1.0** | ≥ 0.99 |
| wrong_object (all 86 episodes) | **0** | 0 (H5) |
| Time to first verified success | 8.2 min | — |
| Budget used | 418,343 new-tokens (8.4%), 86 min wall (3.6%) | ≤ 5M / 40 h |
| Session end | `agent_done` (self-completed) | — |
| Frozen drift / infra errors | none / none | — |

Caveat stated plainly: the held-out sample is 8 seeds — 1.0 means
"no failures observed at N=8" (a 90%-true-pass1 system clears 8/8 about
43% of the time). The dev trajectory below and the 10-episode dev runs
are the stronger evidence of a real ≥0.9 system.

## The trajectory (per-rollout pass@1, chronological)

0.8 → 0.3 → 0.9 → 0.9 → 0.5 → 0.8 → 1.0 → holdout 1.0

The dips are the agent EXPLORING, not regressing blindly — each maps to
a logged idea with an honest verdict (HAR-8 idea tree, 8 hypotheses):

1. I1 (up): T0 expert pipeline transfers to T1 named-med-among-5.
2. I3 (down): grip-yaw policy v1 — the 0.3 rollout; abandoned.
3. I5 (up): grip policy v2 constrained to the baseline yaw envelope.
4. I7 (flat): shallow-grip fingertip clearance over tall neighbours.
5. I9 (down): front-approach override — the 0.5 rollout; abandoned.
6. I11 (down): off-center deep grip; abandoned.
7. I13 (up): slow the grasp-descend stage (vel 0.4) — the 1.0 rollout.
8. I15: generalization check on unseen dev seeds → shipped as the
   deliverable, which then scored 1.0 on the true held-out range.

This is the EN loop working as designed: hypothesize → roll out →
read the failure taxonomy → keep or revert, with losing ideas
explicitly recorded as `down` rather than silently discarded.

## Integrity

Zero `wrong_object` across all 86 episodes (H5 held under free
iteration on grasp/motion policy). Zero frozen-path drift. All rollouts
idea-gated and ledger-charged through the trusted origin/main gate.
Token counting via the tamper-immune live-pipe counter (PR #43); the
ceiling never bound — the session finished 12x under budget.

## Reading against H1

H1 (zero-shot): 15% valid-and-launching for the same model, dominated
by the registry-honesty gap (since closed by INSTALL_MISSING). H2 (with
the iterate loop): 100% held-out pass@1 in 86 minutes. The delta
between one-shot composition and budgeted iteration on the same
substrate is the experiment's clearest evidence so far that the
EN-loop, not composition alone, carries the capability.

## Follow-ups

- The wrong-medicine asymmetry remains untested at N large; the H3
  campaigns will accumulate far more episodes.
- Single arm (claude); a codex arm at the same treatment is one
  command when machine time permits.
- 8 held-out seeds is thin for a headline; widen the held-out range in
  the next campaign config (cheap: episodes are ~30 s).
