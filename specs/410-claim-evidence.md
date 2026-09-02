# SPEC 410 — Claim-to-evidence catalog and benchmark architecture

Status: PROPOSED, HUMAN-REVIEW GATED. Issue: #358. This specification governs
documentation integrity; it cannot promote an empirical claim or substitute
prose for missing evidence. Depends on CON-5, CON-8, CON-12, and the scoped
actuation threat model to be ratified under issue #350.

The canonical machine source is `docs/claim-evidence.yaml`; the readable matrix
is generated at `docs/generated/claim-evidence.md`. README remains the canonical
dated project-status page. The catalog is canonical for claim status, scope,
evidence links, limitations, and allowed public wording.

## Catalog schema and evidence gates

- CLM-1: Every catalog row MUST have a stable claim id, claim text, claim type,
  status, scope, experimental unit/sample description, uncertainty, attestation
  state, supporting protocols/raw records/analyzers/tests, counterevidence,
  limitations, and allowed wording by public surface. Empty values MUST be
  explicit (`not_applicable` plus rationale), never silently omitted.
- CLM-2: Claim type MUST be one of `structural`, `empirical`, `causal`,
  `reproducibility`, or `future`; status MUST distinguish at least `supported`,
  `weakened`, `rejected`, `unrun`, `undecidable`, `unattested`, and
  `hardware_pending`. Simulation evidence MUST NOT satisfy a hardware-scoped
  row, and structural tests MUST NOT satisfy an empirical or causal row.
- CLM-3: A row marked `supported` MUST name tracked evidence appropriate to its
  type. Empirical/causal rows require raw records and a reproducible analyzer;
  causal rows additionally require the registered control and session-level
  uncertainty. Reproducibility rows require an independent reproduction record.
  A missing requirement MUST fail catalog validation rather than downgrade or
  infer a status automatically.
- CLM-4: Every referenced repository path and test node id MUST exist, be
  tracked, and match its declared kind. Generated documentation MUST fail CI
  when a protocol, raw record, analyzer, test, or source row disappears.
- CLM-5: Safety claims MUST occupy distinct rows for declared graph topology,
  kinematic enforcement, semantic detection, identity-aware authorization, and
  observed outcomes. The catalog MUST reject wording that attributes semantic
  prevention to the verifier or kinematic guard without intervention evidence.
- CLM-6: Headline claims in README, the broad technical report, and either paper
  MUST carry a nearby stable claim marker registered by the catalog. CI MUST
  reject unknown/duplicate markers and any catalog-declared headline location
  whose marker is absent. Unrun, undecidable, unattested, future, weakened, and
  hardware-pending rows MUST remain visibly qualified at each public location.

## Generated outputs and canonical status

- CLM-7: `tools/claim_evidence.py --check` and `--write` MUST obey CON-8. Output
  MUST be deterministic from tracked source inputs; `--check` compares the
  committed generated matrix byte-for-byte and returns `ok: false` on schema,
  evidence, marker, qualification, or generated-output drift.
- CLM-8: The README status table MUST remain the sole canonical dated project
  status. Other overview documents MUST identify their snapshot date and link
  to the README status on conflict. They MUST NOT declare themselves a second
  current status source.
- CLM-9: The generated matrix MUST render scope, evidence, sample/unit,
  uncertainty, attestation, counterevidence, limitations, status, and allowed
  wording without hiding fields in tooltips or prose-only appendices.

## External architecture and purpose boundary

- CLM-10: The external benchmark architecture MUST separate and name four trust
  zones: mutable participant/agent surface, frozen evaluator, scoped trusted
  actuation boundary, and hidden evaluation controller. It MUST state the
  coding-agent session as the benchmark experimental unit, identify what is
  actually inaccessible versus merely forbidden, and defer process/socket/
  driver bypass claims to the ratified issue #350 threat model.
- CLM-11: The broad technical report and focused benchmark paper MUST state
  non-overlapping purposes: the report preserves the complete project record;
  the benchmark paper tests the typed-versus-monolithic causal question and the
  typed-evidence fault question. Headline evidence absent from the focused scope
  MUST remain supplemental or historical rather than being relabeled.
- CLM-12: Before public benchmark release, a reviewer outside the wording and
  catalog authorship chain MUST inspect promotional and causal terminology,
  disposition every finding, and sign a retained review record. Missing review
  blocks release and MUST NOT be represented as completed external validation.

## Required fixtures

Acceptance coverage includes a missing evidence file, wrong evidence kind,
unknown/duplicate/missing markers, a simulation row mislabeled as hardware,
structural evidence used for a causal claim, each non-supported status, all five
safety rows, generated-output drift, duplicate current-status declarations,
and the broad-report/focused-paper purpose boundary.
