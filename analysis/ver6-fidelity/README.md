# VER-6 verifier fidelity — first measurement

**Phase 2 DoD item: "verifier-fidelity number" (design doc §8.3).**

Agreement between the ORACLE verifier (VER-1..3, privileged state) and the
REALISTIC verifier (VER-5, pixels only) on identical episodes.

## Result

Run `fidelity-I9` — `graphs/expert_t0.yaml`, tier T0, 5 episodes, seeds 3..7,
DR off, recorded with `AISLE_FRAME_CAPTURE_PERIOD_S=5` (VER-9's checkpoint
cadence), judged offline by `tools/judge_recorded_run.py`, compared by
`harness/fidelity.py`.

| metric | value |
|---|---|
| episodes | 5 |
| oracle success | 5 |
| realistic success | 2 |
| **agreement** | **0.40** |
| false SUCCESS rate | n/a — no oracle failures in this run |
| **false FAIL rate** | **0.60** |

**Every disagreement is a false FAIL.** The realistic verifier never claimed
success where the oracle said failure — conservative, which is the direction
VER-3's asymmetry and VER-13's fail-closed fusion are built for.

## Stage attribution (VER-14 / D4)

| stage | blocked | |
|---|---|---|
| `identity_wrist` | 3 / 5 | dominant |
| `identity_overhead` | 2 / 5 | both are latch inheritance |
| `containment` | 2 / 5 | one genuine, one downstream |
| `upright` | 1 / 5 | downstream |
| `calibration` | 0 / 5 | stage 0 accepts the published block |
| `home` | 0 / 5 | |

Per-episode causes, read from the sidecar's `detail` and `latch` fields
rather than the votes alone:

| episode | outcome | cause |
|---|---|---|
| `ep-0000` | agree (success) | all six stages pass |
| `ep-0001` | false fail | **wrist** latched on `metformin` |
| `ep-0002` | false fail | wrist never detected the target; containment resting-gap fail |
| `ep-0003` | agree (success) | all six stages pass |
| `ep-0004` | false fail | **wrist** latched on `omeprazole` |

### 1. All three false fails are wrist-driven

Two are false wrong-object latches from the wrist camera; the third is the
wrist failing to detect the target at all. The overhead camera contributed
**zero** independent failures — its two blocked votes are inheritance, since
VER-9's latch is episode-global and cross-camera by design.

At close range the wrist frames boxes that the overhead camera never has in
view, so it has a false-positive surface the overhead does not. That was
raised as a prediction in #107 and is now observed twice in one 5-episode
run. It is a false fail caused by the wrist *working*, not by it being
blind — and it means improving wrist cadence without addressing the latch
surface would trade one failure mode for another.

### 2. `ep-0000` and `ep-0003` passed all six stages

