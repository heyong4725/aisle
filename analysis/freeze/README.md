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
manifest is a refusal, not an update.

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
