# Annotated agent transcript — H3 arm L, S3 attempt 3 (2026-08-05)

Phase 1 DoD artifact (design doc §8.2: "one full agent transcript
annotated by the team — read what the agent actually did"). Source:
`h3-L-S3-r3-session.jsonl` — the full 520-event transcript, committed
ALONGSIDE this annotation so the citations are auditable (a verbatim
copy of the live tree's `runs/h3/arm_L/S3-r3/session.jsonl`; 52 turns,
71 min, `agent_done` voluntarily at 253,776 of 750,000 tokens). Event
indices `[n]` are ZERO-BASED event positions into that file (line
n+1). This is the session whose
cell was ultimately excluded from the H3 verdict for `runtime_drift`
(PR #90) — procedurally it is the cleanest campaign session on record,
which is exactly why it is the one worth annotating.

## Timeline with annotations

**[3–10] Contract first, then survey.** The session's first action is
reading `harness/CLAUDE.research.md` in full, then a worktree survey
(git log, registry state, runs ledger). No work before orientation.

> *Annotation:* the research contract's "read first" instruction is
> followed literally. Sessions that skipped this in H1/H2 wasted early
> budget rediscovering harness rules by trial and error.

**[21] ⚠ The agent reads the OPERATOR's memory directory.**

```
cat ~/.claude/projects/-Users-yonghe-src-aisle/memory/aisle-s3-feasibility-geometry.md \
    ~/.claude/projects/-Users-yonghe-src-aisle/memory/aisle-s1-failure-geometry.md \
    ~/.claude/projects/-Users-yonghe-src-aisle/memory/aisle-prewipe-snapshots-hold-lost-work.md ...
```

> *Annotation — the most consequential observation in this transcript.*
> These files are the DEVELOPMENT agent's persistent memory: operator
> notes distilled from earlier sessions, including S3 feasibility
> conclusions ("only both-L1 swaps winnable", counter-buffer strategy)
> derived from analysis of PRIOR CAMPAIGN RUNS — including the wiped
> arm's. The H3 treatment defines arm L's persistence surface as the
> registered library + read-only idea tree (ADR-h3 D3); the operator's
> `~/.claude` memory is AMBIENT STATE outside that definition, invisible
> to the wipe machinery, and asymmetric (arm W sessions could read the
> same files). Nothing in the contract forbade it; the agent did the
> locally-rational thing. Consequences: (a) this cell was already
> inadmissible on `runtime_drift`, so no verdict changes; (b) any future
> campaign MUST isolate the session's config/home so the only knowledge
> channels are the ones the protocol defines (tracked as issue #96);
> (c) H5-style claims about "what the agent figured out itself" must
> discount anything present in these notes.

**[32–55] Git archaeology → resurrection strategy.** The agent inventories
all branches and keep-refs, finds the pre-wipe snapshot mechanism
(`h3/keep-*`), locates the prior registration's eval-of-record commit
(`1b72d33`) and diffs it against the current library copy of
`skills/s3-driver-v1/`.

> *Annotation:* the arm-L treatment working as designed — the library
> persists, and the agent VERIFIES the skill's eval-of-record hash
> rather than trusting the working tree. The keep-ref audit machinery
> (PR #61) is being used by the agent itself, not just by auditors.

**[144] Idea logged before any rollout (HAR-8).**

```
harness report log --idea "resurrect registered s3-driver-v1 (eval-of-record 1b72d33, 6/6):
counter-buffered 3-move swap on both-L1 feasible class; refuses L0 swaps (idle no-op)"
--expect "pass1 6/6 on dev seeds 19,28,33,34,35,36; 0 extra_item/wrong-item penalties"
```

> *Annotation:* a falsifiable pre-registration — named mechanism, named
> seed set, named penalty expectations. This is the idea-tree discipline
> the design doc calls "pre-registered hypotheses" (§9), functioning.

**[166–205] First rollout batch; the trusted-gate wall.** The first
`harness rollout --env-baseline origin/main` refuses: the PINNED
worktree's copy of the env-hash checker is stale relative to
origin/main's blob.

**[188]** — the agent's reasoning, verbatim:

> "The gate wants the local checker byte-identical to origin/main's
> blob. The frozen env itself already matches; only the tooling is
> stale. Let me see what changed in the checker at origin/main before
> syncing it."

> *Annotation:* the agent independently derives the content-vs-revision
> distinction that the owner later ratified as the ancestry+content
> admissibility rule (ADR-h3 PR #90 amendment §1), inspects the diff
> before trusting it, syncs ONLY `tools/env_hash.py`, and — see [379] —
> DISCLOSES the sync in the idea close. Compare L/S3-r2, where the
> operator merged all of origin/main into the worktree and drifted the
> treatment: same wall, opposite discipline, opposite outcome.

**[296–379] Second batch; idea closed with evidence.** Seeds 34,35,36
pass; I12 closes `--verdict up` citing both run IDs, 6/6, zero
penalties, and the tooling sync disclosure. These two runs are the dev
successes behind the cell's re-derived 29.9-min first-success metric.

> *Annotation:* dev seeds were CHOSEN from the known-feasible both-L1
> class — legitimate under the fixed dev-seed rules, and exactly why
> dev pass1 6/6 coexists with holdout 0/8 (seeds 100..107 sit almost
> entirely outside that class). The dev metric measures reaching
> competence on the feasible class, not transfer.

**[401–493] Negative-space testing, unprompted.** A second idea (I14)
pre-registers the REFUSAL behavior — "driver refuses L0-involving swap
at plan time and idles for the full episode" — and verifies it on seed
0: no motion commands, no collisions, timeout-class failure only.

> *Annotation:* the agent tests what its deliverable must NOT do. H5's
> delivery-precision sidebar is served by exactly this kind of evidence,
> and no prompt asked for it.

**[510–518] Deliverable-hash consistency, then a deliberate stop.**

> "All three runs used the identical graph (`4e75b8a5…`, matching the
> registration eval-of-record hash) and the deliverable still validates.
> The campaign state is complete — remaining budget can't buy more
> verified success (the feasible class is saturated at 6/6, the
> infeasible class is verified safe, and L0 is a closed branch across
> three prior sessions), so I'm stopping deliberately under budget."

> *Annotation:* a stated stopping argument — saturation, safety, closed
> branches — instead of budget exhaustion. Of the ten H3 cells, the two
> that ended `agent_done` (this and W/S2-r2) are the only ones where the
> session itself asserted its evidence was complete.

## What this transcript teaches (the §8.2 exercise)

1. **Ambient state is a treatment surface.** The wipe machinery covers
   the worktree; it does not cover the operator's own accumulated
   notes. Protocol fix required before any future campaign (issue #96).
2. **Walls get climbed with whatever discipline the contract taught.**
   The trusted-gate refusal produced a minimal, disclosed, content-equal
   sync — because the contract and prior review history modeled that
   norm. Contracts teach by precedent, not just by rule.
3. **Chosen dev seeds are informative about the choice, not the tier.**
   Feasibility-class selection is rational agent behavior; scoring
   design must (and does) rely on the disjoint holdout for transfer.
4. **The cleanest session can still be inadmissible.** Every discipline
   above was followed, and the cell fell to a host CLI rebuild the
   session could not observe. Environment identity is not the agent's
   to guarantee — which is why it is now the runner's (PR #90 rounds
   4–6).
