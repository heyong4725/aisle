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
2. **`harness skill register skills/<name>` = validate → eval →
   evalcard → install.** Schema lint (CAP-3, reusing the registry's
   machinery) and governance checks run BEFORE any eval spend; the
   shipped rollout measures pass1; a result under the skill's own
   `min_pass_rate` refuses installation; success writes the CAP-1
   evalcard `{suite, pass_rate, last_run}` (clock injected, CON-5) and
   installs the manifest into `registry/manifests/`, where the validator
   and `registry search` treat it like any capability.
3. **Governance is the PR, not the CLI** (§9.4): the CLI only writes
   files; a human merges the PR carrying the skill + its installed
   manifest. Two hard refusals encode the trust boundary: `origin` must
   be `agent-authored` (hub manifests are curated by hand), and a skill
   may never shadow a curated core id.
4. **CAP-5's completeness pin evolves, not breaks**: the curated core
   ids remain pinned exactly; any manifest beyond them must be
   agent-authored WITH a non-null evalcard — the registration path is
   the only way in. The deliberate rearrangement gap now binds the
   CURATED set (an agent-authored rearrangement skill is the intended
   fill).
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
