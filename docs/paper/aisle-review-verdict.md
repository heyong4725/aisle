# Internal conference-review verdict — AISLE paper v1.4

Date: 2026-08-31
Status: internal review record; not a formal venue review
Reviewed manuscript: [`aisle-paper.md`](aisle-paper.md)

## Verdict

**Do not submit the current manuscript unchanged to an RSS/CoRL/ICRA-class
venue.** The likely recommendation is **3/10: reject**, with high confidence.

The underlying project is unusually thoughtful, auditable, and potentially
important. The current paper, however, supports the descriptive claim that
AISLE is an impressive experimental infrastructure that produced many useful
findings. Its central causal claim is stronger: that typed dataflows make
agentic robot engineering faster, more auditable, and more reusable than
script-level iteration. The registered typed-dataflow versus monolithic-script
control is explicitly unrun, so the paper does not yet establish that claim.

The recommended publication path is a focused second paper:

> **AISLE: An Auditable Benchmark for Coding-Agent Robot-System Engineering
> with Typed Dataflows**

The broad current manuscript should remain the project-level technical record.
The second paper should test one primary causal question and use live fault
diagnosis as its secondary systems validation.

## Scorecard

| Dimension | Assessment |
|---|---:|
| Novelty and potential | 6/10 |
| Technical system quality | 7/10 |
| Experimental rigor | 3/10 |
| Support for the central causal claim | 2/10 |
| Physical-AI relevance | 3/10 |
| Reproducibility intent | 8/10 |
| Clarity and focus | 4/10 |

## Material strengths

- The question is timely and important: coding agents are becoming part of
  robotics engineering, but their engineering process is rarely measured.
- Evidence provenance, frozen evaluation, invalid-run retention, and
  analyzer-derived findings are substantially stronger than ordinary demo
  repositories.
- H1's 40 independent composition sessions provide a meaningful initial
  population.
- H6's evidence-first diagnosis transcripts are interesting and could become a
  strong benchmark with genuine blinding and replication.
- Negative, undecidable, and superseded results are reported rather than
  hidden.
- The open substrate could become a useful benchmark artifact independent of
  whether typed dataflows win the causal comparison.

## Reject-level concerns

### 1. The central causal control is missing

The introduction states that typed dataflows make agentic robotics faster,
more auditable, and more reusable than monolithic scripts, while the H4 section
acknowledges that the equal-budget monolithic control remains unrun. A3 compares
two action spaces within AISLE; it does not compare typed dataflow engineering
with monolithic engineering. H1's 40/40 schema validity also has no registry-free
or untyped comparator, so it cannot isolate the effect of typing.

**Required disposition:** run the registered control with repeated independent
sessions, or narrow the contribution to an auditable substrate without a
causal superiority claim.

### 2. Headline comparisons are underpowered

- H2 scores held-out performance on eight seeds. Even 8/8 successes have a
  one-sided exact 95% lower bound of only about 0.69, so they cannot establish a
  population success rate above 0.90.
- The registered 0.99 pass@8 target is not exercised by in-context retries and
  cannot be estimated from the present sample.
- H3's 35% token difference, A3, and A4 are n=1 session per arm. They are case
  studies, not stable treatment effects.
- A5 has one fleet realization per size. Thirteen lanes are not thirteen
  independent replications of the fleet-size treatment.
- H6 has one session per fault class and six post-repair episodes per cell. It
  demonstrates feasibility, not a repair probability or restoration to a 1.0
  population baseline.

**Required disposition:** define the session as the primary experimental unit,
perform a power analysis, repeat sessions in randomized blocks, use more
held-out seeds, and report effect sizes with confidence or equivalence
intervals.

### 3. Positive results are concentrated on an easy, privileged simulator tier

Most positive results are on T1, frequently with sanctioned oracle pose. T2 is
low-performing, T3 remains unsolved, the realistic verifier is not
interchangeable with the oracle, the learned policy is 0/8, and no physical
robot result is reported. This leaves the paper vulnerable to being read as
software-engineering research demonstrated through a toy simulator rather than
physical-AI research.

