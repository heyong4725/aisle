# Ablation A1 — agent-composed vs expert graphs (design doc §6)

A1 asks whether *agent composition* carries a tax (or gain) versus the
hand-written expert. The matched expert fill-runs landed 2026-07-29/30
(held-out seeds 100..107, pin `87b1ff66`; evidence in `records/`).
**These fill-runs predate the ADR-24 attestation implementation and are
UNATTESTED** — their manifests carry `env_baseline: local` with no
`env_fingerprint`/`env_attested`, so per amended CON-5 they make no
reproducibility claim (the frozen-tree `env_hash` matches across rows,
but that does not identify the installed environment). Attested reruns
under the merged implementation are queued as an addendum. n=8 per cell
keeps every conclusion coarse.

## T1 (desk): the END-TO-END estimand shows a real composition tax

**Correction (PR #70 review): an earlier draft conditioned the agent
rows on the 16/40 graphs that launched — selecting away the dominant
composition-failure mode A1 exists to measure.** The estimand is
end-to-end: compose, launch, and pass, with a composition that never
launches scoring 0.

| System | T1 end-to-end pass@1 | Launch rate | pass@1 given launch |
|---|---|---|---|
| Expert `expert_t0` (fill-run, unattested) | **0.875** | 1/1 | 0.875 |
| Agent zero-shot pooled (H1, `abd2e9d3`) | **0.347** (13.875/40) | 16/40 | median 0.875 |
| — claude arm | 0.125 | 3/20 | mean 0.833 |
| — codex arm | 0.569 | 13/20 | mean 0.875 |
| Agent EN-loop claude (H2, `e8f163ab`) | 1.0 | — | — |
| Agent EN-loop codex (H2, clean) | 0.875 | — | — |

- **Zero-shot composition carries a LARGE tax at T1**: 0.347 pooled
  end-to-end vs the expert's 0.875 — driven by the launch gap
  (INSTALL_MISSING class, `analysis/h1/`), not execution quality.
- **Conditional on launching, execution matches the expert** (median
  0.875, same `dropped`-class failures) — composition, not control, is
  what fails.
- **The EN loop closes the tax**: one iteration budget later (H2), both
  arms sit at or above the expert (0.875–1.0). The substrate story is
  "compile-loop feedback repairs composition," not "composition is
  free."

Reference row (different tier, context only): expert_t0 @ T0 = 0.98
over 50 seeds (M0, `3644a501`, CON-5 determinism replicate).

## S1 (retail): an observed 3–4x point-estimate gap — NOT an established gain

**Correction (PR #70 review): cell sizes cannot establish a gain.**
Observed pass rates on held-out seeds 100..107, with Wilson 95%
intervals:

| System | pass@1 (observed) | Wilson 95% | Failures |
|---|---|---|---|
| Expert `expert_s1` (fill-run, unattested) | 0.125 (1/8) | 0.02–0.47 | 5 `timeout`, 2 `extra_item` |
| Agent W/S1 (H3, `03da7469`) | 0.375 (3/8) | 0.14–0.69 | 5 `timeout` |
| Agent L/S1 (H3, `03da7469`) | 0.500 (4/8) | 0.22–0.79 | 4 `timeout` |

The intervals overlap heavily: the 3–4x ratios are **point estimates
from single sessions**, and A1/S1 is **inconclusive pending repeated,
attested sessions**. What the records do support:

- The expert's held-out failure histogram (0.125; 5 timeout,
  2 extra_item) matches its dev-seed baseline run exactly (seeds 0..7,
  `analysis/h3/records/arm_W/S1/dev/`) — a consistent-with, n=8
  replication, not proof of the graph's level.
- The expert commits `extra_item` (the 10x delivery class, RS-7) on
  held-out seeds; the agent-built S1 systems, which explicitly designed
  that mode out, show 0 delivery-class failures held-out. Direction
  noted for the H5 writeup: "delivery precision held" is a property of
  the agent systems, not the suite, and H5's structural guarantee
  cannot cover `extra_item` (guard-ungateable; verifier-detected only).

## Caveats

- n=8 cells throughout; one episode swings a rate by 0.125.
- Expert cells at pin `87b1ff66`, agent rows at their campaign pins;
  frozen-tree `env_hash` identical (`025c7de2`) across rows, installed
  environments NOT identified (unattested runs).
- H3's S1 cells are single sessions (`analysis/h3/` variance caution);
  H1/H2 rows carry their findings' caveats.
- Fill-runs used the recorded human overrides (`--no-idea-gate`,
  `--env-baseline local`); manifests in `records/`.

IDs: design doc §6 ablation A1, RS-7 (delivery class), CON-5 as amended
by ADR-24 (attestation scope of reproducibility claims).
