# ADR-powder-spike — T20 solver spike for the powder family (SPEC 300 PW-0)

Status: **DRAFT** — numbers measured; the go/no-go and the TBD-SPIKE
thresholds are a HUMAN decision (PW-0).

DECISION: (human)

## Setup

`tools/spikes/powder_spike.py`, genesis 1.2.3, macOS arm64 (this
machine), Metal + CPU backends. Powder = MPM sand (PBD particles and SPH
liquid benchmarked as the "as available" comparators; SPH is a liquid
model, not a powder candidate). Particle size 4 mm, bulk density
1500 kg/m³ (particle mass 0.096 g). All dynamic cases at dt 1 ms /
substeps 4 — substep_dt 0.25 ms sits under genesis's suggested stability
bound (0.3125 ms); coarser regimes drew instability warnings or exploded
outright (finding 1). Reproduce:
`uv run python tools/spikes/powder_spike.py --backend metal` (~5 h);
raw JSON + plots in `runs/spike-powder/`. The sweep exits 0 IFF every
case succeeded; the recorded run is `ok: false` — see (d).

## (d) Determinism — the decisive result (CON-5)

Identical seed, run twice, fresh processes, particle-state digests at
0.1 mm resolution:

| backend | same-seed digests | same-seed buckets | sweep completion |
|---|---|---|---|
| **cpu** | **EQUAL (bit-exact)** | equal | 20/20 scoops |
| **metal** | DIFFERENT | 37 vs 29 transferred, 538 vs 568 spilled | 18/20 (2 stochastic crashes; the same seed passes on rerun) |

Metal MPM is nondeterministic (GPU atomics) AND stochastically unstable
(~10% of scoop cases crashed in the sweep; earlier reproduced class:
rigid-solver "invalid constraint forces causing nan"). CPU is bit-exact
across processes. **Consequence: any scored/aggregated statistic for this
family must run on a deterministic backend; Metal is an exploration
backend only.** This creates the family's central tension — see the
recommendation.

## (a) Solver throughput — steps/sec (stability-bounded regime, Metal)

| particles | MPM sand | PBD particle | SPH liquid |
|---|---|---|---|
| 4,913 | **154** | 113 | 148 |
| 19,683 | **130** | 56 | 111 |
| 50,653 | **94** | 26 | 74 |

MPM scales best. But the deterministic backend is what matters for
scoring, and CPU MPM runs the 5k scoop scene at ~7 steps/s (a 1.6 sim-s
scoop takes ~3.9 wall-minutes) — roughly 20x slower than Metal.

## (b) Scripted scoop repeatability — identity-tracked accounting

Every particle lands in exactly one bucket (source / receiver /
spilled_new / out_at_baseline / airborne_or_on_tool); the partition
provably sums to N. The settle splash is identity-excluded from spill
(mean 4.6 particles/run in this regime), and the airborne/tool bucket
was empty in every completed rep — nothing hidden.

CPU (the statistically sound series, 20/20): transferred **mean 6.76 g,
std 5.95 g — CV 87.9%** (range 0.0–21.7 g); spilled_new **mean 55.9 g,
median 56.0, max 81.5** — spill is ~8x the transferred mass. Metal
(noise-inclusive, 18/20): mean 7.01 g, CV 98.7%, spill mean 58.9 g.
No explosions in completed reps (max end speed 1.8 m/s = falling
stragglers).

The scripted primitive is essentially a coin flip per scoop at this
particle scale: open-loop dosing is not a viable strategy, and spill
dominates transfer.

## (c) Pile/pour sanity — pour works mechanically; repose does not emerge

TRUE pour (driven vessel tilting 130° at height): **96–99% of material
streams out** across friction_angle ∈ {default, 35, 55} — pour mechanics
and the rigid-vessel coupling work. But the resulting piles relax toward
flat: flank-fit angles 2.7–6.3° (slump: 0.7–6.0°), versus ~30–40° for
physical powder; a transient ~31° flank mid-settle relaxes away as the
pile keeps creeping. End speeds 1.0–1.9 m/s are straggler streams, not
explosions (no NaN, no ejection). **Granular repose does not hold in
this regime and the friction_angle knob does not fix it** — scenes and
verifiers must not depend on heap geometry.

## Engineering findings that bind T21/T22 (all measured)

1. Integration regime is load-bearing: dt 2 ms / substeps 1 explodes a
   plain 1 cm settle on BOTH Metal and CPU; the stability-bounded
   regime (substep_dt ≤ genesis's suggested bound) is calm.
2. CPIC is required against thin rigid boxes; spawning particles within
   ~1 particle size of a rigid surface NaNs the solve.
3. Kinematic tools must carry TRUE velocity (`set_dofs_velocity`) — a
   zero-velocity teleported tool neither drags nor carries powder.
4. A flat blade does not scoop; payload requires a lip (back wall +
   rails). A 1 mm dip-depth change flips payload by an order of
   magnitude — tool-pose sensitivity dominates dosing variance.
5. MPS tensors need a `.cpu()` hop before numpy.

## Recommendation to the human decider

The family faces a **determinism / throughput / fidelity trilemma**:

- Deterministic scoring (CON-5) requires CPU today → ~7 steps/s at 5k
  particles → a closed-loop P2 episode (~60 sim-s) costs ~2.4 wall-hours.
  Campaign-viable only for P0/P1-scale primitives or smaller scenes.
- Metal is 20x faster but nondeterministic and ~10%-crashy: usable for
  exploration, not for scored statistics.
- If the family proceeds past P1, a **CUDA determinism spike** (PW-0
  names CUDA as optional) should decide whether a deterministic-enough
  GPU path exists before committing to P2+.

Within that frame: solver = **MPM sand** (only powder-like candidate,
best scaling); particle budget ≤ ~5k for CPU-scored work, 20–50k
affordable for Metal exploration. PW-5 spill threshold: CPU spill median
was 56 g at this scale — a sanity threshold near ~2x (≈110 g) rejects
only wild behavior, and the tier should score spill primarily through
PW-11's continuous spill_mg rather than a hard limit. PW-6 (open-loop
±10%) is not achievable with a scripted primitive (CV ~88%): reframe as
a best-effort baseline or drop. PW-7 (±1%): particle quantization
(0.096 g) plus CV means targets must be ≥ ~50 g or particles finer (8x
count per volume at 2 mm — untested throughput). Do not build
repose-dependent behavior into scenes or verifiers.

DECISION: (human)
