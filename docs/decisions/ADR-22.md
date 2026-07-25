# ADR-22 — Skill registration mechanics (T18)

Status: accepted (CON-15: design doc §8.4 item 1 / §3 rule 3 / §9.2
define WHAT registration does; the mechanics are recorded here). Task:
T18. Artifact: `src/aisle/harness/skill.py` + `harness skill register`.
This is the load-bearing prerequisite for the H3 campaigns: the skill
LIBRARY is what the S1→S2→S3 transfer curve measures.

## Decisions

1. **A skill is a self-contained directory** (§9.2): `skills/<name>/`
   holding `skill.yaml` (a CAP-1 manifest, `origin: agent-authored`,
   `eval: null` until registered), the node code or subgraph YAML the
   manifest's `source` points at, and `eval.yaml` — the shipped
   mini-rollout config that IS the skill's eval suite: {suite, graph,
   tier, episodes, seeds, embodiment, min_pass_rate}.
2. **`harness skill register skills/<name>` = validate → STAGE →
   lint → eval → evalcard → final lint** (revised per the PR #30
   review). The candidate manifest is STAGED into the registry before
   its eval — carrying a clearly-labelled provisional evalcard so
   CAP-6's motion gate can pass during the skill's own evaluation
   (otherwise a fresh candidate is MANIFEST_MISSING to the HAR-2
   validator, and an update would evaluate the OLD manifest). The whole
   registry is linted after staging and after finalization (CAP-2/3);
   the eval graph must USE the candidate (a node with the skill's id —
   an unrelated green graph cannot mint the evalcard); default eval run
   ids are uniquified so same-day retries work; ANY failure rolls the
   registry back byte-for-byte.
3. **Governance is the PR, not the CLI** (§9.4): the CLI only writes
   files; a human merges the PR carrying the skill + its installed
   manifest. Hard refusals encode the trust boundary: `origin` must be
   `agent-authored`, and curated-core ids are refused from the
   single-sourced Class-C list `registry/schema/curated_core.toml`
   REGARDLESS of current file state — deleting a core manifest opens
   nothing.
4. **CAP-5 amended by spec-change (CON-14)**: the curated core is
   pinned exactly and single-sourced; extras must be evalcarded
   agent-authored skills. The amendment is its own spec-change PR (the
   prerequisite of this task), not a test rewrite.
5. **Eval rollouts run `--env-baseline local` + `no_idea_gate`**
   (defaults overridable in eval.yaml): registration is library
   machinery, not campaign research spend — both flags are recorded in
   the eval run's manifest as always (ADR-21 semantics).
6. **Deferred, deliberately**: subgraph NESTING in the validator/graphs
   (schema already admits `kind: subgraph`; a registered subgraph skill
   lints and installs, but graph-level flattening is composer work),
   skill VERSIONING (`skill:<name>/<ver>` trace spans need a schema
   `version` field — a Class C change to take with the subgraph work),
   and multi-tier/per-embodiment evalcards (schema holds one
   pass_rate).

## Evidence

tests/unit/test_skill_register.py — happy path (evalcard written,
manifest installed, shipped config drives the rollout), every refusal
(below threshold, core-id shadow, non-agent origin, schema-invalid
before any eval spend, missing eval suite, failed eval run), CLI
surface + CON-8 JSON contract. tests/unit/test_manifests.py pins the
evolved CAP-5 rule.
