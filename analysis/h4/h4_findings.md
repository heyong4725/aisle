# H4 findings — iteration latency, hot-swap vs relaunch (design doc §8.3 item 5, §6 H4)

**Status: MEASURED at T0, phase-randomized (ADR-h4 rev 2). Result:
hot-swap's median iteration latency is modestly LOWER than relaunch —
37.2 s vs 41.0 s (ratio 1.10) — with overlapping distributions at
n=8/6; no significance or equivalence claim is made at these sample
sizes.** The mutation MECHANISM is ~1.7x faster for hot-swap (2.4 s vs
~4.1 s, tight in both arms); end-to-end latency is dominated in both
paths by waiting for a creditable episode. An earlier phase-locked run
of this protocol reported the opposite direction (52.0 s vs 50.2 s);
that design pinned every hot-swap idea to the worst arrival phase and
is retained as superseded evidence
(`records/h4_latency-phase-locked-SUPERSEDED.jsonl` — PR #79 review).

**This evidence is UNATTESTED (CON-5 as amended by ADR-24): dev
measurement, local baseline, no post-run audit — it makes no
reproducibility claim.** Full provenance (git_sha, platform, env_hash,
env_fingerprint, graph sha256, seeds, order/phase seed, dora CLI/API
pair, coordinator port) is recorded per batch in
`records/manifest-seed{2,3}.json`.

Protocol: ADR-h4 rev 2 — seeded random path order AND seeded
uniform(0, 25 s) idea-arrival phase (rev 1's fixed R,H alternation
with ideas logged the instant a result landed made the next episode a
straddler for EVERY hot-swap rep). Crediting is unchanged: the first
episode STARTED at/after the change completes; straddlers never count.

**Reproduce the table:** `uv run python tools/h4_iteration.py
--analyze analysis/h4/records/h4_latency.jsonl`.

## The table (pooled seeded batches, one machine, isolated coordinator)

| Path | n | median (s) | min–max (s) | mechanism (s) | wait-for-credited (s) |
|---|---|---|---|---|---|
| relaunch | 6 (+1 infra-failed) | **41.0** | 39.8–46.2 | 4.0–4.2 | 35.7–42.2 |
| hot-swap | 8 | **37.2** | 29.6–48.4 | 2.4 (all) | 27.3–46.0 |

median ratio (relaunch/hot-swap): **1.10**

- *Mechanism* = idea → change applied (relaunch: stop + edit + full
  SPEC 060 validate; hot-swap: HAR-10 validate + remove + 2 s settle
  [dora#2916 workaround, fixed upstream in eec31a40b] + add + health
  belt). Hot-swap's 2.4 s is invariant; relaunch's ~4.1 s is mostly
  the stop grace + validate.
- *Wait* = change applied → credited episode result. Relaunch pays
  scene build + one episode (tight: 36–42 s). Hot-swap pays the
  REMAINDER of the in-flight episode + one episode — phase-dependent
  by construction (27–46 s), averaging ~half an episode less than a
  full straddler wait.

## Interpretation

- At T0, iteration latency is EPISODE-BOUND, not mechanism-bound:
  ~90% of both paths is sim time. The substrate's live-mutation
  machinery costs 2.4 s, validated and gated — cheap enough that the
  scene-build:episode ratio decides the winner. At T0 that ratio is
  near 1 and hot-swap's edge is the ~4 s of stop+build it skips minus
  the phase-average straddler wait it pays.
- Where builds dominate (S-tier scenes build for minutes), relaunch
  pays that per iteration and hot-swap does not — the expected regime
  for a decisive hot-swap win. UNTESTED here; an S-tier repetition is
  the follow-up.
- H4's full claim also needs the monolithic-script control condition,
  untouched by this experiment.
- Unmeasured but observed: relaunch kills the in-flight episode and
  idles the stream during rebuild; hot-swap risks only the straddler
  while the stream keeps scoring (ENPIRE's idle-robot axis).

## Infra disclosures

- Rep 10 of the seed-2 batch (relaunch) timed out at 600 s
  post-restart and is counted failed, never averaged; the machine was
  shared with an active dora development build throughout.
- The campaign was interrupted repeatedly by cross-instance
  `dora destroy` from the co-resident dora dev checkout (default
  coordinator port; filed as dora-rs/dora#2924) — all batches here ran
  on an isolated coordinator (port 6113, explicit coordinator/daemon).
- A poll-gap artifact (episodes completing during un-polled phase
  delays collapsed onto one timestamp, once crediting a physically
  impossible 3.4 s latency) was caught during the rework and fixed
  with a continuous 4 Hz sampler thread; the contaminated partial
  batch was discarded before any published number.
- The hot-swap path rides the shakeout-hardened HAR-10 (settle, health
  belt, scoped pid reaping — PR #79 review; probe env pinning). The
  nine upstream reports: `docs/upstream/dora-issues/`.

IDs: design doc §8.3 item 5, §6 H4, §9.1 decision 1, §10.3; SPEC 070
HAR-10/HAR-12; ADR-h4 rev 2; CON-5 (as amended), CON-8, CON-12.
