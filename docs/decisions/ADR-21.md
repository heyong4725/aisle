# ADR-21 — Trusted frozen-set baseline; enforced campaign budgets (T17 round 2)

Status: accepted (CON-15). Task: T17 (PR #24 review). Relates to
[[ADR-20]] (the code-pinned research contract this hardens).

## Problem (PR #24, two P1s)

1. The frozen-set gate compared the working tree against a WORKING-TREE
   json: a research agent could edit frozen code, rerun
   `env_hash.py --write` (or rewrite the checker), and rollout would
   bless the change.
2. The contract described budget *semantics* but no actual ceilings, and
   the harness enforced nothing — unusable for equal-budget campaigns.

## Decisions

1. **Trusted baseline from the git object store.**
   `env_hash.py --check --baseline <ref>` reads the committed
   fingerprint from `<ref>` (default `origin/main` — a protected branch
   the agent cannot move) via `git show`, and FIRST verifies the checker
   itself against its own blob at `<ref>`: a rewritten checker is
   refused before it can bless anything. `harness rollout` uses the
   trusted mode by default. `--env-baseline local` remains for
   human-reviewed development branches (env-change PRs legitimately
   diverge from origin/main); it is recorded in every run manifest, so a
   campaign audit finds any run that used it. The trust boundary stated
   honestly: an agent with a shell can bypass anything local — the
   enforceable properties are refusal-by-default plus tamper EVIDENCE in
   artifacts, both of which the audit checks.
2. **Campaign ceilings live in `harness/budget.toml`, which is FROZEN**
   (added to env_hash FROZEN_FILES): an agent cannot raise its own
   budget. Ceilings: tokens 5,000,000 (design §9.5), episodes 500
   (§8.2.3's episodes-left semantics), wall 40 h.
3. **Enforcement split by measurability.** The harness meters what it
   can observe: every trusted-baseline rollout charges
   `runs/campaign_ledger.jsonl` (episodes, wall seconds), the HAR-2 gate
   REFUSES a request past the episode or wall ceiling
   (`gate: budget`), and every report returns
   `budget: {episodes_left, wall_h_left, tokens_ceiling}`. Tokens are
   countable only by the LLM harness (HAR-5), so the token ceiling is
   stated and reported while spend is logged via `ANTHROPIC_TOKENS_LOG`
   in manifests for the external audit. Dev-override runs do not charge
   the ledger — the budget meters the campaign, not development.

## Evidence

- tests/unit/test_env_hash.py::TestTrustedBaseline — the review's attack
  verbatim (edit frozen file + regenerate json passes LOCAL check, fails
  TRUSTED check), checker tampering refused, missing ref explicit.
- tests/unit/test_idea_gate.py — budget gate refusals (episodes, wall),
  remaining-budget reporting, env_baseline recorded.
- tests/unit/test_research_contract.py — the contract's ceilings pinned
  to budget.toml; the trusted-baseline rule stated.
