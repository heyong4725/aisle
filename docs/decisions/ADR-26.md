# ADR-26 — CON-5 layered reproducibility: outcomes are statistical (issue #71)

Status: RATIFIED (owner decision 2026-08-05, in-session; enacted by the
spec-change PR that carries this ADR). Trigger: issue #71's three-part
evidence chain. Relates to ADR-24 (attestation), ADR-25 (reset-anchored
startup), SPEC 090 M0-2, CON-14 (this is a spec change).

## Evidence (issue #71, 2026-08-02..03)

1. **Startup race (fixed):** the attested S1 pair diverged because the
   first physics step raced the first reset — ADR-25 pinned it.
2. **Wall coupling (open):** pipeline wall latency quantizes into sim
   ticks on every tier; machine load shifts outcomes (M0 pair: first
   joint_cmd at sim 0.61 s vs 0.79 s under load).
3. **Backend nondeterminism (irreducible on Metal):** identical cold
   runs occasionally differ by a single joint_state ULP at
   unpredictable steps — step 7 of episode 0 in one measured pair, step
   74 after the second reset in another, none for 4 s in a third. GPU
   parallel-reduction ordering; chaos amplifies a ULP into episode-level
   outcome flips over a 600-sim-s episode. No configuration of this
   stack yields bit-exact replicates on Metal — even single-episode-
   per-launch pairs are independent samples of a distribution.

## Decision

CON-5's "reproducible" is layered (amendment in the same PR):

- **(a) bit-exact:** seed-derived artifacts — goals, plans, injected
  reset states, first post-reset snapshots.
- **(b) exact timing:** first reset at sim step 0; reset-anchored
  publish cadence (ADR-25).
- **(c) tolerance:** physics state values within 1e-6 (unless a spec
  tightens it) over the NORMATIVE comparison window — the first 1.0 s
  of sim time after each reset, with captures under 0.1 sim-s of shared
  span inadmissible (rerun, not compared). Full-episode horizons are
  chaotic and belong to layer (d).
- **(d) statistical outcomes:** an identical CON-5 tuple guarantees the
  same outcome DISTRIBUTION; per-seed status equality is never the
  claim. REVISED per the PR #88 review: non-rejection by a significance
  test is NOT equivalence — at n=50, 48/50 vs 40/50 gives Fisher
  p = 0.028, so a p >= 0.01 gate would wave through a 16-point
  regression. The replicate GATE is the original acceptance threshold
  independently re-satisfied by the rerun (M0-2: pass1 >= 0.95, which
  bounds the rate difference at <= 0.04 by construction), with the
  per-seed flip set, both success counts, and a two-sided Fisher exact
  p-value (context only) PERSISTED to
  `runs/<rerun-id>/m0_2_replicate.json` — pytest capture hides passing
  stdout, so logging means a durable artifact.
  `aisle.harness.stats.fisher_exact_two_sided` is dependency-free and
  pinned against scipy reference values in `tests/unit/test_stats.py`;
  it reports, it does not gate.

Options considered and not taken (may be revisited):
- **CPU backend for attested runs** — the powder spike showed CPU
  bit-exact; but store-sim is already rtf ~0.1 on GPU and a further
  CPU slowdown prices out campaign-scale evidence. The CPU probe
  remains available if a future question needs bit-exact replay.
- **Tolerance-only CON-5** — tolerance bands on chaotic 600-sim-s
  episodes still cannot make per-seed outcomes agree; outcome checks
  end up statistical anyway, so the layering says so explicitly.

## Consequences

- SPEC 090 M0-2 rewritten (this PR); the old per-episode-vector
  equality check would flake forever on Metal.
- Attested pairs and campaign comparisons are interpreted
  distributionally; single-seed status flips near decision boundaries
  are recorded noise, not contradictions.
- Ops discipline stands: acceptance/attested runs still want an idle
  machine — load shifts the OUTCOME DISTRIBUTION through the wall-
  coupling channel (evidence 2), which statistics do not excuse.
- The verifier fidelity work (ADR-realistic-verifier D2) independently
  chose CPU inference for verdict models: judgments must not flicker
  even though episode outcomes are statistical.

IDs: CON-5 (amended), M0-2 (rewritten), CON-14 (spec-change process),
CON-15 (this record).
