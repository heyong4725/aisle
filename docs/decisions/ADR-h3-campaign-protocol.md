# ADR-h3-campaign-protocol — H3 accumulation campaign, S1→S2→S3 (design doc §11.5, §8.4)

Status: accepted 2026-07-27 (decisions D1–D6 resolved by human; D1
claude-only, D2–D6 as recommended). Artifact: `tools/h3_campaign.py`
(Class A) + tests/unit/test_h3_campaign.py, driving the ADR-h2 session
machinery per scenario.
Hypothesis under test (§6 H3): a persistent skill library cuts
time-to-success on later scenarios by ≥2x vs a memory-wiped agent (the
ASPIRE effect). The retail suite is the strongest testbed: S1, S2, S3
share navigation, shelf perception, and placement skills almost
entirely (§11.5), so the S1→S2→S3 transfer curve is the headline
accumulation figure.

## Design

Two arms, same agent model, same scenario sequence S1 → S2 → S3, one
scenario at a time, fresh agent session per scenario in BOTH arms
(conversation memory never persists; the *library* is the treatment):

- **Arm L (library-persisted).** Skills registered during S1 (manifest +
  evalcard via `harness skill register`, T18) remain installed in
  `registry/manifests/` for S2; S1+S2 extras remain for S3. The
  `skills/` source directories persist with them. The idea-tree log
  persists read-only as prior-campaign evidence (see D3).
- **Arm W (library-wiped).** Before S2 and S3, every agent-authored
  registry extra and `skills/` directory is removed; the registry is
  restored to the curated core (`registry/schema/curated_core.toml`,
  CAP-5/CAP-7). The agent starts each scenario with only hub
  capabilities, like a fresh S1 agent.

Arms run SEQUENTIALLY on the one machine (no sim contention; same
exclusivity rule as ADR-h1-protocol point 10). Arm order: W first, then
L (D6 rationale).

## Protocol

1. **Pinned treatment (CON-5).** One commit OID resolved at campaign
   start; both arms and all six scenario sessions run against it. The
   results record OID, agent CLI version, model id, contract sha256
   (`harness/CLAUDE.research.md`), per-scenario budgets, and argv.
   Mid-campaign human help ONLY via committed CLAUDE.research.md diffs,
   each reported with the results (contract rule §0) — any such diff
   applies to BOTH arms' remaining scenarios or is void.
2. **The research contract governs the sessions.** Each scenario session
   is a fresh agent run under `harness/CLAUDE.research.md`: frozen-set
   enforcement fetches origin/main at gate time (ADR-21,
   `--env-baseline origin/main` — the trusted path, NOT the local
   override), every rollout requires an open idea (HAR-8), and the
   campaign ledger meters episodes/wall against `harness/budget.toml`.
