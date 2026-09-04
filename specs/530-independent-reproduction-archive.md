# SPEC 530 — Independent reproduction and immutable evidence archive

Status: PROPOSED, HUMAN-REVIEW GATED. Issue: #355. This contract governs the
evidence archive and independent reproduction of the new primary causal and
fault-localization studies. It does not convert attestation, a clean-clone
self-test, CI, or a maintainer rerun into independent reproduction.

Packaging schemas and validators may be implemented before results exist. A
reproduction campaign depends on frozen and completed primary inputs from
issues #347 and #349, their measurement and integrity dependencies, and the
applicable protocol-freeze hashes. Benchmark participant packaging belongs to
#357; this specification owns the evidence bundle that regenerates reported
results and the independent attempt to reproduce them.

## Source evidence preservation

- RPR-1: Every confirmatory session MUST be copied to content-addressed durable
  storage before worktree or run-directory cleanup. The copy MUST retain raw
  transcripts, prompts/tool records permitted by provider policy, authored and
  executed artifacts, manifests, environment and treatment attestations,
  randomization and seeds, episode/event streams, stdout/stderr, resource and
  timing records, integrity checks, exclusions, deviations, failures, aborted
  sessions, analyzer inputs and outputs, and links to any separately stored
  large media. A successful summary without its raw denominator is invalid.
- RPR-2: A machine-readable source manifest MUST enumerate every file and
  external object by stable logical role, relative path or immutable object id,
  byte length, media type, SHA-256 digest, evidence kind, producing session,
  sensitivity/redaction class and license status. It MUST bind the code,
  protocol, task/fault bank, treatment artifacts, scorer, analyzer, lockfile,
  container where used, and every primary table/figure to immutable hashes.
- RPR-3: Archive construction MUST fail closed on a missing declared object,
  digest mismatch, unsafe path, duplicate logical id, unclassified sensitive
  object, unresolved license, unregistered exclusion/deviation, or primary
  result whose source closure is incomplete. Gitignored state MAY be an input
  to construction but MUST NOT be an undeclared dependency of the result.
- RPR-4: Public redaction MAY remove secrets, provider-prohibited content and
  personal data only through a frozen deterministic policy. The source
  manifest MUST retain private-original digests, public replacements, reasons
  and transformations. Redaction MUST NOT remove failures, exclusions,
  denominators, treatment identity, outcome evidence or adverse results; where
  those cannot lawfully be released, the affected claim is marked
  non-reproducible rather than silently summarized.

## Reproduction package and environment resolution

- RPR-5: The reproduction bundle MUST be constructible from a clean tagged
  source revision plus named immutable evidence objects. It MUST contain no
  symlink escape, absolute campaign-machine path, mutable branch reference or
  dependency on the original `runs/` tree. A validator MUST unpack it into a
  fresh directory, verify all digests and prove every requested analysis input
  resolves within the bundle or through a declared immutable object locator.
- RPR-6: A machine-readable environment manifest MUST pin supported OS,
  architecture, CPU/GPU/accelerator and memory class, storage, Python and uv,
  lockfile and selected extras/groups, dora binary/source revision, simulator
  backend, drivers, containers where used, model ids/revisions/checksums,
  provider/API requirements and locale/clock settings. Bootstrap MUST use
  exact documented commands and fail rather than silently substitute a model,
  backend, dependency source or execution mode.
- RPR-7: The package MUST state expected download size, installed size,
  per-stage wall time, compute/device needs, model/API access steps and expected
  monetary cost as measured ranges with station identities and dates. Optional
  accelerators or credentials MUST be separated from requirements for analysis-
  only regeneration and for primary-cell execution.
- RPR-8: One non-interactive CON-8 entry point MUST validate the bundle and
  regenerate every main paper table, data figure and claim-evidence status row
  from packaged inputs. It MUST emit one JSON result naming inputs, commands,
  tool versions, elapsed time, output digests and failures. It MUST work from a
  clean checkout without undeclared gitignored state or manual file movement;
  a partial or stale output is a refusal, not success.

## Independent operator protocol

- RPR-9: Before receiving the reproduction bundle, the reproduction protocol
  MUST freeze the selected primary cells, hypotheses/gates, source-release id,
  operator instructions, allowed contact, machine requirements, budgets,
  attempts, exclusions, stopping rules, expected comparisons and report
  template. Selection MUST include every condition needed to recompute each
  targeted primary contrast and at least one primary contrast from both #347
  and #349; it MUST NOT be changed after original outcomes are disclosed.
