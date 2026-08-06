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
4. **Bias statement (corrected, PR #59 review).** The leak's DIRECTION
   is not causally established: leaked prior-scenario state could help
   arm W (extra capability) or hurt it (misleading notes/skills for a
   different scenario), and no counterfactual run exists to sign it.
   The remedy is therefore exclusion from the verdict plus a clean
   rerun — never a direction assumption.

## Amendment (resume, PR #61): arm-L residue policy, slot rotation, audit snapshots

The L/S2 resume after the Fable 5 quota abort exposed four runner gaps
(PR #60/#61 reviews); the policy changes are recorded here because they
alter arm-L treatment semantics and the audit surface:

1. **Arm L's persistence surface is the DEFINED library, enforced.**
   "Keeps everything" (D3) is narrowed to: registered skills (evalcarded
   manifest + `skills/<id>/` code), the read-only idea tree, and `runs/`
   (ledger + artifacts). Before every arm-L scenario after the first,
   `clear_nonlibrary_residue` removes stray untracked files,
   agent-committed files, and tracked modifications — the same leak
   classes as the arm-W wipe. Rationale: L/S1 left an unregistered
   `skills/s1-driver-v2/` and a working graph; carrying those into S2
   would be untreated cross-scenario state.
2. **Reruns carry only the tier's ORIGINAL library.** On `--attempt N>1`
   the guard is limited to the attempt-1 record's `prior_skills`, so a
   skill registered during a failed attempt cannot ride into its own
   rerun and read as prior-tier reuse.
3. **Occupied scenario slots rotate aside** (`<slot>-supersededN`)
   instead of being reused: `token_samples.jsonl` appends (an aborted
   prefix poisons tokens-to-first-success — observed live on the L/S2
   resume and repaired by splitting the file) and `session.jsonl` opens
   `w` (the aborted transcript would be destroyed).
4. **Keep-refs are snapshot commits.** `h3/keep-<arm>-pre-<slot>` now
   points at a commit (parent = pre-wipe HEAD) whose tree includes the
   UNTRACKED working state, so every removed file is recoverable via
   `git show <keep_ref>:<path>`. Agent-controlled manifest ids are
   validated (`^[a-z0-9][a-z0-9_-]*$`) before being used as path
   components. The treatment records `h3_runner_sha256` (this
   orchestrator's own hash) so these policy changes are visible in every
   campaign record; existing aggregates are backed up (`-prevN`) before
   a partial-arm invocation writes.

## Amendment (analysis, PR #90): admissibility semantics, owner-ratified 2026-08-05

The final-analysis round surfaced rules the original protocol left
implicit or got wrong. Each was ratified by the owner during the PR #90
review and is durable protocol from here on — `tools/h3_analysis.py`
implements exactly these, fail-closed:

1. **Treatment drift is ancestry + content, not one strict OID.** The
   pin's `git_sha`-equality rule misread the treatment: the agent's own
   commits on top of the pin ARE the treatment (an agent that authors
   skills necessarily moves its worktree HEAD). Drift is (a) post-pin
   origin/main history in the rollout's sha (merge-base against
   origin/main ≠ pin), or (b) a trust anchor whose COMMITTED frozen
   hash (`tools/env_hash.json` at the anchor ref) differs from the
   pin's. Rollouts recorded before annotation fall back to the strict
   sha/oid rule.
2. **Provenance fails closed.** A dev rollout whose provenance is
   neither recorded nor resolvable from the campaign worktree's run
   manifests — or whose lineage/anchor annotation is only half
   derivable — excludes its cell (`provenance_missing`). Absence of
   evidence is never admissibility.
3. **Attestation is judged by the PIN's protocol (grandfather-by-pin).**
   A pin that predates ADR-24 structurally cannot emit `env_attested`;
   null/missing at such a pin is not a flag, an EXPLICIT
   `env_attested: false` fails closed (`unattested_env`). Scope: this
   governs H3-campaign cell admissibility only — it claims nothing
   about CON-5 reproducibility, which continues to require attestation
   for any reproducibility claim (no spec exception intended or made).
4. **A nulled metric with trusted successes is re-derived, or the cell
   dies.** Where the pinned runner's superseded strict rule discarded
   trusted dev successes and nulled first-success, the analyzer
   re-derives it from primary timing evidence (earliest trusted
   success-manifest mtime minus the token-sampler-derived session
   start), marks it `first_success_rederived`, and fails closed as
   `metric_inconsistent` when the re-derivation is implausible
   (outside `(0, wall]`).
5. **The host dora runtime is part of the treatment** (PR #90 review 3,
   the S3-r3 lesson). The committed frozen hash cannot see an external
   executable: attempt 3 ran the post-#85 CLI/daemon (`cd597e705`)
   against the pin-era python API (`7eb4a5f8b`), an environment change
   on one arm mid-contrast — the cell is flagged `runtime_drift` and
   excluded, reverting S3 to undecided. Runtime identity is CONTENT,
   never a version string (round-4 review: dora's version output is
   only CARGO_PKG_VERSION — `7eb4a5f8b` and `cd597e705` both report
   `1.0.0-rc.4`, and the CLI never inspects the pinned python API).
   Going forward the runner records the resolved CLI binary's sha256
   (`host_dora_cli`) in the treatment at launch and BRACKETS every
   scenario — preflight, post-session (before holdout scoring), and
   post-holdout captures (round 5: preflight alone left the multi-hour
   session and holdout windows unguarded). The operator's pin-era hash
   assertion (`--expect-dora-sha256`) is REQUIRED — an optional
   expectation let the S3-r3 mismatch class self-certify clean — and
   launching against an unresolved or different binary refuses; any
   capture differing from the launch identity records `runtime_drift`
   and makes the campaign non-OK; and the analyzer flags any record
   whose recorded shas (preflight or rechecks) differ from the
   campaign's launch identity. Any future attempt at THIS pin must
   cargo-install the CLI at the pin's rev (`7eb4a5f8b`) into an
   isolated prefix and pass its hash at launch. Older records without
   a runtime capture are judged by external evidence where it exists
   (S3-r3's disclosed augmentation) and are otherwise grandfathered —
   all pre-#85 cells shared one runtime era.
6. **Mid-cell merges to origin/main are a protocol violation** even
   when content-equality rescues the record (attempt 3 survived only
   because `tools/env_hash.json` never changed): freeze origin/main
   while a cell runs, and pin campaign rollouts' `--env-baseline` to
   the campaign OID (issue #91).

## Amendment (session isolation, issue #96): the treatment boundary is the process environment, not the repository

Ratified by merge of the implementing PR. The annotated S3-r3
transcript (`analysis/transcripts/h3-L-S3-r3-annotated.md`, event
[21]) showed a campaign session reading the OPERATOR's `~/.claude`
memory — ambient state outside D3's defined persistence surface,
invisible to the wipe machinery, and asymmetric in principle. Rules:

1. **Campaign sessions run under an isolated home.** The runner
   rebinds `HOME` and `CLAUDE_CONFIG_DIR` to a per-session scratch
   directory (`isolated_session_env`); the operator's config, memory,
   and credentials directories are not reachable through the process
   environment. The only knowledge channels are the protocol-defined
   ones: the pinned worktree, the registered library, and (arm L) the
   read-only idea tree.
2. **Isolation is recorded, fail-closed.** Every scenario record
   carries `session_isolation` (the scratch paths); its ABSENCE in a
   future audit means an unisolated session. The launcher runs one
   trivial auth probe under the isolated env before any scenario
   spends budget and REFUSES the campaign on failure (credential
   stores keyed off the operator home break under isolation; a silent
   fallback would reopen the channel).
3. **Grandfathering:** all cells recorded before this amendment
   predate the rule — documented context (the transcript annotation
   and post-mortem carry the disclosure), not a retroactive flag.
