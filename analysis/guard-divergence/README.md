# Guard divergence: proposed vs executed actions (issue #267)

**The question.** Every motion command traverses `budget-guard`, which does not
merely veto — it CLAMPS (BG-3). So a recorded run carries two signals:
`joint_cmd` (what the policy proposed) and `joint_cmd_safe` (what the arm
executed). Before fine-tuning a VLA on expert-graph demonstrations, one thing
has to be decided: **which of those is the demonstration label?**

Neither choice is free. Training on *proposals* teaches the expert's intent
including corrections the model never observes, producing a policy that depends
on a guard being downstream — fine in this harness, unsound as a portability
claim. Training on *executed* actions gives all-legal labels that are a mixture
of two processes, so the model imitates an intervention whose trigger it cannot
see: an off-policy correction problem introduced by a safety mechanism rather
than by exploration.

The issue asked for the divergence statistic first, then a convention.

## The measurement

Run `20260817-170206-79df99` — `graphs/expert_t0.yaml`, tier T0, seeds 0..1,
oracle verifier, 2/2 success, 73.1 s wall / 40.7 s sim.

| quantity | value |
|---|---|
| commands compared | **4071** |
| commands where executed ≠ proposed | **0** |
| max abs(proposed − executed) | **0.0** |
| guard `violation` records | **0** (no `violation` endpoint was ever published) |
| `labels_coincide` | **true** |

Measured two independent ways that agree: element-wise comparison of the two
Arrow streams (`ik-trajectory__joint_cmd` vs `budget-guard__joint_cmd_safe`,
4071 rows each), and the guard's own violation stream, which is empty.

## What this supports

**On the T0 expert corpus the label question is moot** — the two candidate
labels are the same signal, bit for bit. A fine-tune on this data cannot be
biased by the choice, because there is no choice to make.

That is a genuinely useful negative: it means the *easy* part of the
demonstration corpus can be used immediately without settling the convention,
and it isolates where the convention actually matters.

## What this does NOT support

**It is one tier, one graph, two episodes.** Do not read it as "the guard never
clamps".

Three reasons to expect divergence elsewhere, in increasing order of
importance:

1. **T0 is the easiest tier.** Fixed pose, no tour, short trajectories.
2. **The expert graph is tuned.** It has been iterated against these limits for
   the life of the project; staying inside them is what "expert" means here. An
   *agent-authored* motion node has no such history — and agent-authored motion
   code is exactly what a fine-tuning corpus would eventually include.
3. **T2 is where the failure budget is transit collisions** (15 `never_grasped`,
   7 `collision`, 1 `dropped` on the expert baseline). Collision-adjacent
   trajectories are precisely where workspace and velocity clamping would bite,
   and T2 is the tier a useful VLA would have to handle.

So the honest statement is: **divergence is zero where it was measured, and
unmeasured where it is most likely to be non-zero.**

## What changed as a result

- `src/aisle/harness/guard_divergence.py` — aggregates the statistic. Rate is
  per COMMAND, not per violation record (one command can clamp several joints);
  magnitude is reported alongside, because clamping 1% of commands by 2 rad and
  40% by 1e-4 are opposite conclusions; records with no numeric delta
  (`wall_timeout`, `malformed`) count toward the rate and are excluded from the
  magnitude rather than coerced to 0.0.
- Every run manifest now carries `guard_divergence`. Persisted rather than left
  recomputable, because the traces live under gitignored `runs/` — a statistic
  available only "in principle" is the #245/#266 failure one artifact over.

## Recommendation (the decision is the owner's)

Do not fix the convention yet. Run this measurement on **T2** and on an
**agent-authored** motion node first; those are the cells that can actually
discriminate, and a convention chosen from an all-zero corpus would be chosen
from no evidence. If T2 also shows zero, the question dissolves and that is
worth knowing. If it does not, the divergence rate and magnitude together tell
you which label is defensible — and the same number quantifies how much of the
expert graph's competence belongs to the guard rather than to the policy.