3. **What persists between scenarios (arm L).** Installed manifests +
   evalcards + `skills/` sources — and the CLOSED idea-tree entries of
   earlier scenarios, readable but append-only (the §8.4 "peer
   summaries" analogue for a single agent across time). Workspace,
   session, and conversation state do NOT persist in either arm.
4. **What "registered" means mid-campaign.** `harness skill register`
   installs the evalcarded manifest locally (T18); the human-merge PR
   (contract rule 4, §9.4) is opened by the agent but merged
   POST-SCENARIO in batch, so human review latency never enters the
   wall-clock measurement. A skill that fails its own eval is not
   installed and not counted. (Decision D4 below.)
5. **Primary metrics, per scenario per arm.**
   - time-to-first-verified-success (wall-clock from session start to
     the first rollout episode scored `success` by the frozen oracle)
   - tokens-to-first-success and tokens total (session telemetry, HAR-5)
   - pass@1 at budget end: the agent's best graph re-scored in a CLEAN
     worktree at the pinned OID, 8 fixed held-out seeds per scenario
     (ADR-h1-protocol point 3 scoring isolation)
   - skill-reuse count: capabilities in the scored graph whose evalcard
     was registered in an EARLIER scenario (the transfer signal)
   - wrong-item/penalty classes (H5 sidebar: must stay 0)
   H3 verdict: arm L's S2 and S3 time-to-first-success ≤ 0.5x arm W's
   (design doc "≥2x"). Tokens-to-success reported alongside as the
   secondary ratio.
6. **Seeds and episode generators.** Each scenario has a FIXED
   development seed range the agent may roll (recorded in the ledger)
   and a DISJOINT held-out scoring range used only by the clean-worktree
   scorer. Identical ranges across arms.
7. **Budgets per scenario.** The campaign ceilings
   (`harness/budget.toml`: 5M tokens / 500 episodes / 40 h) cover the
   WHOLE campaign; per-scenario sub-budgets are enforced by the runner
   (session killed at budget, best-so-far graph scored). Proposed split
   per arm — S1: 1.0M tokens / 80 episodes / 6 h; S2: 0.75M / 60 / 5 h;
   S3: 0.75M / 60 / 5 h (total per arm 2.5M / 200 / 16 h, both arms
   within one campaign ledger's ceilings with headroom for re-runs).
   (Decision D2.)
8. **Failure attribution.** As in ADR-h1-protocol: agent outcomes
   (timeout at budget, no working graph, refusal) are COMPLETE records;
   infrastructure failures (worktree/uv/CLI crash, orphan interference)
   abort the scenario into a runner-error record and the scenario
   re-runs after the fix with a new run id — never silently resumed.
   Orphan reaping runs between scenarios (known dora leak).
9. **Confound controls.** Same model + CLI version both arms; arm W
   runs FIRST so any undiscovered harness defect is found on the arm
   whose result H3 predicts to be slower (a defect-caused slowdown on W
   biases AGAINST H3's ≥2x claim rather than for it — see D6); scenario
   order is never varied (the curve is the object of study, not an
   order ablation); no mid-campaign repo updates except contract diffs
   per point 1.
10. **Artifacts.** Per scenario: run manifests + ledger entries + idea
    tree + registered skills + scored-graph results, all committed under
    `analysis/h3/` (results JSON + the transfer-curve plot). The
    protocol runner is `tools/h3_campaign.py` (Class A, unit-tested like
    `tools/h1_protocol.py`), driving sessions with the same
    telemetry/shim machinery as H1 where applicable.

## Decisions (resolved 2026-07-27)

- **D1 — RESOLVED: claude-fable-5 only** (one arm-pair; a codex pair
  is a later A4-style extension).
- **D2 — RESOLVED: the point-7 split** (2.5M/200/16 h per arm; the
  40 h wall ceiling covers both arms given sessions rarely exhaust
  their wall budgets — H2 sessions used 33–86 min).
- **D3 — RESOLVED as proposed:** arm L's idea tree persists read-only
  ("the library's lab notebook"; the ASPIRE analogue). Arm W wipes it.
- **D4 — RESOLVED as proposed:** local install counts; agent-opened
  PRs batch-merged post-scenario (human latency stays out of
  wall-clock).
- **D5 — RESOLVED as proposed:** every scenario session in BOTH arms
  gets the identical nudge line "distill what works into registered
  skills — they may pay off later" (not a treatment; H2's clean codex
  arm registered skills unprompted, so the floor risk is low).
- **D6 — RESOLVED as proposed:** W-first (point 9's bias-against-H3
  rationale).

## Prerequisites (first two verified at acceptance; dry run pending)

- H1 protocol results landed (same session machinery is reused; H1 is
  the shakedown).
- `tools/h3_campaign.py` implemented + unit-tested (wipe/restore of
  registry extras is the new mechanism: byte-exact restore of curated
  core state, verified by the registry lint).
- A dry-run S1 scenario session (small budget, e.g. 0.2M/16 episodes/
  90 min) to validate session driving, ledger accounting, and the
  skill-persistence/wipe mechanics end to end before the measured runs.

## Amendments at acceptance (H2 lessons)

- **Pre-analysis pinning (PR #46):** the campaign OID is chosen at start
  and MUST predate any committed analysis of the H3 experiment itself;
  committed analyses of OTHER experiments present at the pin (e.g.
  analysis/h1, analysis/h2) are recorded in the treatment as ambient
  context — the desk-tier findings do not answer the store scenarios,
  but the record keeps reviewers honest.
- **Arm-W wipe surface:** registry/manifests and skills/ restored
  byte-exact to the pinned OID, agent-authored graphs and runs/ideas/
  removed; the campaign LEDGER and run artifacts persist (budget
  continuity is global). Arm L keeps everything (D3).
- **Episode sub-budgets are advisory in v1:** the frozen ledger enforces
  the GLOBAL episode/wall ceilings; per-scenario episode caps are
  recorded targets, enforced only via the per-scenario token/wall kills.
- **Post-session orphan sweep** (PR #46) runs between scenarios.

## Amendment (campaign 2, PR #57): wipe leak, keep-refs, reruns

Campaign 2 (2026-07-28) exposed a wipe leak: the S1 agent COMMITTED its
skill (s1-driver-v2) and research notes on its worktree branch, and both
`git checkout <pin> -- .` and `git clean` skip committed-but-not-in-pin
files — the wiped arm ran S2 and S3 with prior-scenario memory (their
records show `prior_skills: ["s1-driver-v2"]`). Amendments:

1. **Wipe = detach at the pin.** `wipe_library` now runs
   `git checkout -f --detach <pin>` before the clean, so the working
   tree ends byte-exact at the pin including agent-committed files.
2. **Scenario HEADs stay durably reachable.** Before detaching, the
   pre-wipe HEAD is pinned under `h3/keep-<arm>-pre-<slot>` and its hash
   recorded in the wipe report (`detached_from`, `kept_ref`, persisted
   in h3_results.json `wipes`); every scenario record now carries its
   final `worktree_head`. Detached-HEAD commits can never be orphaned
   to the reflog.
3. **Contaminated cells are excluded and rerun.** W/S2 and W/S3 of
   campaign 2 carry the `wipe_leak` flag (derived from the records by
   tools/h3_analysis.py) and are EXCLUDED from the H3 verdict; they
   remain in the table as history. After the campaign completes, both
   are rerun with the fixed wipe under NEW ids
   (`--arms W --scenarios S2,S3 --attempt 2` → `S2-r2`/`S3-r2` scenario
   dirs, `campaign-holdout-W-S2-r2`-style holdout run ids); the verdict
   uses the highest-attempt unflagged cell per (arm, tier).
4. **Bias statement.** The leak's direction is conservative for H3 (a
   leaky wipe makes the wiped arm look MORE capable, shrinking the W–L
   gap); stated here so the rerun's purpose is on the record.
