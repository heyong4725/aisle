# ADR-powder-spike — T20 solver spike for the powder family (SPEC 300 PW-0)

Status: **DRAFT** — numbers measured; the go/no-go and the TBD-SPIKE
thresholds are a HUMAN decision (PW-0).

DECISION: (human)

## Setup

`tools/spikes/powder_spike.py`, genesis 1.2.3, **Metal** backend,
macOS 26.5 arm64 (this machine). Powder = MPM sand (PBD particles and SPH
liquid benchmarked as the "as available" comparators; SPH is a liquid
model, not a powder candidate). Particle size 4 mm, bulk density
1500 kg/m³ (particle mass 0.096 g). All numbers from the STABLE
integration regime (finding 1). Reproduce with
`uv run python tools/spikes/powder_spike.py --backend metal`; raw JSON and
plots in `runs/spike-powder/`.

## (a) Solver throughput — steps/sec (dt 1 ms, substeps 2)

| particles | MPM sand | PBD particle | SPH liquid |
|---|---|---|---|
| 4,913 | **280** | 211 | 225 |
| 19,683 | **242** | 108 | 94 |
| 50,653 | **177** | 50 | 42 |

MPM scales far better (0.63x from 5k→50k vs ~0.24x for PBD/SPH). At 20k
particles MPM runs at rtf 0.24, at 50k rtf 0.18 — the same order as the
store sim the harness already budgets for (nightly-suite scale, ADR-18).
Genesis build time is ~2.6 s per scene — rebuild-per-episode is viable
for this family, unlike the store.

## (b) Scripted scoop repeatability — 20 seeded reps, 20/20 completed

Lipped spatula (blade 50×90 mm + back wall + side rails), kinematic
drive, ~5k particles, seed-jittered pile placement and dip depth (±1 mm).

- transferred mass: **mean 4.82 g, std 2.68 g — CV 55.6%** (range
  0.5–12.6 g; quantization 0.096 g/particle)
- spill outside both vessels: **mean 16.1 g** (median 15.6, max 36.3) —
  ~3x the transferred mass
- no explosions: max end-state particle speed 5.5 m/s across all reps

A 1 mm dip-depth change flips the primitive between small-but-clean
(13 particles, zero spill) and large-but-messy (308 transferred, 612
spilled) — dosing variance is dominated by tool pose at particle scale.

## (c) Pour / angle of repose — MARGINAL-FAIL

Low-drop column slump, 14.5k particles, fully settled (max end speed
0.003 m/s — no explosion):

| friction_angle | repose angle (flank fit) |
|---|---|
| default | 2.7° |
| 15 | 4.1° |
| 35 | 0.8° |
| 55 | 7.0° |

A physical powder piles at ~30–40°. MPM sand at this particle scale
spreads nearly flat, and the friction_angle knob is non-monotonic and
never exceeds 7.6°: **granular repose behavior does not emerge in this
regime**. (A 24 cm high-drop variant splattered to 0.5° regardless —
that part is experiment design, but the low-drop slump is a fair test.)

## Findings the tables don't show (all measured, not assumed)

1. **Integration regime is load-bearing.** dt 2 ms / substeps 1 EXPLODES
   a plain 1 cm settle (particles ejected >100 m/s) on BOTH Metal and
   CPU — not a Metal artifact. dt 1 ms / substeps 2 is calm (residual
   0.37 m/s).
2. **CPIC is required** against thin rigid boxes (vessel walls, blade):
   without it the settle ejects the pile. Separately, spawning particles
   within ~1 particle size of a rigid surface NaNs the solve — spawn
   clearance is a scene-construction rule for T22.
3. **Kinematic tools must carry true velocity.** A teleport-driven tool
   with zeroed velocities neither drags nor carries powder (swept blade
   came up EMPTY); `set_pos/set_quat` + `set_dofs_velocity` with the
   trajectory velocity makes the coupling physical. Directly constrains
   how the bench bridge (T22) must drive tools.
4. **A flat blade does not scoop** — payload requires a lip (back wall +
   rails).
5. MPS tensors from `get_particles_pos/vel` need a `.cpu()` hop.

## Recommendation to the human decider

- **Solver: MPM sand.** Only candidate with powder-like contact behavior
  and the only one whose scaling supports 20–50k particles (rtf
  0.18–0.24 — nightly-suite scale, precedented by the store).
- **Particle budget: ~20k** (242 steps/s) as the default; 50k affordable
  for held-out evaluation runs.
- **PW-5 spill threshold**: scripted-scoop spill median was 15.6 g
  (163 particles); a P0 sanity threshold near ~2x that (≈30 g at this
  scale) rejects only genuinely wild behavior.
- **PW-6 (open-loop ±10%) looks unachievable as specced**: CV 55.6% for
  a scripted scoop. Either the tier's tolerance loosens, or it is
  reframed as "best-effort open-loop baseline" whose expected failure
  motivates P2.
- **PW-7 (closed-loop ±1%) is exactly what the variance argues for** —
  multi-scoop with balance feedback; quantization (0.096 g/particle)
  bounds achievable tolerance: ±1% of targets under ~10 g is
  sub-particle and needs target masses ≥ ~50 g or finer particles (at
  ~8x the particle count per volume for 2 mm).
- **Do not build repose-dependent behavior into scenes or verifiers**
  (finding c): heaps will not hold. PW-2's material realism is limited
  to {density, contact friction w/ tools, cohesion-free flow}; if pile
  geometry ever matters, budget a dedicated calibration pass or a
  different solver.

DECISION: (human)
