# AISLE project-quality roadmap

Date: 2026-08-31
Status: ACTIVE planning program
Umbrella tracker: [#359 — AISLE 10/10 project-quality
program](https://github.com/heyong4725/aisle/issues/359)

## Objective

Raise AISLE itself—not only its papers—from a strong research prototype into an
auditable robot-engineering benchmark and system that withstands critical
external review.

`10/10` is an aspiration, not a self-awarded result. Each dimension below has a
measurable exit gate. A gap closes only when the implementation and evidence
satisfy its issue's acceptance criteria; documentation alone cannot close an
experimental gap.

The hardware issue is the only work item explicitly blocked on equipment. Its
protocol and fixtures can be prepared before acquisition. All other foundation,
simulation, statistics, safety, integrity, and release work can proceed now.

## Dimension map

| Dimension | Starting review | Objective exit gate | Issues |
|---|---:|---|---|
| Novelty and potential | 6/10 | Public versioned benchmark, blind evaluation, external user, and a defensible boundary against adjacent systems | [#357](https://github.com/heyong4725/aisle/issues/357), [#358](https://github.com/heyong4725/aisle/issues/358) |
| Technical system quality | 7/10 | Equal-capability control, sealed evaluator, scoped actuation boundary, and adversarial instrument/safety tests | [#344](https://github.com/heyong4725/aisle/issues/344), [#348](https://github.com/heyong4725/aisle/issues/348), [#350](https://github.com/heyong4725/aisle/issues/350), [#353](https://github.com/heyong4725/aisle/issues/353), [#354](https://github.com/heyong4725/aisle/issues/354) |
| Experimental rigor | 3/10 | Session-level estimands, power, replication, randomized blocks, uncertainty/equivalence, no-fault controls, and retained exclusions | [#345](https://github.com/heyong4725/aisle/issues/345), [#346](https://github.com/heyong4725/aisle/issues/346), [#347](https://github.com/heyong4725/aisle/issues/347), [#349](https://github.com/heyong4725/aisle/issues/349) |
| Support for central claim | 2/10 | Replicated typed-dataflow versus monolithic causal study, with null/negative results accepted without reframing | [#344](https://github.com/heyong4725/aisle/issues/344), [#347](https://github.com/heyong4725/aisle/issues/347) |
| Physical-AI relevance | 3/10 | Positive non-oracle result plus SO-101 physical validation and a live-fault operation cell | [#346](https://github.com/heyong4725/aisle/issues/346), [#356](https://github.com/heyong4725/aisle/issues/356) |
| Reproducibility | 8/10 intent | Independent-machine reproduction, complete raw archive, one-command analysis, and DOI | [#353](https://github.com/heyong4725/aisle/issues/353), [#355](https://github.com/heyong4725/aisle/issues/355) |
| Clarity and focus | 4/10 | Mechanically checked claim/evidence matrix and one canonical external architecture/status narrative | [#358](https://github.com/heyong4725/aisle/issues/358) |

Safety is deliberately cross-cutting rather than hidden inside one score:

- [#350](https://github.com/heyong4725/aisle/issues/350) defines and attacks the
  agent-to-actuation threat boundary.
- [#351](https://github.com/heyong4725/aisle/issues/351) separates topology,
  kinematic enforcement, and empirical semantic outcomes.
- [#352](https://github.com/heyong4725/aisle/issues/352) decides whether AISLE
  can support an identity-aware semantic authorization claim; otherwise H5 is
  permanently narrowed.

## Execution waves

### Wave 0 — rules before results

- [#345 — session-level statistics and power
  analysis](https://github.com/heyong4725/aisle/issues/345)
- [#353 — treatment integrity
  v3](https://github.com/heyong4725/aisle/issues/353)
- [#354 — independent instrument audit and mutation
  benchmark](https://github.com/heyong4725/aisle/issues/354)
- [#358 — claim-to-evidence matrix and architecture
  narrative](https://github.com/heyong4725/aisle/issues/358)

Exit: experimental units, claim scopes, treatment identity, audit expectations,
and analysis decisions are mechanically explicit.

### Wave 1 — build fair instruments

- [#344 — equal-capability monolithic
  control](https://github.com/heyong4725/aisle/issues/344)
- [#346 — non-oracle reachable task
  band](https://github.com/heyong4725/aisle/issues/346)
- [#348 — sealed hidden fault bank and
  injector](https://github.com/heyong4725/aisle/issues/348)
- [#350 — actuation threat model and bypass
  validation](https://github.com/heyong4725/aisle/issues/350)
- [#351 — safety exposure and held-command
  ablation](https://github.com/heyong4725/aisle/issues/351)
- [#352 — semantic authorization
  boundary](https://github.com/heyong4725/aisle/issues/352)

Exit: expert parity, task reachability, blinding, confinement, and safety
evidence gates pass before confirmatory data collection.

### Wave 2 — confirmatory campaigns

- [#347 — replicated typed versus monolithic causal
  study](https://github.com/heyong4725/aisle/issues/347)
- [#349 — replicated typed-evidence versus logs-only fault
  study](https://github.com/heyong4725/aisle/issues/349)

Exit: analyzer-derived session-level treatment estimates, uncertainty, no-fault
false-alarm measurements, and all exclusions are retained. A null or negative
result is a valid exit.

### Wave 3 — independent and physical closure

- [#355 — independent-machine reproduction and DOI
  artifact](https://github.com/heyong4725/aisle/issues/355)
- [#356 — SO-101 physical benchmark and live-fault
  operation](https://github.com/heyong4725/aisle/issues/356)
- [#357 — versioned public benchmark and blind evaluation
  path](https://github.com/heyong4725/aisle/issues/357)

Exit: an external operator can run and verify the benchmark without the
original campaign machine, and physical claims rest on retained hardware
evidence.

## Program rules

1. Pilot data calibrates instruments and never enters confirmatory tables.
2. Every confirmatory study has a frozen estimand, experimental unit,
   power/precision target, stopping rule, exclusion policy, and analyzer.
3. Hidden seeds, faults, same-experiment findings, and reference repairs never
   enter an agent-visible tree.
4. Negative results close valid experiments; post-result claim changes do not.
5. Primary evidence is archived before session/worktree cleanup.
6. Cross-review follows CON-16; instrument audits should include a reviewer
   outside the affected authorship chain.
7. Hardware preparedness is not physical evidence.
8. This roadmap changes no normative requirement. Any implementation that does
   so needs the repository's ordinary spec-change process.

## Completion

Close [#359](https://github.com/heyong4725/aisle/issues/359) only after every
child issue is complete or explicitly dispositioned with a narrower claim, the
claim/evidence matrix has no unsupported headline claim, and an independent
reviewer audits the completed program.
