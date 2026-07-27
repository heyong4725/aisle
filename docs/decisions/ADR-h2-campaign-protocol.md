# ADR-h2-campaign-protocol — single-scenario research campaign runner (design doc §8.3 item 6)

Status: accepted (CON-15: §8.3 names the campaign but not the mechanics;
interpretations recorded here). Artifact: `tools/campaign.py` (Class A) +
tests/unit/test_campaign.py. Hypothesis served (§6 H2): with the EN loop,
the agent raises T1 success to ≥90% pass@1 within a fixed budget. The
same runner is the per-scenario core the H3 orchestrator
(ADR-h3-campaign-protocol, DRAFT) will drive.

## Protocol

1. **One campaign = one pinned worktree + one research session.** The
   worktree detaches at an OID resolved at start (CON-5) outside the
   repository, `uv sync`ed. The session runs under the in-worktree
   research contract (`harness/CLAUDE.research.md`); the prompt names
   the tier goal, the deliverable graph (`graphs/agent_campaign.yaml`),
   and the budgets, and points at the contract. T1 only until the
   realistic verifier lands (T2 needs perception L2).
2. **Rollouts happen INSIDE the session** — the whole point of H2 vs H1.
   `harness rollout` runs with `--env-baseline origin/main` (the ADR-21
   trusted gate): frozen-set integrity, idea gating (HAR-8), and the
   campaign ledger's episode/wall ceilings are enforced by the harness
   the agent itself invokes; the runner does not re-implement them.
3. **The runner enforces what the harness cannot**: the TOKEN ceiling
   (HAR-5 — cumulative usage parsed from the agent CLI's own stream
   telemetry; at the ceiling the session process group is killed and the
   campaign records `stopped: token_budget`) and the campaign WALL
   ceiling (same kill path, `stopped: wall_budget`). A session that ends
   itself records `stopped: agent_done`.
4. **Scoring: held-out seeds, post-session, in the session worktree.**
   H1's clean-worktree isolation existed for zero-shot attribution; a
   campaign's product is the workspace (graphs + authored nodes +
   registered skills), so the honest measure is the deliverable graph
   rolled out BY THE RUNNER after the session on a held-out seed range
   DISJOINT from the dev range (refused otherwise), through the same
   trusted gate (per-run env attestation makes mid-campaign frozen-set
   tampering a recorded refusal, not a scoring input).
5. **No session sandbox in v1** (recorded in treatment + limitation):
   campaign rollouts spawn dora/genesis with cache and tmp writes that
   the H1 SBPL/workspace-write profiles were never shaped for; integrity
   rests on the trusted gate, the ledger, and the post-session workspace
   audit (frozen paths diffed against the pinned OID — any drift is
   reported; the gate would have refused those rollouts anyway).
6. **Resume = same worktree, fresh session, same budgets.** The
   workspace (files, idea tree, registered skills, ledger) is the
   campaign's persistent memory; conversation state is not. Treatment
   mismatch on resume refuses, as in H1. Token/wall spend accumulates
   across resumed sessions.
7. **Metrics** (from artifacts the harness already writes — run
   manifests, episodes.jsonl, ledger, idea tree): time- and
   tokens-to-first-verified-success, per-rollout pass1 trajectory,
   held-out pass1/pass8, failure histogram, wrong_object total (H5:
   MUST be 0), ideas opened/closed, episodes/wall/token spend vs
   ceilings.
8. **Treatment pinning** as in H1 (commit OID, model, CLI version,
   prompt sha256, runner sha256, session_spawn config, budgets), plus
   the dev/held-out seed ranges.
9. **Failure attribution** as in H1: agent outcomes (budget kill, no
   deliverable graph, refusals) are complete records; infra failures
   (worktree/uv/CLI crash) abort into runner-error records and the
   campaign resumes after the fix.
10. **One campaign at a time on the machine** (sim contention corrupts
    both the campaign's own rollouts and any concurrent scoring).

## Limitations (recorded)

- Unsandboxed sessions (point 5): a hostile agent could write outside
  the worktree; the audit detects frozen-path drift but not arbitrary
  host writes. A worker that `setsid`s out of the session's process
  group also survives the budget kill and spends outside the counted
  pipe (PR #43 review) — neither ceiling truly bounds an escaped
  process. Acceptable for supervised runs; revisit before unattended
  fleet mode (§8.4 item 3).
- Token counting reads the vendor CLI's self-reported usage events;
  HAR-5 already treats token accounting as external/best-effort.
  **Budget semantics (decided at the dry run, human choice): NEW tokens
  only** — each token counts once when first processed (claude: input +
  cache_creation + output, cache re-reads excluded; codex: input minus
  cached_input, plus output). The dry run showed the alternatives are
  both absurd: uncached-input-only read 856 tokens for a 91-message
  session (ceiling can never fire); face-value-with-cache-reads read
  5.49M for 18 minutes (the whole 5M budget buys one session).
  New-tokens-only read 202k — the 5M ceiling ≈ 25 such sessions,
  matching the design-era budget intent. Corollary (PR #41 review): a
  cache-read-heavy or idle session accrues ~0 new tokens, so for such
  workloads the WALL ceiling is the operative limiter, not the token
  ceiling — that is the accepted trade of this rule.
- codex sessions get `--sandbox danger-full-access` for parity with
  point 5; both arms' confinement is recorded in `session_spawn`.
