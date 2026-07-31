# H4 findings — iteration latency, hot-swap vs relaunch (design doc §8.3 item 5, §6 H4)

**Status: MEASURED at T0. Result: a NULL — hot-swap does not beat
relaunch on end-to-end iteration latency at this tier** (median 52.0 s
vs 50.2 s, ratio 0.97, ranges fully overlapping, n=6 per path). The
design doc's expectation (§10.3: hot-swap as the iteration-latency
win) does NOT materialize at T0 under honest crediting. The MECHANISM
itself is ~2x faster for hot-swap; the end-to-end latency is dominated
by waiting for a creditable episode in both paths.

Protocol: `docs/decisions/ADR-h4-iteration-protocol.md`. Metric:
HAR-12 (idea-open ts → first episode result under the change), bounded
by started-after-the-change crediting — an episode already running
when the change lands is never credited (the shakeout showed a
straddling episode can fail from the swap itself; the relaunch path
kills it outright).

**Reproduce:** `uv run python tools/h4_iteration.py --analyze
analysis/h4/records/h4_latency.jsonl` — the table is recomputed from
the raw per-rep records (poll-derived episode timelines, idea/change
timestamps); nothing below is hand-computed.

## The table (12 reps, interleaved, one machine)

| Path | n | median (s) | min–max (s) | mechanism (s) | wait-for-credited (s) |
|---|---|---|---|---|---|
| relaunch | 6 (+1 infra-failed) | **50.2** | 40.8–59.5 | 3.9–5.0 | 36.8–54.5 |
| hot-swap | 6 | **52.0** | 47.3–65.6 | 2.4–3.1 | 44.9–62.5 |

median ratio (relaunch/hot-swap): **0.97**

- *Mechanism* = idea → change applied (relaunch: stop + graph edit +
  full SPEC 060 validate; hot-swap: HAR-10 validate + remove + 2 s
  settle [dora-rs/dora#2916 workaround] + add + health belt).
- *Wait* = change applied → the credited episode's result (relaunch:
  scene build + first episode; hot-swap: in-flight episode remainder +
  one full episode).

## Why the expected win vanishes at T0

The hot-swap mechanism is genuinely faster (~2.4 s vs ~4.4 s — and
that relaunch figure already excludes agent authoring time, identical
in both paths by the null-variant design). But T0's Genesis scene
builds in ~10–20 s warm and an episode runs ~25–35 s, so BOTH paths
spend ~90% of their latency waiting on sim time: the relaunched stream
pays scene build then credits its FIRST episode, while the live stream
must wait out the straddler before a creditable episode even starts.
Those two waits are the same size at this tier. The
scene-build:episode-length ratio decides the winner, and at T0 it is
near 1.

**What this does NOT establish:** S-tier scenes build for minutes and
episodes run for minutes — where build time dominates, relaunch pays
it per iteration and hot-swap does not. That extrapolation is UNTESTED
here (an S-tier repetition of this protocol is the follow-up), and H4's
full claim also needs the monolithic-script control condition, which
this experiment does not touch. What IS established: the substrate's
live-mutation machinery works (validated, gated, evidence-logged) at a
~2.4 s mechanism cost, and iteration latency at T0 is episode-bound,
not mechanism-bound.

Unmeasured but observed: relaunch kills the in-flight episode and
idles the whole stream during rebuild; hot-swap risks only the
straddler (one `collision` in the shakeout) while the stream keeps
scoring. In a fleet setting that throughput difference — ENPIRE's
idle-robot concern — is a separate benefit axis this single-stream
metric does not capture.

## Infra disclosures

- One relaunch rep (rep 6, first batch) timed out under external load
  (a concurrent rustc build) and is counted `failed`, never averaged.
- The campaign was interrupted twice by cross-instance
  `dora destroy` from an unrelated dora dev checkout sharing the
  default coordinator port — root-caused via a log tripwire and filed
  as dora-rs/dora#2924; the final 3+3 reps ran on an isolated
  coordinator port (6113), a pattern now recommended for every
  daemon-mode run on shared machines.
- The hot-swap path rides the shakeout-hardened HAR-10: settle
  (dora#2916), post-add health belt, probe env pinning (dora#2918),
  sequencing against init cross-talk (dora#2917). All nine upstream
  reports: `docs/upstream/dora-issues/`.

IDs: design doc §8.3 item 5, §6 H4, §9.1 decision 1, §10.3; SPEC 070
HAR-10/HAR-12; ADR-h4; CON-5/CON-8/CON-12.