- RPR-10: An independent reproduction requires a person who did not conduct,
  curate, analyze or package the original confirmatory sessions and a machine
  that is not the original campaign machine. The operator MUST begin from the
  public or double-blind bundle, not a maintainer worktree, original `runs/`
  tree or private repair notes. A signed machine-readable declaration MUST name
  roles, prior access, conflicts, bundle id, host/environment id and all
  maintainer contacts; assistance outside the frozen instructions is retained
  as a deviation and does not become invisible setup knowledge.
- RPR-11: Environment and runtime identity MUST resolve from the bundle without
  repository-history archaeology. The independent record MUST retain bootstrap
  logs, inventory and attestation outputs, exact commands, downloads, overrides,
  retries, failures and final hashes. A maintainer may answer documented
  questions but MUST NOT edit the operator's environment or result in place.

## Layered reproduction and cost report

- RPR-12: The bit-exact layer MUST compare every artifact promised exact by
  CON-5 and the registered study protocols, including generated task/fault
  assignments, reset state, plans and treatment artifacts. Both digest sets and
  a structured per-object diff MUST be retained; a mismatch MUST NOT be hidden
  by a passing outcome gate.
- RPR-13: The exact-cadence layer MUST compare reset anchoring, sequence and
  command/receipt cardinality, scheduled topic stamps and registered timing
  invariants across the entire registered cadence window. Missing events,
  duplicate ids, shortened coverage or unresolved clock domains MUST fail this
  layer independently of physics and outcomes.
- RPR-14: The physics layer MUST cover the full registered tolerance window
  after every reproduced reset, including CON-5's first 1.0 simulated second
  unless a stricter accepted protocol governs. It MUST retain paired raw state,
  shared coverage, absolute/relative errors, maximum error and tolerance by
  field and seed. Partial coverage is inadmissible; chaotic full-episode state
  MUST NOT be represented as bit-exact.
- RPR-15: The statistical layer MUST independently apply each original outcome
  replicate gate to the reproduction's registered experimental units. It MUST
  retain both success counts, denominators, per-seed or per-instance flips,
  exclusions, failures, effect estimates and uncertainty from #345, plus the
  registered gate result. Non-rejection alone MUST NOT be called equivalence,
  and a failed or opposite-direction reproduction remains a released result.
- RPR-16: Lockstep wall-time cost MUST be measured on a frozen matched workload
  and pinned host using randomized repeated lockstep and registered comparison
  executions. The report MUST retain startup, wall and simulated durations,
  real-time factor, CPU/GPU utilization where measurable, peak memory,
  failures, trial counts, pairing and uncertainty. It MUST distinguish control-
  loop cost from model/API latency and MUST NOT generalize across unmeasured
  hardware.

## Immutable and double-blind release

- RPR-17: The release MUST include repository and third-party license notices,
  dataset/model/media license and access terms, citation metadata, authorship
  and funding metadata appropriate to the review stage, expected compute/time/
  cost, privacy/redaction notes, checksums, schema versions, protocol and
  deviation records, reproduction instructions, support boundary and a
  machine-readable manifest. An unresolved right to redistribute any required
  object blocks a claim that the public bundle is self-contained.
- RPR-18: A double-blind variant MUST be deterministically derived from the same
  source manifest. Its frozen policy MUST remove repository remotes/history,
  author and institution identity, usernames, absolute paths, account ids and
  other declared identity leaks while preserving scientific dates, hashes and
  evidence semantics where safe. An automated leak scan and manual review MUST
  produce a report; the private identity map is access-controlled and its hash
  is retained outside the review bundle.
- RPR-19: A DOI criterion passes only when an actual immutable deposit is
  finalized, the DOI resolves without privileged access to the matching public
  landing record, the deposited checksums match the validated bundle, version
  relationships are recorded and the declared files can be downloaded. A
  draft, reserved DOI, mutable latest-version URL, mock service or unit test
  MUST remain `archive_pending`.
- RPR-20: A release audit MUST download both the DOI deposit and double-blind
  artifact into fresh directories, verify their manifests, run the analysis
  entry point, compare output digests and publish a criterion-by-criterion
  report for RPR-1..RPR-19. Claim status MUST distinguish `passed`, `failed`,
  `not_reproduced`, `redacted_dependency` and `archive_pending`; CI, maintainer
  rehearsal or attestation alone MUST NOT set independent reproduction to
  `passed`.

## Limitations before confirmatory evidence and external participation

Tests may validate schemas, package closure, deterministic redaction, refusal
paths, one-command analysis fixtures and archive round trips using synthetic or
pilot records. They do not satisfy independent reproduction, outcome gates or
the DOI criterion. Until #347/#349 produce frozen confirmatory inputs, an
independent operator executes the protocol, and an authorized deposit is made,
those criteria remain explicitly pending.
