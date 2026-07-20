# ADR-9: T07 budget-guard interpretations (SPEC 080)

Interpretations chosen (CON-15): (1) The EE workspace check (BG-2) uses
pure modified-DH forward kinematics for the Panda (official parameters,
verified against the textbook zero-pose flange position (0.088, 0,
0.926)); kinematics constants live in code, LIMITS live in
env/limits.toml. so101 has no limits section until its asset and
kinematics land (same blocker as TC-5/SCN-4): the guard refuses an
embodiment without limits loudly at startup rather than guessing.
(2) "Clamp to the nearest legal value" for a workspace violation is an
inverse problem; the guard clamps along the segment from the last safe
command (whose FK is inside by invariant) via 20-step bisection — the
furthest commanded progress that stays legal. Per BG-2's letter ("FK on
the commanded pose") the workspace VIOLATION is reported against the
commanded pose even when the velocity clamp already contained the step;
output containment is verified unconditionally on the final pose. (3) The episode wall
timeout (BG-2) is tracked per env from the first command; the guard sees
no goals or resets (BG-1 fixes its inputs), so a command gap of at least
idle_reset_s (a reset/idle boundary — resets silence commands for
longer) restarts the timer. On breach the guard holds the robot at the
last safe command (clamp, never drop, BG-3). (4) Malformed input (wrong
dof count, NaN/inf) is a violation class "malformed": bad joints are
held at their last safe value, a wrong-shape vector is replaced by the
last safe command entirely — the guard MUST NOT crash (BG-3), and NaN
must never reach the robot. (5) Velocity (BG-2 "last command + dt") is
judged against the last SAFE command and the contract dt of the
joint_cmd topic (TC-4: 100 Hz -> 0.01 s), preventing ratcheting past
limits via rejected commands. (6) guard_stats (BG-5) is emitted on the
command cadence whenever >=5 s elapsed since the last emission — with no
traffic there is nothing to guard and no stats. (7) Violation seq (BG-3)
is the violation topic's TC-2 per-topic monotonic seq. (8) The corpus
case motion_ungated_guard_spoof now expects SCHEMA_MISMATCH +
INPUT_NO_PRODUCER instead of MANIFEST_MISSING: the registry legitimately
carries the budget-guard manifest as of T07, so a spoofed guard emitting
raw joint_cmd is caught as a port mismatch (still rejected, VAL-7 exact
set updated).

## Amendments after PR #8 review round 1

(9) The idle-gap episode heuristic was gameable — pausing commands for
idle_reset_s every <60 s stretched the wall budget forever. The ONLY
episode boundary is now reset_done: the guard subscribes to it (manifest
input added), the wall timer restarts only there, and a timed-out
episode stays held until the next reset. idle_reset_s is removed from
env/limits.toml. reset_done also re-references the guard's velocity/hold
state to home — after a teleport reset the robot IS at home, and judging
post-reset velocity against the pre-reset pose would be wrong. (10) The
gripper is under the SAME regime as joints: wall timeout holds it, NaN
holds the last safe value, and a rate clamp (gripper_rate_max x the 30 Hz
contract dt; from the Panda's 0.05 m/s finger speed over its 0.08 m span)
bounds each step — it previously bypassed timeout and velocity
enforcement entirely. (11) env_hash covers the SPEC 080 frozen safety
artifacts (env/ and src/aisle/nodes/budget_guard.py) in addition to
CON-7's literal list — BG-2 declares limits.toml frozen, and a run's
env_hash must change if the guard or its limits do. This TIGHTENS the
frozen set; CON-7's enumeration is treated as a minimum, not a ceiling.
(12) The manifest advertises franka only: env/limits.toml has no so101
section and load_limits refuses it, so claiming so101 support was a lie;
restore when the so101 limits and kinematics land.
