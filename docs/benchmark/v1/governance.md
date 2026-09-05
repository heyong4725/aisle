# Version policy and governance (BMK-18)

- Maintainers: the repository owner holds decision rights over benchmark
  versions; an independent statistician (STA-12) and an external
  terminology reviewer (CLM-12) hold veto over freeze and wording.
- Compatibility: a benchmark version pins tasks, scorers, safety boundary,
  budgets, treatment surfaces, and analysis. Any change to those creates a
  new version id; results from prior versions stay immutable and are marked
  superseded.
- Support window: the two most recent versions accept submissions.
- Schema migration: submission and report schemas carry `schema_version`;
  a migration tool is released with each version.
- Security and leak response: a reported leak of private instances,
  faults, or seeds quarantines the affected set, rotates it under
  `contamination-rotation.md`, and records the event in the version
  manifest.
- Appeals, corrections, withdrawal, errata: recorded as immutable entries
  under `docs/benchmark/v1/errata/`; a withdrawn result stays visible with
  its reason.
- Adverse findings, null and negative results are published with the same
  prominence as favourable ones (CSE-3).
