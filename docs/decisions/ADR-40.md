# ADR-40 — the sandbox trust tier

Status: ACCEPTED 2026-08-17 (issue #265). Amends CAP-6/CAP-7 (`spec-change`).
Implements the lower rung of the §9.4 `sandbox → reviewed → certified`
roadmap. Continues ADR-37, which created the need for it.

## Context

ADR-37 put an absolute floor under every skill's self-declared
`min_pass_rate`, closing a real hole: the exam was self-graded, and a skill
shipping `0.0` registered at `0.0` while the gate reported success.

It also removed the only mechanism an agent had for a different and
legitimate need. A new node has no manifest, so `validate` refuses any graph
naming it with `MANIFEST_MISSING`. The only path to a manifest is
`harness skill register`, which now requires a measured 0.5. **An agent that
wants to run a new node must already have a node that works.**

This is not hypothetical. Both recovered T2 campaign skills shipped
`min_pass_rate: 0.0` with a written rationale:

> `min_pass_rate 0.0` is PROVISIONAL: this registration exists to attest the
> launched source under CAP-2 validation while the campaign measures the
> dev-seed pass rate; the evalcard records the measured rate either way, and
> the human-merged PR remains the trust boundary.

That is not gaming. It is an agent using registration as **attestation**
(admit an id so the graph validates) rather than as **certification** (claim
quality). The gate conflated two functions; ADR-37 correctly closed the
second and inadvertently broke the first.

## Decision

**`trust_tier: sandbox`** — an optional manifest field and a
`harness skill register --sandbox` path that:

- **admits the id** so graphs naming it validate
- **runs no eval and writes no evalcard** (`eval: null` is REQUIRED, not
  merely allowed — the null *is* the absent claim)
- **can never hold `safety_class: motion`**, per §9.4's per-tier ceiling
- **never counts as a library skill** — for the DoD count, reuse measurement,
  or the "beyond the curated core, only registered skills" rule
- is **promoted only by re-registering without the flag**, which runs the real
  gate; a passing registration writes `trust_tier: reviewed`

Two design choices carry most of the weight:

**The counting filter is the evalcard, not the tier.** Everything that counts
capabilities already requires a non-null evalcard, and a sandbox entry has
none by construction. Adding a second rule that also checks `trust_tier`
would create two sources of truth that can drift — the failure this project
has now hit three times (the tamper audit re-listing frozen paths, the
research contract enumerating globs by hand, the architecture doc's prose
copy of the fence).

**The field is optional, not required-with-a-default.** Every curated-core and
pre-ADR-40 manifest predates the tier. Requiring it would force a mechanical
edit across the frozen registry to assert something those entries do not
claim. Absent means "no tier recorded", which is the truth.

## Consequences

- An agent can declare a node it is still building and get a validating graph,
  without the `min_pass_rate: 0.0` workaround and without weakening ADR-37.
- **A sandbox node cannot actuate at all.** ADR-5 requires that any node whose
  outputs are actuation commands be `safety_class: motion`; §9.4 forbids
  sandbox from being motion. The two compose into a stronger property than
  either states alone, and it fell out of the existing rules rather than
  needing a new one — the test fixtures had to change to reflect it, which is
  how it was discovered.
- `certified` exists in the enum and is issued by no code path. That is
  deliberate: the hardware promotion criteria are not specified, and an enum
  value nothing writes is more honest than a tier that means nothing.
- The registry lint now has one CAP-6 exception. Exceptions are where rules
  rot, so it re-checks the motion ceiling rather than trusting the register
  path to have enforced it.

## Alternatives considered

- **Lower the floor instead.** Rejected: that reopens #243 exactly. The
  problem was never that 0.5 is too high; it is that certification and
  admission were the same door.
- **Let unregistered ids validate with a warning.** Rejected: `MANIFEST_MISSING`
  is a load-bearing error — it was H1's dominant failure mode, and the fix
  there was to make it *more* legible, not softer. A warning path would let a
  graph launch code the registry never saw, which is what
  `PATH_MANIFEST_MISMATCH` exists to prevent.
- **A separate `sandbox/` registry directory.** Rejected: two registries means
  two lint paths, two search paths, and a promotion step that moves files. The
  tier is a property of an entry, not a location.
- **Expire sandbox entries automatically.** Deferred rather than rejected —
  attractive, since a sandbox entry that outlives its campaign is clutter, but
  expiry needs a clock, and injecting one for this is more machinery than the
  problem currently justifies.
