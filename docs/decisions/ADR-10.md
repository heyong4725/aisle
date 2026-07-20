# ADR-10: T08 expert graph interpretations and findings

Interpretations chosen (CON-15) while building graphs/expert_t0.yaml and
the CAP-5 pipeline nodes:

(1) Executable identity: runnable graphs wire node id `dora-genesis` (a
new combined manifest declaring the bridge's full port surface); the
camera-source / arm-driver-sim / gripper-driver-sim manifests remain the
abstract capability exemplars the corpus uses. dora-genesis joins
PENDING_M0_EVALCARDS (ADR-3) and retires with the others at T10.
(2) The rollout-client node (new manifest) drives episodes from env
config (seeds, targets, timeout, results path); the T09 rollout runner
will configure it. It sends a cleanup reset after the final episode so
the idle graph stops moving.
(3) Grasp strategy is dictated by shelf geometry: the TOP level (no
board above) uses a classic top-down grasp of the box's top section; the
LOWER levels use a FRONT approach (horizontal wrist inserted into the
inter-board gap from the shelf front) because any from-above descent
collides with the board above — proven repeatedly in live runs. The
front grasp line rides high enough for the wrist to clear the board
(WRIST_CLEARANCE) while keeping fingers on the box.
(4) ik-trajectory solves waypoints with pure-numpy DLS-IK on the shared
Panda kinematics: quaternion-based orientation error (the naive
rotation-vector error is BLIND to 180-degree errors and converged onto
pi-flipped wrists), clamped-error line-searched descent, deterministic
canonical seed retries, a position-only bootstrap, and Cartesian
CONTINUATION for insertion paths (single far solves land on wrist-
flipped branches; interpolating to one sweeps the arm through the
shelf). A continuity invariant refuses discontinuous insertion plans.
(5) The approach flies entirely in free space: rise vertically over the
home footprint, traverse at staging height above every box, then
descend — the raw home-to-pregrasp joint sweep clipped shelf boxes.
(6) Command-layer integral correction closes the MJCF actuators'
~0.08 rad gravity sag (their gains are baked into the asset and are not
PD-reducible), bringing tracking to ~0.01 rad. Stage completion allows
0.10 rad with a bounded at-target dwell (4 s) so contact-blocked joints
cannot stall a plan forever.
(7) LATENT BUG FOUND AND FIXED (scene, present since T04): the MJCF
tendon-approximated finger actuator ignores genesis position control
with default gains — the fingers fall closed and NEVER physically grip.
No prior test asserted finger tracking, so T05's acceptance passed with
a gripper that could not grasp. Fixed by per-profile finger-dof kp/kv
(gripper_kp/gripper_kv in physics.toml), applied in build_scene.
(8) KNOWN COVERAGE GAP (owner decision pending): expert v0 reliably
places TOP-LEVEL boxes (verified end-to-end, live). Under-board levels
still fail (insertion blocked by forearm/board geometry or IK refusals):
~10 of 25 seed-0..4 placements fail to plan and front-mode execution has
not yet produced a live success. M0-1 (pass1 >= 0.95 across seeds 0..49)
is NOT reachable with this expert as-is. Options for the owner: (a) an
env-change to shelf geometry (larger level spacing / fewer levels);
(b) invest in a collision-aware planner; (c) re-scope M0-1's episode
distribution. The T08 live test pins the verified case (seed 3,
ibuprofen, top level) and names this gap.
(9) The reaper patterns in tests/conftest.py now cover every pipeline
node: leaked copies of the new nodes strangled the machine to load 200+
during live iteration (same failure class as T05's orphan bug).
