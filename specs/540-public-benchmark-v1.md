# SPEC 540 — Public benchmark v1 and blind evaluation boundary

Status: PROPOSED, HUMAN-REVIEW GATED. Issue: #357. This contract turns the
research machinery into a versioned participant-facing benchmark. It does not
claim that existing contributor documentation, expert graphs, research
campaign scripts or CI constitute a public benchmark release.

Implementation depends on accepted contracts for the monolithic control
(#344), statistics (#345), non-oracle task band (#346), sealed fault bank
(#348), actuation and safety exposure (#350/#351), semantic authorization
(#352), treatment integrity (#353), instrument audit (#354), and claim status
(#358). The confirmatory result may be null or negative without invalidating
the benchmark. #355 owns the independent evidence archive and DOI round trip;
this specification owns the participant package and evaluation service.

## Versioned benchmark definition

- BMK-1: Every release MUST contain a machine-readable semantic benchmark
  version manifest that binds immutable hashes for participant contract,
  typed and monolithic surfaces, task distributions, public and hidden bank
  commitments, scorer and evidence schemas, safety/authorization boundary,
  budgets, baseline adapters/configurations, submission schema, analyzer and
  governance policy. A result without one resolved version id is invalid.
- BMK-2: The participant contract MUST enumerate allowed observations,
  actions, tools, files, environment variables, network endpoints, package
  installation, persistence, inter-session memory, clocks, compute, tokens,
  wall time, rollout/reset attempts and human assistance. Everything not
  explicitly granted is forbidden, and refusal behavior MUST be documented.
- BMK-3: Versioned task distributions MUST distinguish public development,
  public qualification and private evaluation instances; pin families,
  non-oracle sensor rungs, objects, difficulty strata, sampling weights,
  reset/randomization generators and scorer-visible truth. Public instances
  MUST exercise the same schemas without revealing private assignments or
  scorer intermediates.
- BMK-4: The typed and monolithic treatments MUST ship as the two #344
  participant surfaces with one machine-readable parity declaration covering
  observations, actions, authority, prompts/task information, dependencies,
  budgets, safety boundary, instrumentation and scorer. Representation and
  treatment-specific tooling MAY differ only where declared by the accepted
  causal protocol; a parity failure invalidates comparison results.

## Baselines and clean-clone entry path

- BMK-5: Benchmark v1 MUST ship runnable adapters, frozen prompts/configs and
  access instructions for at least two independently supplied coding-agent
  systems. Each agent MUST run both typed and monolithic treatments, yielding
  at least four baseline cells under the same versioned contract. Hosted model
  identity MUST include provider, model id, requested parameters, client/CLI
  version, access date and any provider nondeterminism; an unavailable model is
  reported rather than silently replaced.
- BMK-6: Human-authored expert artifacts and a deterministic no-research-agent
  fixture MUST ship for every released task family they claim to cover. They
  MUST use the public participant interfaces and safety boundary; privileged
  oracle/scorer access is allowed only for an explicitly labeled ceiling that
  cannot be submitted as a participant baseline.
- BMK-7: One documented non-interactive clean-clone command MUST install the
  declared minimal environment, validate a small public graph, run at least one
  fixed public task instance, construct a submission bundle, validate it and
  regenerate its report. It MUST emit a CON-8 JSON record with stage outcomes,
  versions, hashes, resources, elapsed time and output paths. A skipped stage,
  local override, undeclared cache or pre-existing `runs/` input is a failure.
- BMK-8: The quickstart MUST be exercised from a fresh clone on every supported
  release platform and from the released archive, with measured download,
  install and execution time plus peak storage/memory. Unit tests or a
  maintainer's dirty checkout validate components but do not satisfy this gate.

## Local development and blind evaluation

- BMK-9: Local development mode MUST use a public task/fault subset and visible
  scorer intended for debugging. Its manifests, receipts and reports MUST carry
  `development_public`; results MUST NOT enter the blind leaderboard or be
  described as held out. The same submission validator and participant schemas
  MUST be used so promotion does not require a private format change.
- BMK-10: Blind evaluation MUST execute the participant in a sandbox that does
  not mount or expose private bank files, unrevealed instances, seeds, fault
  payloads, truth, scorer code or outputs, bank keys, future rotations or other
  submissions. An independent controller outside participant authority MUST
  select instances, reveal only the frozen participant-facing task instruction,
  inject #348 faults, run reset/scoring, retain raw evidence and return only the
  frozen feedback allowed by the version.
- BMK-11: An isolation audit MUST attempt access through participant tools,
  filesystem paths and traversal, environment/process inspection, package and
  container layers, logs/errors, network/service endpoints, timing, shared
  memory, crash artifacts and submission contents. Unique canaries in every
  private class MUST remain absent from participant-visible records. Any leak,
  unresolved channel or participant-readable scorer truth blocks blind release.
- BMK-12: Hidden tasks and faults MUST use #348 commitments, roles and reveal
  rules. The release MUST pin bank version and distribution without publishing
  payloads, define who can access keys/plaintext, record every access, separate
  qualification from scored banks, and prevent a participant or baseline
  maintainer from selecting, skipping, relabeling or replacing an instance.

## Submission and result contract

- BMK-13: A submission bundle MUST retain benchmark version; agent/provider/
  model/CLI identity; prompt and tool contract hashes; typed or monolithic
  treatment id; authored and executed artifact hashes; dependency/environment
  identity; participant-visible inputs; session/attempt ids; budget and resource
  records; command, receipt, intervention and outcome evidence; transcript or
  declared provider-limited substitute; and signed provenance, integrity,
  exclusion and deviation records. Private assignments MAY be joined only by
  the evaluator after participant execution.
- BMK-14: Submission validation MUST fail closed on missing/unknown fields,
  schema/version drift, digest mismatch, unapproved files or capabilities,
  treatment/parity mismatch, incomplete session denominators, budget overrun,
  leaked private markers, unattested execution, unregistered exclusions or a
  participant-supplied score. Validation MUST be deterministic and return all
  machine-readable reasons without repairing the bundle in place.
- BMK-15: The evaluator MUST bind an accepted submission to private instance,
  scorer and safety-boundary hashes and issue a signed result receipt. Scoring
  and analyzer execution MUST occur outside participant write authority and
  preserve enough raw evidence for the #354 audit and #355 archive. Re-scoring
  under changed artifacts creates a new result version, never an in-place edit.
- BMK-16: The leaderboard/report schema MUST report sessions/tasks as registered
  experimental units; sample sizes and denominators; success and failure
  classes; effect sizes and uncertainty from #345; exclusions/deviations;
  treatment-integrity and instrument status; safety exposures/interventions;
  latency, tokens, API cost, wall/CPU/GPU time, peak memory and storage; and
  evidence/claim status. It MUST NOT rank by a success scalar alone or pool
  incompatible benchmark versions.
- BMK-17: Resource reporting MUST define token accounting, cached-token handling,
  API pricing date/currency, retries, parallel agents, host/device utilization,
  model download and setup amortization. Missing or incomparable fields remain
  explicit. The public report MAY present Pareto views but MUST NOT silently
  collapse accuracy, safety and cost into an unregistered composite score.

## Governance, contamination, and release gates

- BMK-18: A version policy MUST define maintainers and decision rights,
  compatibility guarantees, deprecation/support window, schema migration,
  security/leak response, appeals/corrections, result withdrawal and errata.
  Changes to tasks, hidden distributions, scorer, safety boundary, budgets,
  treatment surface or analysis require a new benchmark version; prior results
  and adverse findings remain immutable and visibly superseded.
- BMK-19: A contamination and rotation policy MUST record public release dates,
  agent/model training or access cutoffs where knowable, participant disclosures,
  leaked/compromised instances, quarantine decisions, rotation cadence, bank
  commitments and cross-version comparability. Post-release exposure MUST NOT
  be called blind; replacement sets are frozen before use and old sets are
  revealed only under the registered schedule.
- BMK-20: Public release MUST include repository/asset/data/model licenses and
  notices, contribution and security/reporting paths, citation metadata,
  benchmark card, access and compute requirements, version manifest, schemas,
  baseline configs, quickstart, governance/rotation policy and #355 archive
  linkage. An automated audit MUST verify this closure and publication URLs;
  unresolved redistribution rights or mutable required objects block release.
- BMK-21: At least one external person or group that did not develop or package
  benchmark v1 MUST start from the released artifact, complete the clean-clone
  cell and a registered benchmark submission without synchronous maintainer
  intervention. Their machine-readable record MUST retain environment, commands,
  elapsed/resource data, failures, documentation gaps, support contacts,
  submission and evaluator receipt. Individualized guidance or repair needed to
  complete the run invalidates the attempt; the gap is fixed in a new release
  before a fresh external attempt.
- BMK-22: The release audit MUST publish a criterion-by-criterion report for
  BMK-1..BMK-21 with `passed`, `failed`, `external_pending`, `dependency_pending`
  or `not_applicable` plus evidence links. Synthetic fixtures, internal dry runs,
  CI and prose MUST NOT mark the external-user, blind-isolation or public-
  publication criteria passed. #357 remains open until every applicable
  criterion has reviewable evidence.

## Pre-release limitation

Before dependencies merge and external validation occurs, tests may exercise
schemas, treatment matrices, package closure, sandbox canaries, submission
refusals, reports, version migrations and clean-clone fixtures. Such records
MUST be labeled pre-release or synthetic. They establish preparedness, not a
released benchmark or independent user success.
