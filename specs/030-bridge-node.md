# SPEC 030 — dora-genesis bridge node

Status: DRAFT until M0. Module: `src/aisle/nodes/dora_genesis.py`. Implements SPEC 010 against SPEC 020.

- BRG-1: Exactly one bridge node owns the Genesis scene per dataflow. An
  attested, acceptance, or campaign simulation MUST use run-to-quiescence
  lockstep (ADR-30): the bridge publishes the observations due for simulated
  state `S(k)` and then `sim_turn` (`sim_turn_u64`, UInt64[1]); its monotonic
  process-lifetime value and every same-turn message's `turn_id` metadata
  identify exactly one open control turn. Every watermark carries parallel
  `closed_outputs: list[str]` and `emitted_counts: list[int]` metadata in
  lexical port order. A participant MUST receive the declared count for every
  causal input before it closes the turn; transport arrival order MUST NOT be
  treated as closure. The bridge MUST NOT apply
  commands or advance physics until the graph closes every causal branch and
  emits exactly one `turn_commit` (`sim_turn_u64`) for that turn. On a motion
  commit the bridge applies inputs in canonical order (`joint_cmd`,
  `gripper_cmd`, `base_cmd`), advances sim by `cfg.dt` exactly once, and opens
  the next turn. On a reset commit, reset takes priority, discards same-turn
  motion, injects the new state, advances no physics, and opens the next
  globally numbered turn at episode-relative step zero; the initial reset
  similarly opens turn zero before any physics step. Missing, duplicate, stale,
  future, or unstamped commits/commands MUST fail loudly; arrival order and
  WALL latency MUST NOT select a simulated step. A WALL watchdog MAY abort
  a hung turn but MUST NOT advance or manufacture a commit. Free-running
  `dora/timer/millis/10` stepping is permitted only for reset-less bring-up or
  interactive visualization and MUST be recorded non-attesting; it is not
  eligible for acceptance or campaign metrics.
- BRG-2: Rendering is rate-limited independently of physics (TC table rates); a tick MUST NOT render all cameras every step. Target: ≥5x realtime physics with rendering on, single env, M3.
- BRG-3: `joint_cmd` applies PD position control. In lockstep, multiple
  same-turn commands are coalesced by the producer's monotonic `seq`, never by
  arrival order (document in metadata `dropped:int`); free-run commands
  arriving faster than 100 Hz are coalesced to latest.
- BRG-4: Reset service per TC-6. Behavioral mode (mode=1) delegates to SPEC 040 reset routine; teleport mode re-invokes `build_scene` state injection without process restart.
- BRG-5: `n_envs>1`: every output message carries `env_id` (TC-2); `joint_cmd` metadata `env_id` routes commands; missing env_id in multi-env mode is an ERROR event, not a default.
- BRG-6: The node MUST publish a `bridge_info` message once at startup: JSON `{contract:"v0", embodiment, n_dof, n_envs, genesis_version, platform, env_hash}` — rollout runner (SPEC 070) refuses on hash mismatch (CON-7).
- BRG-8: (realistic-verifier increment one; declarative pre-implementation like VER-8..14 — the implementing bridge PR upgrades it to RFC-2119 normative wording together with the updated BRG-6 exact-shape test, avoiding a spec/test conflict on main) the `bridge_info` payload additionally carries `calibration` — the VER-8 v1 calibration block (SPEC 040) as a nested JSON object, populated from the BUILT scene's realized camera state converted to the v1 conventions per VER-8 (post-DR-jitter poses, the same values the render path uses; on hardware, from the measured-calibration artifact VER-8 names). The block is static per graph run — camera jitter realizes once at build — so consumers correlate it by run; time-varying wrist geometry correlates via `joint_state` sim stamps per VER-8. The existing BRG-6 fields, cadence, and the runner's hash-refusal behavior are unchanged.
- BRG-7: Failure honesty: sim exceptions crash the node loudly (dora ERROR event); the bridge MUST NOT catch-and-continue physics errors.

Acceptance: TC-A1..A3 exercise this node; plus
`tests/graph/test_bridge_lockstep.py` runs the same stamped control sequence
under two deliberately different node-delay schedules and asserts identical
command-to-turn assignment, reset/turn stamps, and physics state through
CON-5's complete 1.0 sim-s comparison window (BRG-1); it also proves a hung,
duplicate, stale, future, or unstamped turn fails without advancing physics.
`tests/graph/test_bridge_minimal.py::test_headless_60s_no_drift` exercises the
explicit non-attesting bring-up mode (BRG-1,2 — sim_time vs wall time ratio
logged), `::test_multi_env_routing` covers BRG-5, and
`tests/unit/test_cmd_coalescing.py` covers BRG-3 with sim mocked. Realistic
increment one (env-change PR): a bridge acceptance test asserts the published
`calibration` block field-for-field against the built scene's realized camera
state (BRG-8 + VER-8), including CONCRETE numerics after the
Genesis-to-v1 conversion (overhead 640x480 at vertical FOV 55°: fx = fy =
240/tan(27.5°) ≈ 461.04, cx = 319.5, cy = 239.5) and the converted
OpenCV-frame quaternion for the jitter-consistent overhead pose; the BRG-6
exact-shape assertion is updated in the SAME change.
