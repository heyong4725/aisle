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
flt-bank-calibration-v6 supersedes v5 with the artifacts of the v2 round that actually ran;
bnd v6 supersedes v5 after the semantic-gateway manifest gained its sensor-arm inputs.

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

BND v7 supersedes v6 after the perception-auditor integrity fix (#346). It
preserves the inherited seed commitment and thresholds, retains the old report
as historical evidence, and resets the BND-5 audit gate to pending. A fresh
audit with the hardened implementation is required; v7 is not a protocol freeze.

BND v8 supersedes v7 after finite-xyz and complete-oracle-geometry checks were
added to the scorer. Thresholds, seed lineage, and pending gates are preserved;
the previous declarations and audit records remain historical.
The v8 artifact set also binds the perception CLI and model lock; its report
requires the digest of the verified identity-model lock entry.

CSE v2 supersedes v1 after declared deliverable edits were separated from
treatment drift. It binds preflight and postflight code, retains the original
protocol and seed commitment, and keeps all review, parity, task-band, and
confinement gates pending. Seed verification is also pending; this is not
a study freeze or authorization to collect results.

CSE v5 supersedes v4 for the matched engineering controller, worker preparation,
and retained evidence paths (#519). Its artifact set binds the controller
sources, runtime selection, dependency lock, treatment table, and documentation.
The v4 manifest remains unchanged. V5 inherits its seed commitment and keeps
all review, parity, confinement, and seed-verification gates pending; it does
not authorize study collection or claim the matched controller is complete.

BND v9 supersedes v8 because the shared CLI gained explicit monolithic worker
configuration options for #519. Calibration rules, thresholds, seed commitment,
and pending review/audit gates are unchanged. V8 remains historical evidence.

CSE v6 supersedes v5 after the matched-run providers were corrected to bind the
direct Python framework interpreter under the existing worker executable policy.
V5 remains unchanged. V6 preserves its seed commitment, collection rules and
pending gates; it records implementation drift without authorizing collection.

CSE v7 supersedes v6 for simulator render-call accounting and explicitly binds
the trusted bridge source. V6 remains unchanged. V7 preserves its seed
commitment, collection rules and pending gates; recorded camera work does not
establish total simulator coverage or authorize study collection.

CSE v8 supersedes v7 for internal physics-advance accounting, including the
compilation advance within scene build. V7 remains unchanged. V8 preserves the
seed commitment, collection rules and pending gates; operation-journal coverage
does not establish complete simulator resources or authorize collection.

CSE v9 supersedes v8 for the paired simulation session documentation. V8 remains
unchanged. V9 preserves the seed commitment, collection rules and pending gates;
the engineering tests do not establish complete session admission or authorize
study collection.

CSE v10 supersedes v9 for bounded run-evidence collection and binds the collector
source. V9 remains unchanged. V10 preserves the seed commitment, collection rules
and pending gates; timeout handling does not establish complete resource coverage
or authorize study collection.

CSE v11 supersedes v10 for the v4 Unix-socket capability matrix in the
controller audit and actual Python-only worker profiles (#350). It preserves
the inherited seed commitment, collection rules and all seven pending gates.
The additional socket controls are synthetic engineering evidence; the
registration does not authorize scored collection or attest complete IPC or
independent confinement.

CSE v12 supersedes v11 for authenticated Codex App Server harness requests,
controller-owned responses, and their retained source-to-attempt evidence
(#536; MON-8/MON-12/MON-13). It binds the new adapter and audit sources while
preserving the inherited seed commitment, collection rules and all seven pending
gates. V11 remains unchanged. Engineering fixtures do not establish complete
frontend coverage, independent confinement, or authorization for study collection.

CSE v13 supersedes v12 for durable App Server harness-call reservations and
source-to-attempt reservation auditing (#536; MON-8/MON-12/MON-13). It adds the
dispatch authority and auditor to the controller fingerprint. V12 remains
unchanged; the seed commitment, collection rules and all seven pending gates
are preserved. Covered-call reservations do not establish full frontend coverage
or authorize study collection.

CSE v14 supersedes v13 for the owned Code Mode host and nested dispatch audit
(#536). BND v10 supersedes v9 because the shared lock now includes the RPC runtime.
Both retain the previous seed commitments through byte-bound predecessor
manifests and preserve all pending gates. These remain registrations pending
review, not study freezes or authorization to collect results.

CSE v15 supersedes v14 for the owned Responses provider relay, per-item native
reservations, and provider-to-frontend/controller source auditing (#536). It
binds the complete controller source set and provider documentation while
preserving the inherited seed commitment, collection rules and all seven pending
gates. BND v10 remains current because its declared inputs did not change.
Engineering route tests do not establish complete frontend conformance,
independent confinement, or permission to collect study results.
