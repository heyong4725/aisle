# SPEC 090 — Milestone M0 (Phase 0 exit)

Status: the integration gate. All acceptance tests here carry marker `accept`.

- M0-1: `harness rollout --graph graphs/expert_t0.yaml --tier T0 --episodes 50 --seeds 0..49 --reset teleport` reports pass1 ≥ 0.95 on macOS-arm64. (Cites HAR-1, SCN-*, BRG-*, VER-*, TC-*.)
- M0-2: Re-running M0-1 with identical seeds reproduces identical per-episode status vector (CON-5).
- M0-3: `tools/env_hash.py --write` output committed; `--check` passes; a mutated byte in `verifier/thresholds.toml` makes rollout refuse (CON-7, HAR-2).
- M0-4: `tools/trace_check.py` passes: every MUST in specs 000–080 is cited by ≥1 test (HAR-9).
- M0-5: (ASSET-GATED — deferrable) The same T0 graph with `--embodiment so101` (scene+driver profile swap only, zero YAML edits beyond the profile key) reaches pass1 ≥ 0.80 (lower bar acknowledges hobby-arm reach). (TC-5, SCN-4.) M0-5 is BLOCKED by owner-side gates an agent cannot clear — the SO-101 asset (provenance/licensing, ADR-6) and so101 support in the motion nodes — and the HAR-2 gate refuses `--embodiment so101` up front until both land. Per this clause, M0 MAY be signed off with M0-5 **deferred** to a post-M0 follow-up (owner records the choice in ADR-M0, M0-6); M0-5 is then re-run and must pass ≥ 0.80 before the so101 profile is declared supported. Absent that recorded deferral, M0-5 remains mandatory.
- M0-6: Human sign-off recorded in `docs/decisions/ADR-M0.md`; frozen-set label applied (CON-7).

After M0: Phase 1 specs (100-composer-harness, 110-research-agent-contract) are authored — deliberately NOT written yet; they depend on M0 learnings (CON-15 discipline applies to their drafting).
