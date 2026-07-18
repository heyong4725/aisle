# SPEC 030 — dora-genesis bridge node

Status: DRAFT until M0. Module: `src/aisle/nodes/dora_genesis.py`. Implements SPEC 010 against SPEC 020.

- BRG-1: Exactly one bridge node owns the Genesis scene per dataflow. It is driven by `dora/timer/millis/10` ticks; each tick advances sim by cfg.dt and services pending inputs in arrival order.
- BRG-2: Rendering is rate-limited independently of physics (TC table rates); a tick MUST NOT render all cameras every step. Target: ≥5x realtime physics with rendering on, single env, M3.
- BRG-3: `joint_cmd` applies PD position control; commands arriving faster than 100 Hz are coalesced to latest (document in metadata `dropped:int`).
- BRG-4: Reset service per TC-6. Behavioral mode (mode=1) delegates to SPEC 040 reset routine; teleport mode re-invokes `build_scene` state injection without process restart.
- BRG-5: `n_envs>1`: every output message carries `env_id` (TC-2); `joint_cmd` metadata `env_id` routes commands; missing env_id in multi-env mode is an ERROR event, not a default.
- BRG-6: The node MUST publish a `bridge_info` message once at startup: JSON `{contract:"v0", embodiment, n_dof, n_envs, genesis_version, platform, env_hash}` — rollout runner (SPEC 070) refuses on hash mismatch (CON-7).
- BRG-7: Failure honesty: sim exceptions crash the node loudly (dora ERROR event); the bridge MUST NOT catch-and-continue physics errors.

Acceptance: TC-A1..A3 exercise this node; plus `tests/graph/test_bridge_minimal.py::test_headless_60s_no_drift` (BRG-1,2 — sim_time vs wall time ratio logged), `::test_multi_env_routing` (BRG-5), `tests/unit/test_cmd_coalescing.py` (BRG-3, sim mocked).
