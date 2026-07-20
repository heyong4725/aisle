# ADR-8: T06 verifier/reset interpretations (SPEC 040)

Interpretations chosen (CON-15): (1) VER-2's robot-home condition requires
joint_state, which VER-1's subscription list omits — the verifier manifest
gains a joint_state input, and the home-error snapshot rides into the
spec-fixed judge signature via cfg (judge stays pure). Until the first
joint_state arrives, success is BLOCKED (never failed) — an unreported
robot cannot be "home". (2) The tray volume is OPEN-TOPPED: footprint
containment plus bottom-above-tray-floor — medicine boxes (up to 0.11 m)
are taller than the tray walls (0.04 m), so a closed-top AABB test could
never pass; wrong_object uses center-entry (a box "enters" the moment its
center crosses), success uses full-AABB containment — entry and
containment are deliberately different predicates. (3) "collision" from
oracle poses alone is a displacement proxy: any non-target box displaced
beyond knock_epsilon from its episode-start pose. Contact-level detection
would need collision queries the oracle contract does not carry.
(4) never_grasped = timeout AND the target never left its start pose
(move_epsilon); plain timeout otherwise. (5) The reset node is a thin
mode dispatcher for M0: teleport passes through to the bridge (which owns
injection, BRG-4; RST-1's <2 s budget asserted on live t_reset_ms in
acceptance A2); behavioral mode raises NotImplementedError (RST-2 is
Phase 2 per TASKS.md) — refused loudly, never silently downgraded.
(6) One episode_result per goal (TC-7): the node clears its goal after
publishing; a new goal restarts initial-pose capture.

## Amendments after CON-16 cross-review (Codex + workflow, T06)

(7) The deadline gates success: judge checks timeout BEFORE the success
predicate, so a placement completed after timeout_s is a timeout, never a
late success (TC-8 metrics must not inflate). (8) Tray containment uses
the WORLD-frame AABB (|R| @ half-extents) — a yawed box's footprint grows
under rotation; the unrotated body-frame AABB under-covered. (9) The
wrong_object entry region is BOUNDED in z (tray floor up to
wrong_object_entry_height_m): with an unbounded ceiling, a shelf box
standing over the tray footprint — or one arcing above it — counted as
"entered". (10) dropped_z_m raised 0.015 -> 0.07: floor-resting centers
span 0.015–0.055 m while tray/shelf-resting centers start ~0.08 m; the old
value was unreachable by any physically attainable pose (the class could
never fire). (11) The home condition is disabled for embodiments whose
profile has no home_qpos (so101 until its asset lands): an unobservable
condition must not block success forever; re-enable when the profile
gains home_qpos. (12) The verifier refuses (stderr) a new goal while one
is active (TC-7 actions don't overlap) and a goal naming an unknown
target_med — refusal, not crash. A freshness barrier discards oracle
samples timestamped at-or-before goal receipt so in-flight pre-goal
snapshots never seed an episode's initial poses. episode_result metadata
carries env_id and seq (TC-2). (13) The reset service refuses a bad
request per-request — stderr plus a correlated reset_done reply with
payload [0] and an error field — and keeps serving (a raise in the event
loop would kill the service for all later teleports, violating TC-6).
Resets in acceptance A2 route THROUGH the dispatcher so RST-1's <2 s
budget is measured end-to-end across both hops.
