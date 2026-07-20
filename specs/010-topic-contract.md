# SPEC 010 — Driver topic contract v0

Status: STABLE after M0. Class C (CON-10) — changes need human review.
This contract is what makes sim→real a node swap. Hardware drivers in Phase 4
MUST honor it byte-for-byte.

## 1. Conventions

- TC-1: All angles radians (float32); all positions meters; all frames are the
  robot BASE frame unless a topic name says otherwise. Quaternions are (x,y,z,w).
- TC-2: Every output message MUST carry metadata keys: `sim_time_ns` (int),
  `env_id` (int, 0 in single-env mode), `seq` (per-topic monotonic int).
- TC-3: Image topics carry metadata `h`, `w`, `enc` ("rgb8") and data as a flat
  `UInt8` Arrow array of length h*w*3. Consumers MUST NOT assume resolution.
- TC-4: Rates are contracts, not hints: producers MUST publish within ±20% of
  the declared rate under nominal load; consumers MUST tolerate jitter within that band.

## 2. Topics (producer → schema @ rate)

| Topic | Dir | Arrow schema | Rate | Notes |
|---|---|---|---|---|
| `rgb_overhead` | out | UInt8[h*w*3] | 30 Hz | TC-3 metadata |
| `rgb_wrist` | out | UInt8[h*w*3] | 30 Hz | attached to EE link |
| `depth_overhead` | out | Float32[h*w] | 15 Hz | meters; 0 = invalid |
| `joint_state` | out | Float32[n_dof] | 100 Hz | meta `names: list[str]` |
| `gripper_state` | out | Float32[1] | 100 Hz | 0 open … 1 closed |
| `oracle_state` | out | Float32[n_obj*7] | 30 Hz | pos+quat per box, order = scene manifest; VERIFIER-ONLY (VAL-6) |
| `poses` | out | Float32[n_obj*7] | 30 Hz | ground-truth box poses for tier-T0 oracle perception; NON-privileged (VAL-6 governs oracle_state only); T1/T2 tier gating of this topic is a Phase-2 validator rule (issue #2 resolution) |
| `joint_cmd` | in | Float32[n_dof] | ≤100 Hz | position targets |
| `gripper_cmd` | in | Float32[1] | ≤30 Hz | |
| `episode_result` | out (verifier) | JSON utf8 | per episode | see §3 |

- TC-5: The bridge MUST publish `joint_state` and accept `joint_cmd` for BOTH
  embodiment profiles (`franka` n_dof=7+2, `so101` n_dof=6+1) with identical
  semantics; `names` metadata disambiguates.

## 3. Services and actions (dora patterns)

- TC-6: Reset is a dora SERVICE (request/reply via `request_id` metadata,
  per dora docs/patterns.md): request `reset` payload UInt32[2] = (seed, mode)
  where mode 0=teleport 1=behavioral; reply `reset_done` payload UInt32[1]=1
  with metadata `seed`, `mode`, `t_reset_ms`. The bridge MUST NOT publish
  observations between receiving `reset` and sending `reset_done`.
- TC-7: An episode is a dora ACTION (goal/feedback/result via `goal_id`):
  goal `episode_goal` = JSON `{tier, target_med, timeout_s, seed}`;
  feedback `episode_feedback` = JSON `{t, phase}` at ≥1 Hz;
  result `episode_result` = JSON:
  `{"status": "success"|"fail", "failure": null|"wrong_object"|"dropped"|
    "timeout"|"never_grasped"|"collision", "t_end": float, "seed": int,
    "goal_id": str, "verifier": "oracle"|"realistic"}`.
- TC-8: `episode_result.status == "success"` from the ORACLE verifier is the
  ONLY ground truth any metric may count. Realistic-verifier verdicts are
  recorded alongside for the fidelity metric, never substituted.

## 4. Acceptance

- TC-A1 (`tests/accept/test_contract.py::test_schema_conformance`): run the
  bridge 10 s headless; every observed message validates against §2 schemas,
  TC-2 metadata present, rates within TC-4 band. Cites TC-1..5.
- TC-A2 (`::test_reset_service`): 20 seeded resets; no observation interleaves
  reset→reset_done; identical seed twice ⇒ identical `oracle_state` first
  message (CON-5). Cites TC-6.
- TC-A3 (`::test_episode_action_lifecycle`): scripted trivial episode ends with
  a schema-valid `episode_result` carrying the goal's `goal_id`. Cites TC-7/8.
