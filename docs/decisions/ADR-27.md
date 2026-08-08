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