Including `identity_wrist`. So the wrist cadence problem (#107) is **lossy,
not hopeless**: 2 of 5 episodes sampled the ~1 s window around release.

### 3. One genuine geometry failure

`ep-0002`'s containment fails on "reconstructed bottom is not resting on the
tray floor". `upright` measured 20.7 deg there, under the 30 deg threshold,
so it passed. `ep-0001`'s containment/upright `error`s are downstream of the
latch — no grounded target detection means the geometry stages fail closed
by design, and the sidecar distinguishes `error` from `fail` so the two are
separable.

## The false-SUCCESS half (the safety-relevant direction)

The positive run above contains no oracle failures, so it cannot test VER-6's
headline risk: the realistic verifier passing an episode the oracle failed.
T0 is too reliable to supply failures by volume — a separate 20-episode run
(`fidelity-neg-I1`, seeds 10..29) came back **20/20 success**.

Induced instead, honestly and reproducibly: `AISLE_MAX_JOINT_VEL=0.08`
(the trajectory executor's own env knob — no graph edit, no frozen file
touched) makes the arm too slow to finish inside the 60 s sim timeout.

Run `fidelity-neg2-I2` — 6 episodes, seeds 3..8, same graph and cadence:

| metric | value |
|---|---|
| oracle outcome | **6 / 6 fail**, all class `timeout` |
| realistic outcome | **6 / 6 fail** |
| agreement | **1.00** |
| **false SUCCESS rate** | **0.00** (0 of 6) |

**The realistic verifier never passed an episode the oracle failed.** These
frames include the box still on the shelf and mid-transit above the tray, so
they exercise the airborne case containment is meant to reject — and
`containment` recorded a genuine `fail` (not `error`) on two of them,
meaning it had a grounded detection and rejected the geometry on its merits.
`home` failed on all six, correctly: the arm is mid-motion at timeout.

**Statistical honesty:** 0 of 6 is consistent with a true false-success rate
up to roughly 40% (rule of three). This establishes that the failure mode is
not rampant; it does not establish a small rate. Treat it as a floor to
build on, not a clean bill.

## Combined result

| | episodes | agreement | false success | false fail |
|---|---|---|---|---|
| positives (`fidelity-I9`) | 5 | 0.40 | n/a | 0.60 |
| negatives (`fidelity-neg2-I2`, induced) | 6 | 1.00 | 0.00 | n/a |
| **combined** | **11** | **0.73** | **0.00 (0/6)** | **0.60 (3/5)** |

The asymmetry is the point, and it is the one VER-3 and VER-13 were designed
to produce: this verifier **rejects correct behaviour** more often than we
would like, and so far **never accepts incorrect behaviour**. For A7 — the
loop driven by the realistic verifier with the oracle held out — that is the
safer failure direction, but a 0.60 false-fail rate would still have the
agent optimising against a verifier that rejects most of what works. The
false-fail cause is entirely the wrist (#107).

## What this does NOT establish

- **No false-success measurement.** All five episodes were oracle successes,
  so `false_success_rate` is null. VER-6's headline risk — realistic passing
  what the oracle fails — is **untested** until a run contains genuine
  failures. That is the next measurement worth buying.
- **n = 5**, one tier, one graph, DR off.
- **Not the live path.** Judged offline from recorded frames; the dora node
  that publishes `episode_result` with `verifier:"realistic"` is increment
  1b. Offline judging is what makes the number replayable (VER-7), but it is
  not the code path a live run takes.

## Two corrections, both mine, both worth recording

The first two versions of this note were wrong, in ways that are more
instructive than the number.

**1. I read stage votes without the `detail` beside them.** I reported that
`identity_overhead` failed 3/5 because real deliveries land off-centre
versus PR #104's centred sweep placements. Those failures were latch
inheritance; overhead detection was healthy throughout (0.148–0.328). Same
mistake in kind as calibrating on a whole-frame maximum instead of the
ROI-filtered score: the number I looked at was not the number the code
decided on.

**2. I judged frames that did not belong to the episode.** The first version
windowed each episode as `(previous episode_result, this episode_result]`.
The frame at the reset boundary still shows the PREVIOUS episode's scene —
the render happens before the teleport is applied — so the previous
delivery appeared as a wrong object in this episode's tray and latched VER-9
on frame one. Two of the three "overhead false latches" were that artifact:
the detector correctly saw a blue `cetirizine` box in the tray, and it was
genuinely there in the pixels; only `oracle_state` had already moved on.

That bug cost a fidelity number (0.20 instead of 0.40) and an issue blaming
the detector for a correct detection. Episodes are now windowed strictly
after their own `reset_done`. **Ground truth and pixels can disagree at a
reset boundary, and when they do, the pixels are what the verifier is
entitled to believe.**

## Reproducing

```bash
DORA_COORDINATOR_PORT=6113 AISLE_FRAME_CAPTURE_PERIOD_S=5 \
  uv run harness rollout --env-baseline local --graph graphs/expert_t0.yaml \
  --tier T0 --episodes 5 --seeds 3..7 --reset teleport --run-id <id>
uv run python tools/judge_recorded_run.py --run runs/<id>
uv run python -m aisle.harness.fidelity --run-dir runs/<id>
```

The frames are bytes on disk, so a verifier change can be re-scored against
the SAME episodes without re-simulating — which is the point of #105, and
what made both corrections above cheap to find.
