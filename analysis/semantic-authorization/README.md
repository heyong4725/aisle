# Semantic authorization boundary (SPEC 480, issue #352)

Evidence kind: `synthetic`. This directory holds the mechanism SPEC 480
asks to evaluate and its held-plan adversarial replay. No simulator or
hardware executed a transition; identity assertions are scripted; the
result establishes that the mechanism enforces the declared semantics of
every SEM-10 condition, and nothing about deployability, portability, or
physical prevention (SEM-14 to SEM-16).

## Mechanism

`src/aisle/harness/semantic_shield.py`: a `SemanticAuthorizer` separate from
policy, kinematic guard, verifier, and agent workspace (SEM-2) that binds a
signed task assignment, registered identity assertions, and a carried-object
track into a short-lived, single-stage, single-use permit (SEM-3, SEM-4) at
three stages: pre-grasp, post-closure carry, delivery entry (SEM-5).
Missing, refused, stale, future-dated, time-regressing, out-of-envelope,
below-threshold, unsupported, unregistered, or disagreeing evidence yields
no permit; goal change, carrier loss, track change, and restart revoke
(SEM-6). Thresholds are frozen in the corpus (SEM-7). A `PermitGateway`
authenticates and consumes each permit once and refuses replayed,
expired, wrong-stage, wrong-proposal, wrong-episode, wrong-goal-revision,
and wrong-carrier permits.

## Records

`records/sem-held-plan-adversarial-v2/`: `corpus.json` (60 held plans, 15
SEM-10 conditions x 4 seeded variants, expected decision per transition
declared per arm from the condition's semantics), `result.json` (three-arm
replay), `tcb.json` (SEM-2 trusted computing base record). Registration:
`analysis/freeze/sem-held-plan-adversarial-v2/` (pending CON-14 approval of
SPEC 480 and the SEM-8 containment gate, which a synthetic replay cannot
model).

| arm | identity source | plans with any false allow | plans with any false block | permits | halts requested |
|---|---|---|---|---|---|
| no_shield | synthetic sensor, never blocks | 56 / 60, exact 95% 0.836 to 0.982 | 0 / 60 | 76 | 72 |
| oracle_sim_shield | `simulation_oracle` (ceiling only) | 0 / 60, upper 0.060 | 0 / 60 | 112 | 48 |
| sensor_shield | synthetic sensor | 0 / 60, upper 0.060 | 0 / 60, one-sided 95% upper 0.049 | 76 | 72 |

Primary (SEM-11, unit = held plan): false-allow risk difference
sensor_shield minus no_shield is -0.933, Newcombe 95% -0.974 to -0.823.
False-block non-inferiority against the frozen 0.05 margin: one-sided
upper bound 0.049, decision `non_inferior`. The four correct-target
negative-control plans are the only ones no_shield cannot fail.

What this is: an internal consistency result. The corpus, the expected
decisions, and the authorizer were written by the same author; the
replay shows the implementation matches its own declared semantics under
every attack class. It is not sensor-backed simulation evidence, and the
oracle arm measures a privileged ceiling (SEM-15).

## Regenerate

```bash
uv run harness semantic corpus --seed 480001 \
  --output analysis/semantic-authorization/records/sem-held-plan-adversarial-v2/corpus.json
uv run harness semantic run \
  --corpus analysis/semantic-authorization/records/sem-held-plan-adversarial-v2/corpus.json \
  --analysis-seed 480001 \
  --output analysis/semantic-authorization/records/sem-held-plan-adversarial-v2/result.json
```

## Not done here

Integration into a live graph (oracle-backed shield in simulation, then a
sensor adapter over rendered perception), the SEM-8 containment and
kinematic-masking exclusions, the SEM-14 hardware adapter contract and the
SEM-16 claim disposition. H5 wording stays narrowed to measured layers and
zero observed events.
