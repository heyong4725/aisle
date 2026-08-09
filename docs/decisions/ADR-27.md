# ADR-27 — Official SO-101 model and 5+1 topic contract (issue #13)

Status: ACCEPTED 2026-08-08. Owner: @heyong4725.

Issue #13 resumes M0-5 using TheRobotStudio's official SO-101 simulation
model at commit `7629d2ad9853d10fb903093a33ef6114099d97e5`, licensed Apache-2.0
(`https://github.com/TheRobotStudio/SO-ARM100`). The accepted asset is
`Simulation/SO101/so101_new_calib.urdf` together with every referenced STL
mesh, the upstream SO-101 README, and the upstream license. Vendored files
retain their relative layout and record the source commit so the environment
is reproducible (CON-5, SCN-4).

The official follower URDF has six actuated joints total: five arm joints
(`shoulder_pan`, `shoulder_lift`, `elbow_flex`, `wrist_flex`, `wrist_roll`)
and one `gripper` joint. TC-5's former `6+1` notation incorrectly counted the
six motors as six arm joints and then added the gripper a second time. The
owner approved correcting the frozen contract to `5+1`; no synthetic joint is
introduced. Joint limits, origins, axes, inertials, meshes, and the new-
calibration zero convention come from that pinned official model. AISLE may
derive normalized `gripper_cmd` conversion and conservative safety velocity
limits where the upstream README explicitly leaves the LeRobot gripper
mapping unresolved, but those derivations must be documented and tested.

## Motion-stack amendment (2026-08-08)

The planner and budget guard parse the serial chain through the official
`gripper_frame_link` directly from the vendored URDF; a zero-pose regression
matches the Genesis import, so AISLE does not maintain a second SO-101 DH
table. `env/limits.toml` retains the URDF's exact position limits and its
declared `10 rad/s` velocity for all six motors. Normalized gripper command
`g` maps linearly from the official open upper limit to the closed lower
limit: `q = upper + g * (lower - upper)`; its normalized rate is the official
`10 rad/s` divided by the official `1.919863 rad` jaw travel.

The imported chain also resolves a provisional-planning mistake: at the
compact shelf, the official wrist-flex range cannot realize a vertical
top-down TCP. SO-101 therefore uses its native radial-front grasp. Its five
independent IK constraints are TCP position plus pitch/roll; world yaw is
coupled to target azimuth by `shoulder_pan` and is left free. The desired
front frame is derived from each waypoint (radial tool axis, tangential jaw
motion axis, remaining axis up), so shelf-to-tray Cartesian continuation remains
feasible without synthetic joints or relaxed official limits. This replaces
ADR-6's provisional “top-down tool axis” statement with measured official-
chain behavior.

Genesis uses the STS3215 position gains (`kp=998.22`, `kv=2.731`) from the
pinned official
[`so101_new_calib.xml`](https://github.com/TheRobotStudio/SO-ARM100/blob/7629d2ad9853d10fb903093a33ef6114099d97e5/Simulation/SO101/so101_new_calib.xml).
Joint limits, axes, fixed frames, and mesh references come from the matching
official
[`so101_new_calib.urdf`](https://github.com/TheRobotStudio/SO-ARM100/blob/7629d2ad9853d10fb903093a33ef6114099d97e5/Simulation/SO101/so101_new_calib.urdf).
The simulator profile keeps these official model parameters separate from
AISLE-derived operational values.

Genesis cannot retain a hobby-arm pinch through the horizontal carry, so the
accepted ADR-18 kinematic-carry pattern is used: normalized close captures the
nearest carton within a geometric envelope, carries its yaw/upright pose, and
open releases it back to physics. It receives no goal or target identity. The
distance origin is the official fixed `gripper_frame_link` TCP, not Genesis's
surviving parent `gripper_link`; using the parent link displaced the query by
98 mm and produced the failed 39/50 campaign by repeatedly selecting an inner
neighbor. Capture is at `0.68`, preclose is `0.60`, and the commanded close
stops at `0.70`, below the measured physical-contact onset near `0.75`.
Normalized topic endpoints remain exactly `0=open, 1=closed`; these are
documented simulator operating points, not an endpoint remap.

The SO-101 profile scales medicine geometry by `0.50`, so the widest
tangential face is 22.5 mm inside the configured 24 mm usable opening. The
radial-front capture TCP sits 120 mm above the carton center, keeping official
robot collision geometry out of the planogram while the nearest-object latch
remains inside its 200 mm envelope. Position carry is yaw-relative and height-
preserving, matching ADR-18; orientation stays upright. Build-time
reachability calls the same pure grasp planner and URDF-derived IK as runtime.

The measured insertion radius remains capped at 440 mm, below the official
chain's 545.7 mm swept maximum. The lower board uses a seed-randomized,
physics-configured two-column slot lattice on a 680 mm shelf: medicine-to-slot
assignment and a global ±1 mm translation vary by seed, while every candidate
is rejection-checked for board bounds, reach, overlap, and at least 180 mm
pairwise center clearance. The upper level remains a 10 mm rear rail, so the
scene still has the specified staggered two-level geometry. Labels, classes,
goal distribution, graph YAML, and verifier criteria are unchanged.

The trajectory rises to 300 mm, performs radial-front insertion, lifts and
retracts gently, transfers at 300 mm, lowers over the side tray, releases, and
returns home. Every waypoint is solved through the official limits. The exact
M0-5 command (`graphs/expert_t0.yaml`, profile swap only, seeds `0..49`) passed
50/50 with `pass1=1.00` in run `m0-5-so101-final-v2`; the prior candidate's
39/50 result is retained as negative evidence for the fixed-frame bug. Issue
#13's deferred M0-5 gate is therefore satisfied.
