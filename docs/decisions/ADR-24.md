# ADR-24 — Installed-distribution attestation (issue #38)

Status: DRAFT — decision-ready for the owner. Trigger: PR #34 red-team
follow-up 4/5 (the critical one). Relates to ADR-21 (trusted baseline),
SPEC 060 VAL-2/VAL-3 (INSTALL_MISSING, SOURCE_INVALID, launchability),
SPEC 050 CAP-4 (`--installed` search).

## Problem

Validation verdicts — and therefore the HAR-2 rollout gate, the
INSTALL_MISSING/SOURCE_INVALID error surface, alternative hints, and
`search --installed` — now depend on **installed-distribution state**.
But the attested environment surface (`tools/env_hash.py`, ADR-21's
trusted baseline) fingerprints only the frozen tree. Consequences:

- `uv pip install dora-yolo` flips gate outcomes and validation
  verdicts with **zero trace** in any run record or baseline: a graph
  that was un-launchable yesterday validates today, and no artifact
  says why.
- CON-5 (reproducible from `(graph hash, env hash, seed list)`) is
  silently false for any graph touching a pip-sourced capability: the
  same triple can validate on one machine and refuse on another.
- A research agent that installs a package mid-campaign changes its own
  gate outcomes; the frozen-set audit cannot see it. (`uv sync` drift —
  the sim-extra footgun — is the accidental version of the same hole.)

## Decision (proposed)

Attest the distribution state at three layers, cheapest first. The
probed set is always **the registry-referenced distributions** (every
`pip:` source in installed manifests), not the whole environment —
that keeps the surface small, stable, and meaningful.

1. **D1 — validate reports carry `dist_state`** (VAL-3 shape change,
   spec-change PR): `{"<dist>": "<version>"|null, ...}` for every
   registry-referenced distribution, probed once per validation from
   the same snapshot the checks used. Every INSTALL_MISSING/launchable
   verdict becomes self-explaining and diffable.
2. **D2 — run manifests record the environment lock** (HAR-4
   addition): `uv_lock_sha256` (hash of the repo's `uv.lock`) plus the
   same `dist_state` map. A run record then pins WHAT was installed,
   not merely what the frozen tree looked like — CON-5's triple becomes
   `(graph hash, env hash + dist attestation, seed list)`.
3. **D3 — the trusted baseline gains a dist surface** (ADR-21
   extension): the env-baseline commit's `uv.lock` hash is the trusted
   lock; at gate time the runner compares (a) the local `uv.lock` hash
   against the baseline's and (b) the live `dist_state` against the
   lock's claims for registry-referenced dists. Policy on mismatch:
   - **campaign/agent runs (env-baseline != local): REFUSE**, same as
     frozen-tree drift — an installed dist the baseline doesn't know is
     exactly the "agent installs its way past the gate" hole.
   - **dev runs (env-baseline local): RECORD, don't refuse** — the
     manifest carries the attestation, so a dev result is honest about
     the environment it ran in without blocking iteration.

## What this deliberately does NOT do

- No full-environment freeze: only registry-referenced dists are
  gate-relevant; hashing all of site-packages would churn on every
  unrelated dependency bump.
- No new hash inside `env_hash` itself: the frozen-tree hash keeps its
  meaning (CON-7's set); the dist surface is a SEPARATE recorded field
  so drift attribution stays legible ("code drifted" vs "environment
  drifted").
- No runtime enforcement inside nodes: attestation is a gate/record
  concern; nodes never probe.

## Mechanics (implementation sketch)

- `registry.dist_state(root) -> dict[str, str | None]` — reuses
  `_pip_dist`/`_pip_installed`-adjacent probing, one
  `importlib.metadata` snapshot per call.
- `uv_lock_sha256 = sha256(uv.lock bytes)`; absent lock = recorded as
  null (and REFUSED for campaign runs — a campaign without a lock is
  unattestable).
- Gate wiring in `run_gates` (HAR-2) beside the env-hash check;
  refusal code `DIST_DRIFT` with the differing dists named.
- Spec changes: VAL-3 (report field), SPEC 070 HAR-4 (manifest fields)
  + HAR-2 (gate), via one spec-change PR after this ADR is accepted.
- The campaign runners' treatment records gain the same two fields, so
  arm comparability includes the dist surface (the H2/H3 records to
  date predate this and are not retroactively annotated).

## Consequences

- An env-change PR that installs a hub distribution (the exact act the
  INSTALL_MISSING hint invites) becomes visible end-to-end: new
  `uv.lock` hash in the baseline, new `dist_state` in every subsequent
  report and manifest.
- Cross-machine reproduction failures become diagnosable from the run
  record alone (compare `dist_state`).
- Cost: one metadata scan per validation (cached), two fields per
  manifest; no sim-time cost.

## Open question for the owner

Whether D3's campaign-mismatch policy should refuse on ANY
registry-referenced dist difference (proposed — strict, matches the
frozen-set posture) or only on differences that change a verdict for
the graph under validation (laxer; harder to reason about, weaker
audit). The proposal is the strict form.

IDs: CON-5, CON-7 (posture parity), ADR-21 (baseline surface), VAL-2/3,
HAR-2/4, CAP-4; CON-14 (implementation via spec-change PR).
