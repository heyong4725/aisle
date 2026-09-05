# Calibration procedure (HWP-4, frozen before station results)

Fixtures: the pharmacy tray fixture id `tray-fixture-v1`, a printed
checkerboard (9x6, 20 mm squares) for intrinsics, a 4-marker AprilTag board
on the tray for extrinsics, a machinist's rule (0.5 mm) and a dial indicator
for repeatability.

Environmental conditions recorded per session: ambient temperature,
lighting (lux at the tray), and time since power-on (at least 15 minutes).

Motor calibration (HWP-2), per joint in TC-5 motor order: drive to each
mechanical stop at 10 percent speed, record encoder counts at both stops
and at the marked zero, repeat five times in the order
zero, min, max, zero, max, min; the artifact fields are the medians and the
backlash/repeatability is the range. Gripper: open, contact on the 40 mm
reference block, closed; five repeats. Expiry: 30 days or any mechanical
work. Retry rule: a repeat outside 2 percent of range invalidates the joint
and restarts its sequence. Equations: radians equal
(counts minus zero) times direction times 2 pi divided by 4096.

Workspace calibration (HWP-3): 20 checkerboard poses per camera for
intrinsics (reprojection error under 0.5 px, else redo); 12 AprilTag board
poses for extrinsics with residuals retained; time mapping from 100
synchronized frame and joint-state pairs. Split: poses 1 to 12 calibrate,
13 to 20 evaluate; the evaluation residual is the reported uncertainty.

Raw record schema: `raw/<session>/{images/, joint_samples.jsonl,
measurements.jsonl, residuals.json}`; `tools/hw_calibration.py --build`
regenerates the signed artifacts from them and `--check` runs the VER-8
stage-0 refusal before any robot motion.
