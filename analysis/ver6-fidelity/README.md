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

Three findings, in descending order of how much they change what we do next.

### 1. `identity_wrist` is the dominant blocker — and it now has a second failure mode

Expected from #107: the wrist sees the tray only in a ~1 s window around
release, so a 5 s checkpoint cadence usually misses it. Confirmed — but
`ep-0000` passed **all six stages**, so the cadence is not hopeless; it is
lossy. 1 of 5 episodes happened to sample the window.

The unexpected part is `ep-0001`, where the wrist set the **wrong-object
latch on `metformin`** while the correct med was being delivered. This is
the close-range hazard flagged as a prediction in #107 — the wrist sees
neighbouring boxes the overhead camera never has in frame — now observed in
a real run rather than argued. It is a false FAIL caused by the wrist
*working*, not by it being blind.

### 2. `identity_overhead` fails more often on real deliveries than the DR sweep predicted

The five-class calibration sweep (PR #104) detected the target in 5 of 5
cells; here the same stage fails 3 of 5. The sweep teleported each med to
the tray **centre, upright**; a real delivery lands off-centre and tilted.

So the sweep's operating point is measured under easier conditions than
production. That is a methodology finding about our own evidence, and the
fix is to calibrate against recorded deliveries — which this run now makes
possible — rather than against teleported placements.

### 3. The geometry stages contribute independently

`containment` blocks 2 and `upright` 1. Both are grounded on the terminal
overhead detection, so some of that is downstream of finding 2 (no grounded
detection means those stages record `error` and fail closed by design). The
sidecar distinguishes `fail` from `error`, so the two causes are separable
per episode in `runs/<id>/verifier_stages.jsonl`.

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
