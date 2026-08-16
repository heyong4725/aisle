# TASKS.md — implementation order and paste-ready prompts

> **HISTORICAL ROADMAP — not a to-do list.** This file records the
> dependency order the build followed and the prompt templates used to
> drive it. Most tasks below have landed: T01–T10 (M0) are signed off,
> and the retail suite T11–T15 is implemented. Do **not** read an entry
> here as outstanding work. **Design-doc Phase 2 (§8.3) and Phase 3 (§8.4)
> both closed on 2026-08-16** — record in
> [`analysis/reports/phase2_phase3_report.md`](analysis/reports/phase2_phase3_report.md).
>
> For what is actually true now, see **[the README status
> table](README.md#status)** (the single current status page, issue
> #142); for exact catalogs of graphs, manifests, CLI commands and
> ADRs, see the generated
> [`docs/generated/project-inventory.md`](docs/generated/project-inventory.md);
> for orientation, [`docs/contributor-wiki.md`](docs/contributor-wiki.md).
> The prompt templates below remain useful verbatim for new work.

Dependency order (each task = one PR; tests-first per CLAUDE.md loop):

T01 bootstrap        → repo skeleton per CON-6; pyproject (uv); ruff, pytest markers
                       (CON-12); CI script `tools/ci.sh`; tools/trace_check.py (HAR-9);
                       tools/env_hash.py (CON-7). No sim deps yet.
T02 registry schema  → SPEC 050: JSON schema, schemas.toml vocabulary, registry CLI
                       lint/search, 12 manifests. Pure unit territory.
T03 validator        → SPEC 060 against the manifests; golden corpus (≥20 bad, ≥3 good).
                       Still no genesis import.
T04 scene            → SPEC 020: build_scene + meds.toml/physics.toml + sim tests.
T05 bridge           → SPEC 030 + contract acceptance TC-A1..A3.
T06 verifier+reset   → SPEC 040 (oracle + teleport path; behavioral is Phase 2).
T07 budget guard     → SPEC 080 incl. adversarial graph test.
T08 expert graph     → graphs/expert_t0.yaml + topdown grasp + oracle-pose nodes
                       (CAP-5 manifests already exist); make it pass locally.
T09 rollout runner   → SPEC 070 HAR-1..6; wire env-hash + validate + idea gates.
T10 M0 gate          → SPEC 090 acceptance suite; fix until green; human sign-off.

Paste-ready kickoff prompt (T01):
  "Read CLAUDE.md and specs/000-constitution.md. Implement task T01 from TASKS.md:
   bootstrap the repository exactly per CON-6/CON-12, tests first
   (tests/unit/test_trace_check_selfhost.py, tests/unit/test_env_hash.py).
   Do not add genesis or dora dependencies yet. Finish with all gates green
   and a PR description listing requirement IDs."

Per-task prompt template:
  "Read CLAUDE.md, specs/<NNN>-*.md, and TASKS.md task T<nn>. List the MUST IDs
   you will satisfy. Write the acceptance/unit tests named in the spec first,
   docstrings citing IDs. Implement. Run all gates. Open PR citing IDs.
   Ambiguities: ADR per CON-15, do not stall."

Cross-review prompt (other agent):
  "Run a review of PR <n> against specs/<NNN>. Check: every MUST cited by a test;
   CON-8 CLI compliance; CON-5 determinism; no frozen-set edits; hints quality
   for validator errors. Comment findings; do not push."

Post-M0 tasks (retail suite; do not start before SPEC 090 sign-off):
T11 mobility contract → SPEC 210: base topics in bridge, nav action, guard mutex,
                        locations.toml; contract acceptance tests.
T12 store scene       → RS-1..3: planogram.toml, build_store, episode generators.
T13 retail verifier   → RS-4..9: judge_retail + placement.toml + failure classes.
T14 registry ext      → §11.4 capabilities: base-driver-sim, waypoint-nav,
                        patrol-planner, order-reader(oracle rung), stock-detector,
                        misplacement-detector, placement-controller, task-planner
                        manifests (implementations may be oracle-rung stubs first).
T15 S1 expert graph   → scripted S1 episode end-to-end (test_s1_expert gate).
Then: agent campaigns on S1→S2→S3 measure the H3 transfer curve (design doc §11.5).

Bench suite tasks (powder family; T20 may run now; T21+ gated by PW-0 ADR):
T20 powder spike     → tools/spikes/powder_spike.py per PW-0: solver benchmark
                       (MPM vs PBD/SPH), particle-count sweep, scripted-scoop
                       repeatability, qualitative report + plots. Class A
                       (tools/), no spec edits, results to
                       docs/decisions/ADR-powder-spike.md as DRAFT for human
                       go/no-go. Runs on Metal; note CUDA deltas if a box exists.
T21 bench contract   → SPEC 310: ft stream, impedance mode+reject, balances with
                       settling, tare + tool-change services, TOOL_MISMATCH check.
T22 bench scene      → PW-1..3: build_bench, materials.toml, tools.toml,
                       particle oracle.
T23 mass verifier    → PW-10,11: judge_mass + redo-cycle semantics + new
                       failure classes.
T24 registry ext     → PW-13 manifests incl. naive fixed-scoop baseline
                       (the deliberate strategy gap stays open).
T25 P0 expert graph  → scripted scoop transfer (test_p0_expert gate).
Then: agent campaigns P1→P4; dosing-efficiency curves; H3 via material-
conditioned skill accumulation.
