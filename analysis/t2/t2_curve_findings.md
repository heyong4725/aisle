# T2 expert-baseline curve (Phase-2 DoD; design doc §3 tier table)

Ideas I13 (reader design, closed `up`) and I14 (acceptance curve,
closed `down` on the success expectation, safety expectation met).

## The number

| tier | graph | pass@1 | wrong_object | run |
|---|---|---|---|---|
| T2 (labels only, colors shuffled) | expert_t2.yaml | **0.08** (2/25, seeds 0 & 24) | **0 — across all 50 T2 episodes ever run** | 20260811-173916-814353 |

Failure taxonomy (final stack): 15 never_grasped (tours exhausting or
refusing within the 150 s tier budget), 7 collision + 1 dropped
(residual tour-transit sweeps, early-t signatures).

Reference: T1 rows in the same architecture — L0 0.98 / L1 0.96 /
L2 0.72 (earlier epoch).

## Reading the 0.08 honestly

The label READ works when parked: seed-3-class layouts read 4/5 with
margins +0.10..+0.31 under live-grade estimate error, 0 wrong reads.
What dominates the failure budget is TOUR MECHANICS on diverse layouts
(reachability of read poses, transit safety, refusal cascades). That
headroom is the campaign agent's iteration target by design — A1 is a
baseline, not a ceiling — and expert-graph polish stopped here by owner
directive (loop infra over task perfection).

The safety claim the tier exists for held everywhere: **no wrong
medicine was ever grasped**, in any run of any stack revision. Every
failure mode is honest (refusal, timeout, collision) — the asymmetric
penalty (§1: wrong med is 10x worse) is enforced by measured margin
floors, not hope.

## Measured design constants (each from a live/streamed failure)

| constant | value | measurement |
|---|---|---|
| rectification corner order | NW=(+y,−z)… | 5/5 min margin +0.301; other orders read at noise |
| margin floor / pitched floor | 0.04 / 0.15 | pitched wrong-read at +0.093; correct pitched reads +0.28 |
| read-track tolerance | 0.20 rad | good parks 0.03–0.11, shelf jams 0.3–0.9 |
| far-side pitched-first ladder | y > 0.05 | flat entries jam +y faces; knocked boxes to 40–125° tilt |
| one park per candidate | retry=1 | a retry transit knocked a box 3 cm → collision |
| alignment search step | 5 mm | NCC peak ~5 mm wide (0.852 exact vs 0.207 at 5 mm) |
| hypothesis z | board-snap | live L2 z estimate 1–2.5 cm low; per-hypothesis search compressed margins 4× |

## Provenance

PRs #149 (labeled scene), #152 (reader stack), #155 (tier budgets +
candidate bounds), #158 (staged reads). Known limitation: the
single-episode T2 graph gate is wall-coupled (issue #153, ~1/3 flake,
honest failure modes only).
