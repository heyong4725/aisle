# ADR-20 — The research contract is code-pinned prose (T17)

Status: accepted (CON-15: the design doc mandates harness/CLAUDE.research.md
— §8.2.3 and §5 — but no spec MUST governs its shape; the agent picks and
records). Task: T17. Relates to [[ADR-19]] (S2/S3 rollouts, the campaign's
other prerequisite).

## Decision

`harness/CLAUDE.research.md` is the RESEARCH-agent contract (the agent
that runs INSIDE experiments; development agents keep the repo CLAUDE.md).
Its load-bearing content is pinned to code truth by unit tests
(tests/unit/test_research_contract.py) so prose cannot drift from the
implementation:

1. **≤ 300 lines** — §8.2.3's explicit token budget, enforced.
2. **Goal verbatim** — the §1 quote, including the 10x asymmetric
   penalty sentence.
3. **The frozen set is enumerated from tools/env_hash.py** (CON-7):
   every FROZEN_DIRS/FROZEN_FILES entry must appear, so "don't edit
   env/" can never under-describe the real fingerprinted set.
4. **The failure glossary derives from the code constants** — every
   class in verifier.oracle.FAILURE_CLASSES (VER-3) and
   verifier.retail.RETAIL_FAILURE_CLASSES (RS-4) must be glossed.
5. **CLI examples are parse-tested** — every fenced `uv run harness ...`
   example must parse against the REAL argparse tree (CON-8);
   `build_parser()` was extracted from `cli.main` for exactly this.
   Registry examples are checked against the registry CLI too (CAP-4).
6. **Idea gate and budget semantics stated** — HAR-8 (idea before
   rollout) and HAR-5 (ANTHROPIC_TOKENS_LOG) are named, as are all
   tiers T0–T4 / S1–S3 and the HAR-3 pass@8 semantics.

## Why

§8.2.3: "agents and students both learn from examples, not descriptions"
— an example that no longer parses is worse than no example. The same
argument applies to the frozen list and the taxonomy: the contract is
the one document the research agent re-reads every session, so its
claims are tested like code.
