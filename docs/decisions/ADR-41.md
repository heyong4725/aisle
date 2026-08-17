# ADR-41 — skill applicability: what a machine can check, and what it cannot

Status: ACCEPTED 2026-08-17 (issue #264). Adds CAP-1b (`spec-change`).
Continues ADR-40, which added the first optional manifest field.

## Context

An evalcard records that a skill **worked on a suite**. It records nothing
about **when reaching for it is appropriate**.

The concrete case that raised it. After `ik-transfer-v2` was registered, an
agent searching its capability saw:

```
ik-trajectory    origin=hub             eval.pass_rate=1.0   launchable=True
ik-transfer-v2   origin=agent-authored  eval.pass_rate=1.0   launchable=True
```

Two entries, one capability, identical headline numbers, **no basis to
choose**. Nothing said that `ik-transfer-v2` matters only when the transfer
path sweeps a shelf at the carried box's level — outside that geometry it is
the stock plan plus overhead.

H3 assumes accumulated skills transfer. It measures whether reuse *happened*;
it gives the agent nothing to reason about whether it *should*. As a library
grows, undifferentiated same-capability entries make selection harder, so the
accumulation benefit can invert — which would be a self-inflicted wound on the
hypothesis the library exists to test.

## Decision

Two optional fields, split by **what a validator can actually check**. That
split is the decision; the fields are its consequence.

**`specializes: <id>` — checked.** Names a capability sibling this entry is a
narrower way of doing. Lint requires the target to exist, to share at least
one `provides` entry, and not to be the manifest itself. This lets search
present a hierarchy instead of a flat list.

**`applies_when: <string>` — deliberately unverifiable.** One sentence,
capped at 280 characters, on the situation the entry is for. No validator can
check that prose matches behaviour.

**Both keys are returned by search on every match, explicitly null when
unset.** The gap was never that the data could not be stored — a manifest is
YAML and always could have carried a comment. It was that nothing put the
information in front of the decision. A key that disappears when empty is a
key readers stop checking for.

## The tension, stated rather than resolved

Issue #264 named the problem with any solution here: **agent-authored free
text is unverifiable, and a field the validator cannot check is
documentation.** That argument is correct, and it is why `applies_when` is
capped, advisory, and described in the schema itself as unverifiable rather
than dressed up as a contract.

The alternative — admit only checkable fields — was rejected because it
answers the wrong question. `specializes` tells an agent that one entry
refines another; it cannot say *when the refinement pays*, which is the thing
the reader actually needs. Shipping only the checkable half would have left
the original complaint intact while looking like it had been addressed.

So the honest position: half of this is enforced and half is a note from the
author. The schema says which is which, at the point of use.

## Consequences

- `ik-transfer-v2` carries both fields, and the search result that motivated
  the issue now discriminates.
- Lint gains three failure modes (dangling target, unrelated capability,
  self-reference). Each was chosen because it would silently corrupt the
  hierarchy search builds from.
- Nothing is required. Every existing manifest stays valid, and an author who
  has nothing useful to say says nothing — which is better than a mandatory
  field filled with noise to satisfy a linter.
- The fields are advisory to *selection*, never to *safety*. No gate consults
  them. `safety_class`, the guard, and the evalcard floor are unchanged, and
  an entry claiming broad applicability gains no privilege from saying so.

## Alternatives considered

- **`known_limits` (where the author measured it NOT helping).** Deferred:
  strictly more useful than `applies_when` and strictly harder to obtain,
  since it requires the author to have run the negative case. Worth revisiting
  once a campaign produces that evidence naturally.
- **A structured precondition language** the validator could evaluate against
  a graph. Rejected as premature: it needs a vocabulary of situations that
  does not exist, and inventing one from a single example (shelf-sweep
  geometry) would fix the abstraction to that example.
- **`eval.population` as a field** rather than the comment it currently is in
  `eval.yaml`. Rejected for now — it describes the exam rather than the skill,
  and belongs with the suite definition, not the capability.
- **Ranking search results by evalcard.** Rejected: `pass_rate` measures a
  suite the entries do not share, so ordering by it would invent a comparison
  the numbers do not support. A hierarchy is a weaker and truer signal.
