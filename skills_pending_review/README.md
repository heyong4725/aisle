# Campaign skill sources — staged, provenance VERIFIED, pending owner review

> **Read this first.** These files are staged for review, not
> registered: nothing here is on the curated path or in the frozen set.
> Eval graphs are staged HERE (as `*.graph.yaml`), not under `graphs/`,
> because `graphs/eval_*.yaml` is frozen post-ADR-36 — restoring them
> there is a Class-C env-change for the owner to make separately.

The #242 review recorded `t2-scan-pose`, `t2-scan-tsm`, and
`ik-transfer-v2` as "source not retained" (#245). This directory stages
source for all three, added 2026-08-16.

## Origin

Operator-side campaign worktrees under the main repo's gitignored
`runs/` on the machine where the campaigns ran:

- `runs/h3_desk/worktree_L/skills/{t2-scan-pose,t2-scan-tsm}`
  (desk-H3 arm L, pin `dd4e3f1a`, registered during T2-r2)
- `runs/a3/worktree_F/skills/ik-transfer-v2` (A3 arm F, pin `8af9b47a`)

copied verbatim, plus each registry manifest as `manifest.yaml`.

## Provenance status: VERIFIED (2026-08-16, on the campaign machine)

The settling check from the #252 review, run by the operator's session
on the machine where the desk-H3 and A3 campaigns executed:

- **Paths exist** and **file mtimes predate 2026-08-16**:
  `t2_scan_pose.py`, `t2_scan_tsm.py` + their eval/skill yamls all
  `Aug 14 10:08` (during L/T2-r2); `ik_transfer_v2.py` `Aug 14 19:22`,
  its `eval.yaml` `Aug 14 19:24` (during A3 arm F).
- **Byte-identical**: every staged file diff-verified against its
  worktree source at staging time.
- The reviewing checkout's absence of `runs/h3_desk`/`runs/a3` is
  explained: campaigns ran on the operator machine; the reviewer's
  environment was a different checkout whose `runs/` never held them.
  The #245 "exists nowhere" claim was scoped to what that environment
  could see; the #247 archival protocol remains the right fix.
- Authenticity signals from the review stand alongside: line counts
  match #242 (210/394/100); cited pins and run ids are real;
  `t2_scan_pose.py` contradicts the #242 note's erroneous
  verifier-import claim in a way reconstruction-from-notes could not.

## The exam papers (#252 review item 2)

- `ik-transfer-v2`: `agent_eval_ik_transfer_v2.graph.yaml` staged here —
  recovered from `runs/a3/worktree_F/graphs/`, so this skill IS
  re-evaluable.
- `t2-scan-tsm`: `eval_t2_scan_tsm.graph.yaml` staged here — recovered
  from the pre-wipe audit snapshot `h3/keep-L-pre-T3-r2` (the PR #61
  keep-ref machinery working exactly as designed), so re-evaluable.
- `t2-scan-pose`: its `eval.yaml` points at `graphs/agent_campaign.yaml`
  (the campaign deliverable itself, which exists) — no separate eval
  graph was ever authored; re-evaluable through the deliverable.

## Reopened owner decisions (#242 sections 3–5)

- `t2-scan-pose`: review can proceed for real (recorded "no fault
  found, nothing to merge" → there is now something to merge or
  decline).
- `t2-scan-tsm`: the recorded DECLINE now verifiable against the actual
  evalcard; note its `min_pass_rate: 0.0` was a documented PROVISIONAL
  attestation registration (see #252 review item 3), below the ADR-37
  floor either way.
- `ik-transfer-v2`: the one motion-class skill is recovered WITH its
  eval graph; the open merge question is live again.
