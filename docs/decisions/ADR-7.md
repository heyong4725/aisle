# ADR-7: T05 bridge interpretations and measured performance (SPEC 030)

Interpretations chosen (CON-15): (1) Bridge configuration (seed, embodiment,
n_envs, repo root) comes from node env vars (AISLE_*) in the dataflow YAML —
dora's native per-node configuration channel. (2) The post-reset
oracle_state is published immediately after reset_done, before any physics
step, so the first observation after reset is a pure function of the seed
(TC-A2/CON-5); velocities are zeroed at injection. TC-6's no-interleave rule
is satisfied structurally: the event loop is single-threaded and replies
before returning. (3) gripper_cmd 0..1 maps linearly onto the embodiment's
finger travel (gripper_open_m/gripper_close_m in the layout profile).
(4) Behavioral reset (mode=1) raises NotImplementedError until SPEC 040
lands (T06) — a loud failure, not a silent teleport fallback. (5) env_hash
for bridge_info comes from invoking the canonical tools/env_hash.py, not a
reimplementation.

MEASURED PERFORMANCE (M3, genesis 1.2.3, Metal, 640x480+320x240 cams):
scene.step() = 4.4 ms at substeps=1 (8.7 ms at substeps=2 — hence the
physics.toml change; rigid pick-place is stable at dt=0.01), overhead
rgb+depth render 5.6 ms, wrist 3.4 ms. At contract rates the tick loop
sustains sim/wall ~0.77. BRG-2's stated TARGET of ">=5x realtime physics
with rendering on" is NOT met on this stack and is likely unreachable with
per-tick stepping (the 10 ms timer alone caps the loop at 1x); the MUST in
BRG-2 (rate-limited rendering, never all cameras per tick) is met and
tested. The drift test pins sustained throughput at >=0.70 so regressions
surface. If 5x matters for rollout wall-time (HAR-1, 50-episode runs), the
path is batched offline stepping or a faster physics backend — flagged for
M0 review rather than silently dropped.

Amendments from the T05 review round: (6) gripper_cmd drives ONLY the
finger dofs (dofs_idx_local) — an all-dof write was cancelling in-flight
arm trajectories; (7) teleport reset drains coalesced commands and
re-latches the PD target to home, so no controller state leaks across
episodes (CON-5); (8) camera topics publish env 0 only in batched mode —
genesis batched scenes render a single view, and mislabeling it per-env
would be worse than the gap (per-env rendering deferred; fleet mode is
state-based); (9) BRG-3's dropped:int rides joint_state/gripper_state
metadata; reset_done carries the full TC-2 key set; (10) TC-4's wall-clock
rate band IS met and asserted end-to-end (acceptance A1): the earlier
0.77x measurement was an artifact of leaked test processes saturating the
CPU (since fixed by the harness orphan reaper) — on a healthy machine the
loop holds contract rates within the ±20% band; the drift floor stays at
0.70 as a loaded-machine tolerance; (11) the nightly CI job (CON-12) runs
sim/graph/accept on macos-14; dataflow test timeouts budget ~5 min of
bridge startup because genesis kernel compilation under CPU contention
(parallel suites/reviews) has been measured to exceed 3 min.

From the CON-16 cross-review (Codex): (12) commands flow through ONE
arrival-ordered queue across kinds — the last-arrived command owns
overlapping dofs (BRG-1); env_id is validated to [0, n_envs) at push;
gripper dof count/indices come from the embodiment profile (gripper_dofs:
franka 2, so101 1 — TC-5); joint_state names are per-DOF in payload order
with a startup length assert; malformed reset requests (wrong shape, mode
outside {0,1}, missing request_id) raise instead of receiving reset_done
(TC-6). (13) seq semantics: TC-2 says "per-topic monotonic"; in multi-env
mode this bridge keeps seq per (topic, env_id) so per-env consumers can
detect drops without false gaps — the single-env case (the profile TC-2
was written for) degenerates to exactly per-topic. Recorded as the
multi-env extension rather than silently deviating.
