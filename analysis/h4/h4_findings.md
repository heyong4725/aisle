# H4 findings — iteration latency, hot-swap vs relaunch (design doc §8.3 item 5, §6 H4)

**Status: MEASURED at T0, phase-randomized (ADR-h4 rev 2). Result:
hot-swap's median iteration latency is LOWER than relaunch — 32.4 s vs
41.8 s (ratio 1.29), n=6 per path, zero infra failures** — with
overlapping extremes (hot-swap 23.2–46.5 s is phase-dependent by
construction; relaunch 40.8–60.9 s is tight save one under-load
outlier); no formal significance or equivalence claim is made at n=6.
The mutation MECHANISM is ~1.7x faster for hot-swap (2.4 s invariant
vs 4.0–4.7 s); end-to-end latency in both paths is dominated by
waiting for a creditable episode.

Two earlier versions of this measurement are retained as SUPERSEDED
evidence and excluded from the table (PR #79 reviews): a PHASE-LOCKED
design whose fixed R,H order pinned every hot-swap idea to the worst
arrival phase and reported the OPPOSITE direction
(`records/h4_latency-phase-locked-SUPERSEDED.jsonl`), and a
phase-randomized batch run with the review fixes UNCOMMITTED, whose
manifests could not identify the code
(`records/h4_latency-dirty-tree-SUPERSEDED.jsonl`, manifests
`-seed{2,3,4}-dirty-tree-SUPERSEDED`; directionally consistent with
the final result).

**This evidence is UNATTESTED (CON-5 as amended by ADR-24): dev
measurement, local baseline, no post-run audit — it makes no
reproducibility claim.** Full provenance per mini-batch
(`records/manifest-seed{10..15}.json`): git_sha `b3f4fe16` with
`git_dirty: false`, platform, env_hash, env_fingerprint, graph
sha256, seeds, order/phase RNG seed, dora CLI/API pair, coordinator
port. The HAR-7 idea log and HAR-12 swap events for every rep ride
with the bundle (`records/ideas.jsonl`, `records/swaps.jsonl`),
captured automatically at batch end.

Protocol: ADR-h4 rev 2 — six independent mini-batches (1 relaunch + 1
hot-swap each, seeded order + seeded uniform(0, 25 s) idea-arrival
phase, fresh isolated coordinator per batch). Crediting: the first
episode STARTED at/after the change completes; straddlers never count.

**Reproduce the table:** `uv run python tools/h4_iteration.py
--analyze analysis/h4/records/h4_latency.jsonl`.

## The table (6 mini-batches, one machine, isolated coordinator)

| Path | n | median (s) | min–max (s) | mechanism (s) | wait-for-credited (s) |
|---|---|---|---|---|---|
| relaunch | 6 | **41.8** | 40.8–60.9 | 4.0–4.7 | 36.6–56.2 |
| hot-swap | 6 | **32.4** | 23.2–46.5 | 2.4 (all) | 20.8–44.2 |

median ratio (relaunch/hot-swap): **1.29**

- *Mechanism* = idea → change applied (relaunch: stop + edit + full
  SPEC 060 validate; hot-swap: HAR-10 validate + remove + 2 s settle
  [dora#2916 workaround, fixed upstream in eec31a40b] + add + health
  belt).
- *Wait* = change applied → credited episode result. Relaunch pays
  scene build + one full episode every time. Hot-swap pays the
  REMAINDER of the in-flight episode + one episode — phase-dependent,
  averaging about half an episode less.

## Interpretation

- At T0, iteration latency is EPISODE-BOUND, not mechanism-bound:
  ~90% of both paths is sim time. The live-mutation machinery itself
  costs 2.4 s, validated and gated.
- The ~9 s median advantage ≈ the scene build + stop grace hot-swap
  skips, minus the phase-average straddler wait it pays. Where builds
  dominate (S-tier scenes build for minutes), the advantage should
  scale up — UNTESTED here; an S-tier repetition is the follow-up.
- H4's full claim also needs the monolithic-script control condition,
  untouched by this experiment.
- Unmeasured but observed: relaunch kills the in-flight episode and
  idles the stream during rebuild; hot-swap risks only the straddler
  while the stream keeps scoring (ENPIRE's idle-robot axis).

## Infra disclosures

- The 60.9 s relaunch outlier ran while a dora development build
  loaded the machine; it is INCLUDED (nothing disqualifies it — load
  is life). No rep failed in the final campaign.
- Earlier attempts were interrupted repeatedly by cross-instance
  `dora destroy` and silent coordinator kills from the co-resident
  dora dev checkout (dora-rs/dora#2924); the mini-batch design (fresh
  isolated coordinator per batch, port 6113) was adopted so an
  ambient kill costs one mini-batch, not the campaign.
- A poll-gap artifact (episodes completing during un-polled sleep
  windows collapsed onto one timestamp, once crediting a physically
  impossible 3.4 s latency) was caught during the rework and fixed
  with a continuous 4 Hz sampler thread; contaminated data was
  discarded before any published number.
- The hot-swap path rides the shakeout-hardened HAR-10 (settle, health
  belt, pid-scoped reaping; probe env pinning). The nine upstream
  reports: `docs/upstream/dora-issues/`.

IDs: design doc §8.3 item 5, §6 H4, §9.1 decision 1, §10.3; SPEC 070
HAR-7/HAR-10/HAR-12; ADR-h4 rev 2; CON-5 (as amended), CON-8, CON-12.
