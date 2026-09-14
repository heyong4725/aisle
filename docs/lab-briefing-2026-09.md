# AISLE in one hour — a briefing for a research group

<!-- status-snapshot:2026-09-14 canonical:../README.md#status -->
*Written 2026-09-14 at commit `fa339ee` for a visiting research lab. Every
number here is copied from the [README status table](../README.md#status),
which is canonical and wins on conflict. This page adds no claims; it orders
the existing ones for a first conversation.*

## 1. What AISLE is, in three sentences

AISLE is an environment plus the infrastructure around it for studying one
question: **can AI coding agents autonomously build, diagnose, improve, reuse,
and safely operate robotic systems when those systems are composed as typed
dora dataflows?** The robot tasks (a pharmacy desk, a retail store, a powder
bench) are instruments, not the object of study; the object is the full
engineering loop and whether an improvement can be attributed to the agent
rather than to an easier seed, a changed scorer, environment drift, or extra
compute. The contribution is the substrate and the evidence discipline: a
typed topic contract, a capability registry with a static validator, a frozen
verifier and reset, a clamp-only motion guard, and a harness that records
task result, cost, safety events, code identity, environment, seeds, and an
admissibility audit together.

The brain is a coding agent (Claude Code or Codex) working in the **outer
loop**, between episodes: it edits graph YAML, node code, and skills, and
reads typed traces. Nothing in the inner control loop calls a language model
unless a learned policy is deliberately swapped in as a typed node.

## 2. What has been measured

Read these as the project reads them: each carries its own sample size and
attestation label, and none is a confirmatory treatment effect.

| Result | Number | Qualifier |
|---|---|---|
| M0 expert pharmacy pick | 0.98 pass@1 over 50 seeds | signed off, replicate re-satisfied the gate |
| H1 zero-shot composition | 40/40 schema-valid; 15% (Claude) / 65% (Codex) launch | target not met; dominant failure is uninstalled hub packages |
| H2 iteration to a working system | Claude 1.0 held-out; Codex 0.875 at n=8 | met on one arm |
| H3 skill accumulation | speedup ratio ~1.03 on T4; T2-only follow-up 35% cheaper at equal quality | undecided; the tier ladder had no tier that could show an effect |
| H4 hot-swap vs relaunch | 32.4 s vs 41.8 s median iteration latency, n=6 | unattested; pre-lockstep graphs only |
| H5 wrong-object under free iteration | 0 in the retained denominator, incl. 224/224 episodes | weakened observational claim, not prevention evidence |
| H6 agent operates a running system | 3/3 fault classes detected, localized, restored to 1.0 | bounded feasibility, n=1 per class |
| A3 params-only vs params+code | 200k vs 396k tokens at equal quality | n=1 per arm, easiest tier |
| A4 Claude vs Codex | both 1.0 held-out; 186k vs 364k tokens | n=1 per arm |
| A5 fleet scaling | 1.6 → 4.1 → 4.3 successes/hour at 1/4/8 agents | saturates near four lanes on one host |
| A6 teleport vs behavioral reset | 1.00 vs 0.80 pass@1, +19 s per episode | the reset is itself a manipulation task |
| T2 perception wall | expert 0.08 → agent-authored stack 0.5 on n=8 | 0 wrong-object across every T2 episode |
| Realistic verifier fidelity | agreement 0.45, false success 0.00, false fail 0.68 | conservative, not interchangeable with the oracle |
| VLA policy value (SmolVLA) | 0/8 live, 0/8 under lockstep eval | competence, not latency, is the wall at the current training dose |
| VLM judge | five configurations refused | zero false promotions by the fidelity gate |
| M3 environment ladder, v2 | Spearman 0.746 over 16 graphs × 8 seeds | unattested, self-authored population |

Three observations a lab will care about. Retained development summaries
report zero wrong-medicine deliveries, but no independently audited table spans
the project-wide session denominator, and the repository refuses to call the
observation a prevention claim. The verifier that would port to hardware is
measured to be conservative rather than accurate, and the project publishes
that number instead of tuning it away. And the project's own review of
agent-authored robot code found three defects in the harness and none in the
agents' work.

## 3. What has not been measured, and why

This is the part to hear before the demo.

- **The headline claim is unrun.** Whether a typed dataflow substrate beats
  script-level iteration (SPEC 500, issue #347) has been pre-registered twenty-two
  times and executed zero times. Every one of the fifteen 4xx/5xx specs exists
  to make that result defensible, and all fifteen are still PROPOSED.
- **The first pilot could not collect a session.** On 2026-09-12 the pilot
  registration found that the matched executor admitted only the oracle T1
  mode, that both paired T1 surfaces leaked oracle state to policy code, and
  that the task-band calibration named no perception-eligible task. Those
  three facts are the most useful measurement the project made in a month:
  the study as registered had no instrument. A merged stack bound a T1-L2
  realistic pair; pilot v8 is registered with collection pending, and its task
  still carries a failed eligibility audit as a named limitation.
- **Rigor was being applied in the wrong order.** ADR-66 (accepted 2026-09-11)
  records the correction: run the pilot, then H6, before any new spec,
  platform, or hypothesis; nine specs are parked; one measured result every
  two weeks or infrastructure work stops. The invariants that make results
  attributable are unchanged. Only the sequencing changed.
- **Hardware is prepared and closed.** An SO-101 driver exists and is
  loopback-tested; the entry gates are GPU-gated on a learned policy that
  currently has no task competence and on a portable judge whose fidelity is
  below the detector verifier's.

## 4. How the project is run, and why it matters for reproducibility

- Specs carry numbered MUSTs; every implemented MUST needs a test that cites
  its ID. Unimplemented requirements in PROPOSED specs may carry an explicit,
  reviewable waiver; CI fails on any MUST with neither coverage nor a waiver
  (`tools/trace_check.py`).
- The scene, verifier, reset, and expert graphs are a hash-frozen set; a
  rollout refuses to start on drift; agents can read the judge and never edit it.
- Every run is identified by `(git_sha, env_hash, env_fingerprint, platform,
  seed)`; an unattested environment says so in the record.
- Claims live in `docs/claim-evidence.yaml` with a status (`supported`,
  `weakened`, `unrun`, `hardware_pending`) and a checker that fails when a
  public claim marker has no registered evidence.
- Campaigns are pre-registered in a content-addressed freeze registry; a
  changed artifact is a new version that names the old one, never an edit.
- Decisions are ADRs; the interpretation an agent chose under ambiguity is
  written down and dated (CON-15).

## 5. Positioning against the two closest 2026 systems

PhyAgentOS (arXiv 2607.16636) shares AISLE's founding observation and runs on
the same dora runtime, but hides the dataflow behind an HTTP tool API and lets
a composable verifier overturn benchmark verdicts. That is a shipped instance
of AISLE's control arm, which makes #347 a comparison against a real design
choice rather than a strawman. TypeGo (arXiv 2607.05482) puts the LLM in the
inner loop at several cadences and pays in tokens and unverified authority.
AISLE keeps the LLM in the outer loop and freezes the judge. Its risk is the
mirror image of theirs: not producing untrustworthy results, but producing
too few results. The paper's §7 carries the full comparison.

## 6. Where a graduate student can contribute this semester

Ranked by how much each unblocks, and with the moratorium in mind: nothing
below adds a spec or a platform.

1. **A task-band candidate round (SPEC 490, #346).** The pilot's remaining
   blocker is that no task stratum passes perception eligibility on the frozen
   envelope. Proposing and auditing candidates is calibration work with a
   defined protocol and a real payoff: it is what lets the headline study run.
2. **The independent statistical review (STA-12, #483).** A statistics
   student can do what no in-house work can: review the pre-registered
   estimand, power analysis, and stopping rules before any confirmatory session.
3. **Independent reproduction on a second machine (RPR-10/11, #485).**
   Clone, attest, re-run the primary cells, and report the deltas. This is a
   gate the project cannot pass by itself.
4. **T2 and T3 at session budgets.** The standing manipulation challenge. No
   arm has solved either tier within budget. Progress here is a paper on its own.
5. **Refuted-idea recurrence for the next H3 rerun (#566).** Register the
   metric, then decide whether a retrieval surface is warranted. Parked until
   an H3 rerun exists, so this is design work now and measurement later.
6. **The hackathon tracks** in the technical report's Appendix C, from
   ninety-minute warm-ups to genuinely open weekend problems, with Appendix D
   as the register of known gaps.

What not to do this semester: write a new spec, add an embodiment declaration,
or tune a verifier threshold to make a task eligible. Each of those is either
parked under ADR-66 or forbidden by the evidence rules that make the results
worth having.

## 7. Reading path

1. [`getting-started.md`](getting-started.md) and the quickstart in the README.
2. [`glossary.md`](glossary.md), especially the requirement-ID prefixes and
   the pre-registration vocabulary.
3. The technical report's §0 reading paths, then §2 (why "the demo worked" is
   not evidence) and §9 (what has been measured).
4. The [README status table](../README.md#status), including its
   "Since 2026-08-28" block.
5. [`decisions/ADR-66.md`](decisions/ADR-66.md) for the current execution
   order, and [`next-phases.md`](next-phases.md) for the ledger.
