# ADR-37 — the registry sets the passing grade, not the candidate

Status: ACCEPTED (issue #243, owner decision 2026-08-15 in the PR #242
governance review). Tightens CAP-6. Supersedes no ADR; continues ADR-36.

## Context

`harness skill register` gates registration on the skill's measured eval
(CAP-6/CAP-7). The gate works — `skill.py`:

```python
minimum = float(skill.eval_cfg["min_pass_rate"])
if pass_rate < minimum:
    raise RegistrationError(...)
```

but `minimum` is read from the candidate's own `eval.yaml`, and
`load_skill` only checked that it parsed as a float. There was no absolute
floor anywhere in `src/`, `specs/`, or `registry/`.

**So the exam was self-graded.** A skill shipping `min_pass_rate: 0.0`
registers at pass_rate 0.0 and the gate reports `ok: true`.

Not hypothetical: `t2-scan-tsm` (desk-H3 L/T2-r2, `safety_class: decision`)
entered that campaign's library at **pass_rate 0.0**
(`analysis/h3/desk/desk_analysis.json`, `cells[*].skills_after`; PR #242
review notes item 4).

The two skills that reached mainline both chose 0.5 independently
(`skills/s1-driver-v2/eval.yaml`, `skills/s3-driver-v1/eval.yaml`), so the
hole was never exploited adversarially — only accidentally. That is luck,
not a gate.

## Decision

**An absolute floor, `REGISTRY_MIN_PASS_RATE = 0.5`, under every skill's
self-declared `min_pass_rate`.** A skill may hold itself to more; never to
less. A sub-floor declaration is refused at LOAD, before the eval rollout
is spent.

This is ADR-36's argument one layer down. There, the exam paper was
editable by the candidate and we froze it. Here, the passing grade was
chosen by the candidate. An evalcard whose threshold the candidate picked
attests nothing, and §9.4 builds trust tiers — eventually `safety_class:
motion` ceilings on real hardware — on top of exactly that evalcard.

**0.5 is calibrated, not chosen in the abstract.** It is what both
registered skills picked for themselves. A floor that evicted a shipped
skill would be a silent de-registration of merged work, so the calibration
is asserted rather than assumed:
`::test_every_shipped_skill_meets_the_registry_floor` fails if any skill in
`skills/` declares below it.

## Consequences

- No mainline skill is affected: both declare exactly 0.5.
- A campaign agent that would have registered a failing skill now gets a
  refusal naming both numbers, at load, having spent no episodes. Refusing
  before the rollout matters under ADR-21 metered budgets: the old path
  would run the full eval and only then decide.
- The floor is a MINIMUM. `s3-driver-v1` holding itself to 0.5 and
  `t2-scan-pose` holding itself to 0.9 are both governed by their own
  number; the floor only removes the bottom of the range.
- Raising the floor later is a further spec-change and would evict any
  shipped skill below the new value — which is why the corpus test names
  the eviction, not just the rule.
- The unenforced-floor class is now closed in the two places it existed:
  the eval graph (ADR-36) and the eval threshold (here). What remains
  candidate-chosen is the eval's SEED SET and episode count, which is a
  live and unaddressed version of the same shape — noted for the trust-tier
  roadmap rather than fixed here, one spec concern per PR.

## Alternatives considered

- **Refuse only `min_pass_rate <= 0.0`.** Rejected: it fixes the observed
  case and leaves the mechanism. A skill declaring 0.05 is exactly as
  self-graded as one declaring 0.0, and the next campaign would find that
  out rather than the reviewer.
- **Leave it, document that reviewers must check the declared floor.**
  Rejected on ADR-36's precedent: a gate whose integrity rests on a human
  noticing a number in a YAML file is advisory, and advisory separations
  are the class VAL-6 and VAL-8 exist to make structural. The whole point
  of registration is that it is the machine-checked path past the curated
  core.
- **Take `max(declared, floor)` at the measured gate as defense in depth.**
  Rejected as unreachable: once load refuses sub-floor declarations, the
  declared value is always at or above the floor and the `max()` can never
  bind. Dead code that reads like a safeguard is worse than no code.
