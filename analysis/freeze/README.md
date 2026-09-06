# Campaign freeze registry

Content-addressed pre-registrations for the simulation campaigns behind
issues #346 to #352 (SPEC 450, 470, 480, 490, 500, 510). Each directory holds
one `declaration.json` (hypotheses, endpoints, decision rules, exclusions,
instrument set, seed commitment, budgets, integrity gates, artifacts, analysis
scripts, exact commands) and the `freeze-manifest.json` that
`harness freeze build` derived from it: a SHA-256 for every named artifact,
analysis script, and passed-gate record, plus the salted seed commitment.

## Status semantics

`frozen` is reachable only when every integrity gate is `passed` at a retained
record and the manifest carries an explicit timestamp. Every manifest here is
`registered_pending_review`: the gates each spec hands to a human (CON-14
approval, STA-12 independent statistical review, BND-1 candidate amendment)
and the machine gates whose instruments are not built yet are listed under
`pending_gates`. Nothing in this directory authorizes scored collection.

## Seeds are withheld

Held-out seed values live outside every worktree under
`~/aisle-private/freeze/<campaign>/` (BND-13, CSE-9). Only
`sha256(salt || canonical seeds)` is committed. A host without the private
files checks the manifests with `--allow-withheld-seeds`, which reports the
commitment as `unverified` while still checking every other hash.

## Regenerate and check

```bash
for c in analysis/freeze/*/; do
  uv run harness freeze check --manifest "$c/freeze-manifest.json" --allow-withheld-seeds
done
```

Rebuilding a manifest (`harness freeze build --declaration ... --output ...`)
is only legitimate as a new registration version; drift against an existing
manifest is a refusal, not an update. When a trusted artifact legitimately
moves (a graph fix under CON-7 review, a new registry manifest), the affected
registrations are superseded by a new version whose declaration names the old
campaign id in `superseded` and says why; the old directory is retained as a
drifted record and `tests/unit/test_freeze_registry.py` tolerates drift only
for registrations named that way. v3 of the BND, FLT and SFE registrations
supersede v2 after #475 (turn watchdog) and #492 (monolith-broker manifest);
flt-bank-calibration-v4 supersedes v3 after injector v2 (FLT-8 parity pad and probe);
bnd v4, sfe v4 and flt v5 supersede their predecessors after the SPEC 480 live-graph
manifests (semantic-gateway, goal-adversary) entered the registry;
flt-bank-calibration-v6 supersedes v5 with the artifacts of the v2 round that actually ran.

BND v5 supersedes v4 after the Dora 1.0.1 lockfile upgrade. Its unchanged
salted seed commitment is inherited from the byte-bound v4 manifest because
the original private sources are unavailable on the build host. The added
`seed commitment verification` gate stays pending; this is not a verified
seed commitment or authorization to collect scores. Strict checks still
require those sources. See
[the inheritance decision](../../docs/decisions/ADR-seed-commitment-inheritance.md).

To make such a pending successor, set `seed_commitment.inherited_from` and
`artifacts.seed_commitment_predecessor` to the same predecessor manifest path,
name its campaign in `superseded`, preserve its seed rules and source paths,
and include a pending `seed commitment verification` machine gate. The normal
`harness freeze build` command validates and hashes that lineage. It rejects
changed seed rules, a missing verification gate, or restored sources whose
digest disagrees. The predecessor itself is retained unchanged.

## Confirmatory protocols

`cse-causal-study-v1` and `fel-fault-evidence-study-v1` also carry a SPEC 400
`protocol.json`, its `power.json`, and the deliberate
`protocol-freeze-refusal.json`. The power inputs are pre-registration
assumptions (control success 0.40, no pilot has run); the frozen sample size
must be re-derived from pilot-only control success under a new protocol id
before any scored session (CSE-8). At the assumed rates the planned 0.25
risk difference needs 49 randomized sessions per arm per stratum.

## What is NOT here

No campaign outcome, treatment effect, physical result, external review, or
DOI. Building a manifest is a hashing step, not evidence.
