# ADR-m3-protocol — M3: neural-env ranking agreement, v0 surrogate

Status: ACCEPTED (CON-15 — protocol pre-registered before any scored
run). Date: 2026-08-27. Scope: next-phases §5.3 / Phase-5 measurement
M3 ("does neural-env candidate RANKING agree with Genesis ranking on
the H1 graph population"); design doc §7.5 three-tier environment
ladder.

## Question

The environment ladder's cheapest tier exists to SCREEN candidates:
run many graphs in a cheap surrogate, promote the promising ones to
Genesis. That is useful iff the surrogate's ranking agrees with the
physics ranking. M3 measures that agreement on a population that
already has recorded Genesis outcomes.

## The v0 backend, declared honestly

A Cosmos/DreamDojo-class learned backbone is GPU-gated (the owner's
open budget decision). v0 is a DETERMINISTIC KINEMATIC SURROGATE
("cartoon physics"): first-order joint tracking toward commands,
grasp-by-proximity attachment (gripper closing within ATTACH_R of a
box top attaches it; opening releases it; a box released inside the
tray AABB settles onto the tray floor), no contact, no friction, no
collision class. It speaks the bridge's exact topic surface (BRG
outputs, TC-6 reset envelope, scene layouts from the same
`sample_placements(seed, ...)` the bridge uses), so a graph swaps
environments without edits — the §7.5 claim being exercised.

v0 CANNOT reproduce contact-mechanism failures (collision, dropped).
The pre-registered expectation is therefore asymmetric: graphs that
fail in Genesis for WIRING/LOGIC reasons should fail in the surrogate;
graphs that fail for CONTACT reasons may pass it. The measurement is
whether the ranking SIGNAL survives that fidelity gap — exactly the
screening question.

## Population (frozen at registration)

The launchable H1 first-graphs with recorded Genesis outcomes
(runs/h1 on the campaign machine; committed copies + sha256 under
analysis/m3/population/): 3 Claude + 13 Codex = **16 graphs**, each
with recorded pass@1 over seeds 0..7 (the H1 protocol,
ROLLOUT_EPISODES=8). No graph is edited beyond the environment swap
and the standard absolute-path staging; the rollout client gets the
SAME seeds 0..7.

## Measurement

Per graph: swap `dora-genesis` → `world-model-env` (same node id, same
wiring), run 8 episodes free-run, pass1_surrogate = successes/8.
Analysis (recomputed from records by `tools/m3_ranking.py --analyze`):

- Spearman rank correlation (average ranks over ties) between
  pass1_genesis and pass1_surrogate over the 16 graphs.
- Top-half/bottom-half screening agreement: fraction of graphs the
  surrogate places on the same side of the population median as
  Genesis.
- Per-graph table with both scores and failure classes.

REPORTING-ONLY: no pass/fail threshold is pre-registered. The
literature reference the design doc names (r≈0.995 for neural-sim
ranking) is the comparison point; v0's number is expected FAR below
it and its distance is the finding that sizes the GPU backbone's
value. Free-run, non-attested (the population predates ADR-30;
UNATTESTED dev measurement per ADR-24). Lockstep parity and a
learned backend are recorded follow-ups, not this registration.

## Interpretation bounds

- n=16, outcome variance concentrated at {0, 0.75, 0.875, 1.0}: rank
  ties dominate; Spearman with average ranks is reported WITH the
  raw table, never alone.
- The surrogate shares the pipeline's own kinematics (fk_tcp), so
  agreement is partly self-fulfilling for wiring failures — noted;
  the screening use-case has the same property.
- One machine, one session; no rerun-until-pass (a graph's surrogate
  run is one attempt; infra failures are recorded and the graph is
  excluded, not retried to success).


## Amendment 1 (v2): the spread population — pre-registered before any run

v0 measured the instrument gap: ranking needs outcome variance the
launchable-H1 population lacks. v2 constructs a population with
KNOWN-ORDERING spread from measured knobs, and thereby asks a sharper
question than agreement: WHERE is the surrogate's fidelity boundary?

**Population (16 graphs, frozen at this amendment):**
- `clean` — expert_t1 unmodified (Genesis prior: 1.0).
- `vel_{0.3,0.5,0.7,1.5,2.0}` — expert_t1 with `AISLE_MAX_JOINT_VEL`
  on ik-trajectory (default 1.0). Timing-graded: low values risk
  budget exhaustion, high values overshoot.
- `fault_{f1,f2,f3}` — expert_t1 with the measured H6 fault magnitudes
  (pose bias 45 mm / grasp lift 60 mm / executor stall). Genesis
  prior: 0.0 each (preflight-measured).
- `h1_{claude_00,claude_10,claude_15,codex_02,codex_06,codex_13}` —
  six v0 population graphs re-used for authored diversity (recorded
  Genesis 0.75–1.0), re-scored fresh in this sweep for
  same-conditions comparability.
- `nof1_neighbours` — expert_t1 with the grasp planner's `neighbours`
  input edge removed (clearance-blind grip-axis selection; Genesis
  prior: degraded on close-packed seeds).

Ground truth: EVERY graph runs fresh in Genesis, 8 episodes, seeds
0..7, oracle verifier (no stale priors are scored). Surrogate: the
same staging, world-model-env v0 unchanged.

**Pre-registered fidelity-boundary predictions (falsifiable):**
1. TIMING/KINEMATIC variants (vel_*, fault_f3) rank correctly: the
   cartoon executes the same commands on the same clock, so slow arms
   time out and stalls fail in both worlds.
2. CONTACT-GEOMETRY faults flatten or invert: the cartoon's attach
   rule (60 mm radius, 100 mm z-window) SWALLOWS a 45 mm pose bias and
   a 60 mm lift, so fault_f1/fault_f2 are predicted ~1.0 in the
   surrogate vs 0.0 in Genesis — the fidelity boundary made visible.
3. Overall Spearman therefore lands mid-range (positive, well below
   the 0.995 literature reference), and EXCLUDING the two
   contact-geometry graphs it rises substantially. Both numbers are
   reported; neither is a pass/fail gate.

Same reporting-only stance, same free-run/UNATTESTED scope as v0.

Measured staging note (before the surrogate side ran): the constructed
variants derive from expert_t1, an ADR-30 LOCKSTEP graph, and the v0
surrogate is free-run by declared scope — the barrier starves and zero
episodes result (measured on all ten constructed variants; the
pre-barrier H1 graphs ran fine). The surrogate staging therefore
STRIPS the turn machinery (barrier node, turn edges, lockstep envs) —
lockstep is opt-in by env, so the pipeline nodes run free exactly as
pre-ADR-30 graphs do. The Genesis ground truth stays lockstep. The
asymmetry is a v0-scope condition, recorded here; true lockstep
participation for the surrogate remains the v0 follow-up.
