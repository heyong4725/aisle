# ADR-43 — the budget unit for non-API agents

Status: ACCEPTED 2026-08-18 (issue #282, the gate for the alternative-agent
arm). Extends HAR-5 / ADR-h2 point 3. No existing arm's numbers change.

## Context

`tools/campaign.py` counts spend from the agent CLI's live stream with
new-token semantics: `input + cache_creation + output`, **cache reads
excluded**. That definition was chosen empirically at the dry run — counting
only `input_tokens` read 856 for a 91-message session, counting cache reads
read 5.49M for 18 minutes, and the middle is what makes a 5M budget mean what
the design intended.

It is an **API-pricing-shaped** definition. Two of its three terms
(`cache_creation`, and the exclusion of cache reads) describe a vendor's
caching product, not work performed.

A locally-hosted model has no vendor meter and no cache-read discount. You can
count exactly — strictly better — but the unit stops meaning the same thing.
Every budget-normalized result compares against the API-shaped unit:

- A3: params-only won at **200k vs 396k tokens**
- A4: Claude ~2× cheaper than Codex **at equal quality**
- A5: token super-linearity **+22% / +31%** per agent

A local arm reporting "0.4M tokens" will be read against those, and should
not be.

## Decision

**Two units, recorded side by side, never summed.**

1. **`tokens_new`** — the existing API definition, unchanged. Continues to be
   the ledger's enforcement unit for API agents. Nothing about A3/A4/A5 moves.

2. **`tokens_generated`** — output tokens only, recorded for **every** arm
   including the API ones.

`tokens_generated` becomes the **cross-arm comparison unit**, because it is
the one quantity that means the same thing everywhere: tokens the model
actually produced. It is insensitive to prompt caching, to context re-sending,
and to whether a vendor bills for prefix reuse.

Both are recorded per session. Neither is derived from the other. A comparison
that spans arms of different kinds MUST cite `tokens_generated` and say so.

**Enforcement stays where it is.** The ledger enforces on `tokens_new` for
API agents and on `tokens_generated` for local ones, and the ceiling remains
counted from the live stream by the runner (issue #42: the runner is the sole
authority on spend, never a log the session could rewrite). An agent whose
CLI cannot stream usage cannot be metered mid-session — see the consequence
below, because that case is real.

## Why not the alternatives

- **Redefine everything as `tokens_generated` and restate A3/A4/A5.** Cleanest
  in principle, and rejected: the historical arms' raw records may not carry
  an output-only breakdown for every turn, so the restatement would be partly
  reconstructed. Restating a published number from incomplete evidence is
  worse than carrying two units.
- **Wall-clock or compute-normalized budgets.** Attractive for local models,
  whose real cost is time and hardware. Rejected as the primary unit because
  wall time is host-dependent, and this project has already been bitten once
  by a measurement that silently encoded host speed (the rtf-coupled test in
  #236). Worth recording alongside; not worth trusting as the budget.
- **A conversion factor between units.** Rejected outright. The ratio depends
  on prompt length, cache-hit rate, and turn count — it is not a constant, and
  a fabricated constant would make incomparable numbers look comparable, which
  is the precise failure this ADR exists to prevent.

## Consequences

- No existing result changes, and no existing budget is restated.
- Every future campaign record carries both units. `tokens_generated` for API
  arms is derivable from the same stream already parsed, so this is an
  accounting addition, not new telemetry.
- **A cross-arm claim citing `tokens_new` is invalid** once a local arm
  exists. That is a rule a reviewer can check.
- An agent CLI that reports usage only at completion — a single final JSON
  object rather than a stream — can be *accounted* but not *metered*: the
  ceiling cannot fire mid-session. Such an arm runs under a wall ceiling only
  and MUST be labelled as unmetered in its record, the same way ADR-24 labels
  unattested runs. It may not carry a budget-normalized claim.
- A5's fleet numbers do not transfer to local arms regardless of unit: local
  inference contends with the simulator for the same device, so the
  contention profile differs, not just the accounting.
