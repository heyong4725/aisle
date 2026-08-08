# VER-6 verifier fidelity — first measurement

**Phase 2 DoD item: "verifier-fidelity number" (design doc §8.3).**

Agreement between the ORACLE verifier (VER-1..3, privileged state) and the
REALISTIC verifier (VER-5, pixels only) on identical episodes.

## Result

Run `fidelity-I9` — `graphs/expert_t0.yaml`, tier T0, 5 episodes, seeds 3..7,
recorded with `AISLE_FRAME_CAPTURE_PERIOD_S=5` (VER-9's checkpoint cadence),
judged offline by `tools/judge_recorded_run.py`, compared by
`harness/fidelity.py`.

| metric | value |
|---|---|
| episodes | 5 |
| oracle success | 5 |
| realistic success | 1 |
| **agreement** | **0.20** |
| false SUCCESS rate | n/a — no oracle failures in this run |
| **false FAIL rate** | **0.80** |

**Every disagreement is a false FAIL.** The realistic verifier never claimed
success where the oracle said failure, which is the safety-relevant
direction: it is conservative, not permissive. That is what VER-3's
asymmetry and VER-13's fail-closed fusion are for, and it holds here.

The number is low, and the value of it is the attribution rather than the
scalar.

## Stage attribution (VER-14 / ADR-realistic-verifier D4)

Episodes in which each stage did not pass:

| stage | blocked | note |
|---|---|---|
| `identity_wrist` | 4 / 5 | dominant |
| `identity_overhead` | 3 / 5 | |
| `containment` | 2 / 5 | |
| `upright` | 1 / 5 | |
| `calibration` | 0 / 5 | stage 0 accepts the published block |
| `home` | 0 / 5 | |

**Every one of the four false fails traces to one of two causes, and the
`detail` field in the sidecar is what distinguishes them — reading stage
votes alone gives the wrong story (it gave me one; see the correction note
at the end).**

| episode | cause |
|---|---|
| `ep-0001` | false wrong-object latch (wrist saw `metformin`) |
| `ep-0002` | wrist never detected the target + containment resting-gap fail |
| `ep-0003` | false wrong-object latch (**overhead** saw `cetirizine`) |
| `ep-0004` | false wrong-object latch (**overhead** saw `omeprazole`) |

### 1. Three of four false fails are FALSE WRONG-OBJECT LATCHES, and two came from the OVERHEAD camera

At each latching frame **exactly one** class is detected inside the tray ROI
and it is the wrong one, with no target detection on that frame:

| episode | target (colour) | latched on | score | camera |
|---|---|---|---|---|
| `ep-0003` | omeprazole (purple) | cetirizine (blue) | 0.1485 | overhead |
| `ep-0004` | metformin (green) | omeprazole (purple) | 0.2361 | overhead |
| `ep-0001` | ibuprofen (orange) | metformin (green) | 0.1845 | wrist |

So the detector puts a single confident box in the tray and labels it wrong.
This is **colour confusion on the delivered object** — the same failure mode
#108 recorded for composed SCN-6 randomization, except **domain
randomization is OFF here**, and it happens in 3 of 5 episodes.

That matters because it contradicts the evidence the operating point was
calibrated on. PR #104's sweep reported 70/70 cells clean with no surviving
non-target anywhere, but it **teleported each med to the tray centre,
upright, and settled it**. A real delivery arrives off-centre, tilted, and
sometimes still near the gripper. The calibration distribution was easier
than production, and the envelope in `verifier/thresholds.toml` and VER-9
inherits that optimism. Filed as its own issue.

### 2. `identity_overhead`'s three "failures" are latch inheritance, not detection failures

The latch is episode-global and cross-camera by design (VER-9), so one
camera's false positive fails BOTH identity votes. Overhead target scores on
these episodes are healthy where the latch did not preempt them: 0.3284
(`ep-0000`), 0.1480 (`ep-0002`), and 0.2272 / 0.2629 measured on
`ep-0003` / `ep-0004` frames before their latches. Overhead **detection** is
working; overhead **classification** is what fails.

### 3. The wrist is a contributor, not the dominant cause

`identity_wrist` blocked 4 of 5, but 3 of those are the shared latch. Its
own independent failure is `ep-0002` ("target never detected in tray"), and
its own independent false positive is `ep-0001`'s latch. The cadence
problem (#107) is real but is NOT what dominates this number — and
`ep-0000` passed all six stages, so the ~1 s release window is lossy rather
than hopeless.

### 4. Geometry stages: one genuine failure

`containment` fails once for a real reason — `ep-0002`, "reconstructed
bottom is not resting on the tray floor" — and errors once (`ep-0001`)
downstream of the latch, because no grounded target detection means the
geometry stages fail closed by design. `upright` measured 20.7 deg tilt on
`ep-0002`, under the 30 deg threshold, so it passed.

## What this does NOT establish

- **No false-success measurement.** All five episodes were oracle successes,
  so `false_success_rate` is null. VER-6's headline risk — the realistic
  verifier passing an episode the oracle fails — is untested until a run
  contains oracle failures. That needs a run with genuine failures in it,
  not a synthetic one.
- **n = 5.** This is a first measurement on one tier, one graph, DR off.
- **Not the live path.** Judged offline from recorded frames; the dora node
  that publishes `episode_result` with `verifier:"realistic"` is increment
  1b. Offline judging is what makes the number replayable (VER-7) and is
  what A7 will need to be trustworthy, but it is not the same code path a
  live run would take.

## Reproducing

```bash
DORA_COORDINATOR_PORT=6113 AISLE_FRAME_CAPTURE_PERIOD_S=5 \
  uv run harness rollout --env-baseline local --graph graphs/expert_t0.yaml \
  --tier T0 --episodes 5 --seeds 3..7 --reset teleport --run-id <id>
uv run python tools/judge_recorded_run.py --run runs/<id>
uv run python -m aisle.harness.fidelity --run-dir runs/<id>
```

The frames are bytes on disk, so a verifier change can be re-scored against
the SAME episodes without re-simulating — which is the point of #105.


## Correction

The first version of this note claimed `identity_overhead` failed 3/5
because real deliveries land off-centre versus the sweep's centred
placements. That was wrong: those three failures are **latch inheritance**,
and the latches were false wrong-object detections — two of them from the
overhead camera itself. I had read the stage `vote` fields without reading
the `detail` and `latch` fields next to them, which is the same mistake in
kind as calibrating on a whole-frame maximum instead of the ROI-filtered
score. The sidecar records the cause; the vote alone does not.

The substantive consequence changed too: the fidelity gap is dominated by
**false wrong-object latches with DR off**, not by wrist cadence.