**Required disposition:** include a positive non-oracle result, a task in the
empirically reachable-but-nontrivial band, and physical validation for a
physical-AI main-track submission.

### 4. The safety argument conflates three properties

The budget guard enforces a kinematic envelope: joint, velocity, workspace, and
timeout limits. It does not know medicine identity. The verifier detects a
wrong-object event after the semantic violation occurs. Zero observed wrong
deliveries is therefore an empirical policy record, not an effect attributable
to the guard.

Likewise, VAL-5 proves that declared graph paths into bridge command ports pass
through the guard; it does not by itself rule out all code-level or process-level
side channels. The word `unbypassable` is broader than the demonstrated threat
model.

**Required disposition:** separate (a) graph-topology motion gating, (b)
kinematic-envelope enforcement, and (c) empirical semantic outcomes. State a
threat model, test bypass attempts and guard-on/off effects, report exact
exposure denominators and confidence bounds, and add an identity-aware runtime
authorization layer before claiming prevention of wrong-object delivery.

### 5. H6 is not yet a blinded fault-diagnosis benchmark

The current result covers three public fault classes, one session each. The
fault menu is present in the repository; source reads occurred in two cells;
reference repairs made restoration easy by construction; and five measured
amendments preceded the scored cells. This is a valuable existence result but
can be criticized as a calibrated demonstration.

**Required disposition:** create an out-of-worktree hidden fault bank with
randomized selection, severities, intermittent and coupled faults, no-fault
controls, repeated agent sessions, and at least one repair requiring novel
code. Compare typed evidence against a logs-only baseline.

### 6. Attestation is not itself a reproduction experiment

Hashes and provenance establish treatment identity. They do not demonstrate
that an independent machine can reproduce the result. Some raw records were
purged, several measurements are UNATTESTED, and the paper's byte-exact wording
needs reconciliation with the layered tolerance/statistical reproducibility
contract in CON-5.

**Required disposition:** reproduce the primary cells on a second machine,
report exact/tolerance/statistical layers separately, quantify lockstep cost,
and publish all raw records and analyzers in an immutable archive.

### 7. The manuscript is too broad for one conference paper

The manuscript is approximately 6,500 words with a 342-word abstract and 18
figures. VLM judging, VLA fine-tuning, granular physics, surrogate environments,
fleet scaling, reset behavior, composition, iteration, skill accumulation,
safety, operations, and reproducibility cannot all be central results in an
eight-page paper.

**Required disposition:** retain only the substrate, the causal
typed-versus-monolithic experiment, the blinded fault benchmark, the safety
boundary, and external reproduction. Move the remainder to the supplement or
the project-level technical report.

### 8. Related work does not yet establish the novelty boundary

The revision must directly compare AISLE with ENPIRE, ASPIRE, RHO, RigorBench,
and Physical Agentic AI, as well as typed robotics middleware and runtime
assurance. In particular, Physical Agentic AI overlaps with typed skill
interfaces and deterministic runtime enforcement and already reports a held-plan
ablation plus physical trials.

## Submission decision gate

The second paper should not be submitted as a main-track physical-AI paper until
all of the following are true:

1. The typed-versus-monolithic causal control is complete with independent
   session replication.
2. Statistical decision rules and analysis code were frozen before confirmatory
   data collection.
3. The fault benchmark is hidden, repeated, includes no-fault controls, and has
   a comparator that removes typed evidence.
4. Safety claims are split according to what the guard, verifier, and observed
   policy behavior actually establish.
5. At least one non-oracle positive result is present.
6. A physical validation cell is present, or the venue and claims are explicitly
   narrowed to a simulated systems benchmark.
7. The primary artifact has been reproduced outside the campaign machine and
   archived with raw records.

If these gates cannot be met, AISLE remains a strong candidate for a systems
demo, artifact, benchmark workshop, or technical-report release with more
modest claims.
