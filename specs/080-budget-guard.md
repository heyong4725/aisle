# SPEC 080 — Budget-guard node

Status: DRAFT until M0; frozen set after. Module: `src/aisle/nodes/budget_guard.py`.

- BG-1: Interposes on all motion command edges (validator enforces topology, VAL-5): inputs `joint_cmd`, `gripper_cmd`; outputs `joint_cmd_safe`, `gripper_cmd_safe`, `violation`.
- BG-2: Limits from `env/limits.toml` (frozen): per-joint position limits, per-joint max velocity (computed against last command + dt), EE workspace AABB (via forward kinematics on the commanded pose), episode wall timeout.
- BG-3: On violation: clamp (never drop) the command to the nearest legal value, publish `violation` JSON {reason, joint|axis, requested, clamped, seq}, continue. The guard MUST NOT crash the dataflow.
- BG-4: Guard adds <2 ms p99 latency per command on M3 (measured in test).
- BG-5: Metrics: cumulative violation counts exposed in a `guard_stats` message every 5 s; rollout manifest stores totals. Target across any accepted run: zero UNCLAMPED violations by construction (clamping is the mechanism; the count of clamps is data, not failure).

Acceptance: `tests/unit/test_guard_clamping.py` — table-driven joint/velocity/workspace cases (BG-1..3); `tests/unit/test_guard_latency.py` (BG-4, p99 over 10k msgs); `tests/graph/test_guard_in_graph.py::test_adversarial_commands` — scripted node emits out-of-range commands; sim robot never exceeds limits (BG-1..3,5).
