# H6 (Operation) — campaign findings

Date: 2026-08-26. Protocol: ADR-h6-operation-protocol (amendments 1-5).
Records: analysis/h6/records/ (per-cell raw evidence, transcripts,
manifests; UNATTESTED dev measurements per ADR-24 — no reproducibility
claim). Every number below recomputed by `tools/h6_campaign.py
--analyze`; the table is derived, never hand-written.

## Verdict

**H6 SUPPORTED — 3/3 cells PASS** (pre-registered
criterion: >= 2/3). The registered hypothesis text held in full: in
every cell the agent detected the induced degradation from live
evidence, localized it to the faulted node with cited sanctioned
evidence, restored performance with a validated repair, with zero
`wrong_object`, zero guard bypass, and no out-of-space dependency —
within 300k-token / 2 h ceilings, no human in the loop.

| Cell | Faulted node | fault rate | post rate | detect | repair | tokens | verdict |
|---|---|---|---|---|---|---|---|
| F1 | segmented-pose | 0.00 | 1.00 | 447 s | 477 s | ? | PASS |
| F2 | grasp-planner-topdown | 0.00 | 1.00 | 338 s | 385 s | 280008 | PASS |
| F3 | ik-trajectory | 0.00 | 1.00 | 299 s | 331 s | 168955 | PASS |

(Latencies from launch; the fault is active from launch per amendment
4. Baseline = registered T1 expert 1.0.)

## What the agents actually did (transcripts, audited)

All three sessions converged on the same *differential* method without
it being prompted: establish the failure class from episodes.jsonl,
then eliminate nodes tier by tier with live evidence — trajectory
stage logs to rule the executor in/out, `harness probe` on
target_pose/grasp_pose to arithmetically confirm or exclude planner
and perception offsets, guard stats to rule out blocking.

- **F1 (pose bias +45 mm):** excluded F3 (all 13 stages complete),
  excluded F2 numerically (probed grasp z = target + h/2 - engagement
  exactly), then pinned a ~45 mm lateral bias from failure geometry
  (fingers striking box edges; one IK failure past the reach
  envelope).
- **F2 (grasp +60 mm):** probed grasp_pose vs target_pose on live
  topics (+67 mm above the designed TCP), and — notably — recognized
  target_pose as UNBIASED by comparing against the previous day's
  probes, ruling out F1 with historical evidence.
- **F3 (executor stall at 70% waypoints):** `stage done` stops after
  `retract` (8 of 13 stages) every episode; guard violations empty
  (commands ceased, not blocked); upstream nodes shown healthy.

Every repair followed the sanctioned path: corrected graph authored
from reference entries, `harness validate`, `repair.json` declared,
one relaunch. Transcript audits: no graph-env or swap-ledger reads in
any cell (F2 wrote its own env-redacting reader to inspect topology —
the compliant pattern). The fault-menu source reads (F1, F3) are the
ADR's anticipated confirmatory reads: in each, stream evidence
independently carried the localization.

## Protocol lessons (all pre-registered as amendments before scoring)

1. **Faults must be measured, not assumed** (amendments 1-2): both
   geometric faults were absorbed by the expert at their original
   magnitudes (6/6 despite 18 mm bias / 25 mm lift) — margin the
   pipeline's engagement depth and settle machinery provide for free.
2. **A one-shot operator exits on a healthy world** (amendment 3):
   sessions must start after faulted evidence exists.
3. **MACHINERY FINDING — hot-swap is structurally broken on lockstep
   graphs** (amendment 4, measured 2/2): HAR-10 node remove/add kills
   the ADR-30 turn — the swap removes a turn participant mid-turn and
   the watchdog aborts the dataflow. H4's live-swap evidence predates
   ADR-30 (its graph had no barrier); the H4 hot-swap-vs-relaunch
   table does not transfer to lockstep graphs. Turn-aware swap (a
   quiescence handshake between HAR-10 and the barrier) is the filed
   substrate follow-up.
4. **Credit windows must be counted as the scorer counts** (amendment
   5): the h4 start inference mis-starts the first post-relaunch
   episode across a stop/start boundary.

## Interpretation bounds

- n=1 session per fault: existence/feasibility per fault class, not a
  rate estimate.
- Repair authoring was easy by construction (reference entries
  provided); the claim tested is detect/localize/validated-restore on
  a live system. Repairs requiring novel node code are a follow-up.
- The fault menu is public in-repo; the audits record menu reads and
  the localization credit rests on cited live evidence in every cell.
- Wall-coupling caveat (ADR-25): cells ran sequentially on an
  otherwise idle machine.

## Invalid attempts (retained under records/superseded/, honesty record)

- F1 attempt 1 (protocol: injection raced a one-shot session; agent
  exited 57 s before the fault landed).
- F1 attempt 2 (machinery: hot-swap injection wedged the lockstep
  dataflow — the amendment-4 finding).
- F2 attempt 1 (measurement: teardown stopped one credited episode
  short of a genuinely restored window).
