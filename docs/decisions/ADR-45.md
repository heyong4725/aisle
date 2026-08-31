# ADR-45 — the M1 training-dose ladder (pre-registered)

Status: ACCEPTED (CON-15 — registered before any training step runs).
Follows the M1 lockstep-eval findings (analysis/m1): with latency
removed, the 800-step/4k-tuple LoRA dose has no task competence — so
the measured unlock is DOSE, and each dose is measurable on this
machine via the lockstep condition (ADR-38 amendment 1) before any
GPU is bought.

## Design

ONE training run to 5000 steps on the SAME 4k-tuple corpus and
hyperparameters as the recorded 800-step adapter (CPU, per the MPS
load fence; base model + revision pinned unchanged), with checkpoint
dumps at steps {800, 2000, 5000}. Three doses from one run: the dose
axis is isolated — same data order (seeded), same optimizer
trajectory prefix.

Two tool fixes land with this (both recorded as needed in the M1
findings, neither changes semantics):
- **Pre-decode cache**: the recorded 33 h wall was mp4 random-seek
  per step; unique training frames are decoded once, downscaled to
  the training size, and cached on disk keyed by the video and the
  index set. Same pixels, delivered without seeks.
- **Checkpoint/resume**: model+optimizer+RNG state at every dump
  point; a killed run resumes losslessly.

Each checkpoint is evaluated under the LOCKSTEP condition:
`eval_vla_smolvla_so101_lockstep.yaml`, n=8, seeds 30..37 (the M1
suite), `--env-baseline local`, UNATTESTED — identical to the
recorded 800-step eval, so the 800-step checkpoint doubles as a
REPLICATION of that result under the new data path.

## Pre-registered expectations (reporting-only)

1. Training loss decreases monotonically-ish across doses (the 800
   run measured 0.041 → 0.0066; further decrease expected, small).
2. The 800-step checkpoint's lockstep failure MIX replicates the
   recorded run (fast wrong actions; possibly a grasp-and-drop) —
   a data-path-change control.
3. Task value: pass@1 may stay 0/8 at every dose — that is the
   honest expected floor. The pre-registered SIGNALS short of a pass:
   (a) grasp attempts per episode (cartoonless: episodes with a
   `dropped` failure = the arm at least grasped), (b) collision rate
   falling with dose. Any nonzero pass@1 at any dose is headline.
4. If 5000 steps shows NO movement on any signal, the honest reading
   is that dose alone at 4k tuples is insufficient — the next axis is
   corpus size, and that is recorded as the follow-up rather than
   silently retried.

Wall estimate: ~1-2 min/step train (CPU, cache-fed) → the run is
days-scale and checkpoint evals interleave at dump points. No
rerun-until-pass; one run, three doses, reported as measured.
