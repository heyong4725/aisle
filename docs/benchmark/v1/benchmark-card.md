# AISLE benchmark v1 card (BMK-20 draft)

- Purpose: measure whether a typed-dataflow engineering surface changes
  what a fresh coding-agent session can build for a simulated pharmacy
  pick-and-deliver robot, and whether typed runtime evidence changes fault
  localization and repair, under a frozen safety boundary.
- Unit: the coding-agent session. Seeds, episodes, and events are nested.
- Tasks: `task-distributions.json` (draft; final ids await #346).
- Treatments: typed (available), monolithic (dependency pending, #344).
- Baselines: `baselines.json` (adapters for two hosted agents and a local
  model exist; zero of four baseline cells have run under the contract).
- Safety boundary: validator topology (VAL-5), kinematic guard (SPEC 080),
  verifier semantic detection (SPEC 040), exposure ledger (SPEC 470),
  semantic authorization (SPEC 480, synthetic only). No physical claim.
- Access and compute: macOS arm64 or Linux x86-64 with Python 3.11+, uv,
  Rust toolchain for the pinned dora CLI, roughly 6 GB for the sim extra;
  a T1 episode takes about 90 s wall on the campaign Mac; T2 perception
  is CPU-pinned OWLv2 at about 1.6 s per inference (#391).
- Licenses: not yet selected. The repository carries no LICENSE or
  CITATION file; this is an owner decision and blocks public release.
- Security and reporting: GitHub issues on heyong4725/aisle; leak response
  per `governance.md`.
- Archive linkage: #355 (no DOI exists).
- Known gaps: `release-audit.json` lists every BMK criterion with its
  status; nothing here is a public release.
