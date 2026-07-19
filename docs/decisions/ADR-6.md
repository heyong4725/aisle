# ADR-6: T04 scene interpretations (SPEC 020)

Interpretations chosen (CON-15): (1) genesis-world and torch live in an
optional `sim` extra, not default dependencies — neither is CUDA-only
(macOS wheels are MPS/CPU, CON-1-clean), but they add gigabytes to the
default `uv sync` that unit CI and the validator never need; sim/graph CI
jobs run `uv sync --extra sim` (genesis-world 1.2.3 does not declare its
torch dependency, so we pin it explicitly). (2) The so101 URDF asset is NOT
vendored: acquisition needs owner sign-off on provenance/licensing, so
`assets/so101/` is empty, `build_scene(embodiment="so101")` raises a clear
FileNotFoundError, and the so101 sim test skips with that reason — SCN-4 is
fully verified for franka and code-complete-but-asset-blocked for so101
(M0-5 forces resolution by T10). (3) Backend selection: gs.metal on Darwin,
gs.cpu elsewhere (SCN-5/CON-1); the rasterizer renderer is set explicitly.
(4) The SCN-3 reachability assertion runs for n_envs == 1 (the M0
configuration); batched builds skip it since per-env IK across identical
placements is redundant. (5) Placement randomness is a pure
`random.Random(seed)` sampler, separate from each DR toggle's own seeded
RNG, so enabling a toggle never shifts placements (tested). (6) Camera
poses and all layout/physics constants live in physics.toml per SCN-2 —
including the wrist camera's EE-link offset and every DR distribution range.

Amendments from the T04 review round (workflow + Codex cross-review):
(7) Layouts are PER-EMBODIMENT (`[embodiment.franka]`, `[embodiment.so101]`)
— M0-5 defines an embodiment as a scene+driver profile swap, and a single
shared shelf/tray position provably cannot satisfy SCN-4 for both reach
envelopes; so101's scaled-down numbers are provisional until its asset
lands. (8) Reachability is deterministic by construction (CON-5): genesis
is initialized with a fixed seed, and IK runs seeded multi-start with
explicit init_qpos (the embodiment's home_qpos ready pose — the all-zeros
pose is singular and never converges) and max_samples=1 so genesis's global
RNG never influences outcomes; position AND rotation error are checked.
(9) The placement sampler pre-filters candidates to reach_m *
reach_margin_frac so seed sweeps cannot abort builds on corner placements;
separation is per-axis AABB clearance (the earlier L1 rule provably
admitted overlaps — regression-tested across 200 seeds). (10) numpy is a
default dependency (CON-1-clean); genesis/torch stay behind the sim extra.
