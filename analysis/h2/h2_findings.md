# H2 findings — measured T1 campaigns, three runs (design doc §8.3 item 6, hypothesis §6 H2)

Protocol: `tools/campaign.py` per ADR-h2-campaign-protocol. Same tier
(T1), dev seeds 0..49, held-out seeds 100..107, ceilings 5M new-tokens /
40 h wall. Raw records: `h2_campaign_claude.json`,
`h2_campaign_codex_clean.json`, `h2_campaign_codex_contaminated.json`.
All three ran the matched dora 0.5.0 runtime pair (worktree lockfiles;
verified during the PR #45 environment archaeology).

## The three runs, honestly labeled

| | claude | codex CLEAN | codex CONTAMINATED |
|---|---|---|---|
| Status | independent | **independent replication** | invalid as replication |
| Commit | `e8f163ab` | `e8f163ab` | `8eab2ca9` (contains the claude findings) |
| Model | claude-fable-5 | gpt-5.6-sol | gpt-5.6-sol |
| Held-out pass@1 / pass@8 | 1.0 / 1.0 | 0.875 / 0.875 (one `dropped`) | 1.0 / 1.0 |
| wrong_object (all episodes) | 0 / 78 | 0 / 88 | 0 / 58 |
| First verified success | 8.2 min | 8.6 min | 10.7 min |
| Session wall / tokens | 86 min / 418k | 49 min / 241k | 33 min / 165k |
| Ideas (verdicts) | 8 (3 up, 3 down, 1 flat, 1 ship) | 6 (3 up, 2 flat, 1 down) | 3 (3 up — all confirmations) |
| Ended | agent_done | agent_done | agent_done |

**H2 verdict: met by both independent arms.** Claude 1.0 held-out;
codex-clean 0.875 held-out with dev-side evidence of a ≥0.9 system (a
30-episode dev run at 0.967, plus three 10-episode runs at 0.9–1.0);
the single held-out failure is a `dropped` at N=8. The ≥90%-pass@1
target is comfortably supported; the ≥99% pass@8 target is only
formally met by the claude arm (retries are not yet distinguishable at
these sample sizes — both arms' pass8 equals pass1 because no
in-context retries occurred).

## The contamination lesson (why the third column exists)

The first codex run was launched from a commit containing
`analysis/h2/h2_findings.md` — the claude arm's write-up — inside its
own worktree. Its first idea reads "Prior H2 neighbour-aware grasp
scoring plus 0.4-speed shelf descent transfers…": it read the committed
findings and confirmed them, 1.0 from the first rollout, no exploration.
**Committed analysis of the same experiment is an experimental input to
a repo-reading research agent.** The `--commit` pin (PR #46) exists so
replication arms predate any such analysis; ADR-h3 inherits this as a
worktree-isolation requirement (the same channel D3 worries about via
the registry, arriving through git).

Relabeled, the contaminated run is an accidental **knowledge-transfer
datapoint**: handed prior findings, the agent converged with 2.5x fewer
tokens and 2.6x less wall time than its own clean replication — the H3
phenomenon, observed through documentation rather than the skill
library.

## What the clean codex arm did (independent narrative)

Six ideas with honest verdicts: transfer of the T0 stack (up), a
narrow-pinch-axis recovery for a failing seed (flat, twice), baseline
sustains ≥90% with zero wrong_object (up), generalization across all
dev seeds (up), a targeted 10 mm hover fix for one seed's post-release
topple (down — reverted). Notably, it used **`harness skill register`
unprompted** — three registration evals (pass1 0.8–0.9) and one
authored skill (`stable-narrow-grasp`) — the first organic use of the
T18 pipeline by a research agent.

## Cross-arm reading

- Both independent arms found materially the same solution family
  (neighbour-aware grasp selection on the oracle-pose stack) by
  different routes and with different polish; both preserved
  wrong_object = 0 under free motion-policy iteration (H5, now
  0 / 224 episodes across all three runs).
- H1 vs H2 stands sharpened: 15% (claude) and 65% (codex) zero-shot
  valid-and-launching, versus 1.0 and 0.875 held-out with the iterate
  loop — the EN loop, not composition alone, carries the capability,
  for both vendors.
- Runner integrity features all fired in anger across these runs: the
  live-pipe counter, the post-session orphan sweep (8 nodes reaped
  automatically in the clean run — the leak that twice corrupted
  timing tests), the frozen-path audit (clean, 3/3), and the ledger.

## Follow-ups

- Widen held-out N (8 seeds is thin; the codex-clean 0.875 vs dev 0.967
  gap is within noise at this size).
- pass@8 semantics need in-context retries to be exercised (T4
  territory) before the ≥99% target is meaningfully testable.
- The codex arm's mid-turn token blindness (usage only at
  turn.completed) leaves its live ceiling wall-only; acceptable,
  recorded in ADR-h2 limitations.
