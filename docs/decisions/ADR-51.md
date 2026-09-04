# ADR-51 — Hidden faults live in a sealed external bank

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #348.

## Decision

AISLE will keep production fault definitions, assignment seeds, injector
ledgers, healthy references, and accepted-repair oracles outside the repository,
its git object namespace, and every participant-visible filesystem/tool surface.
The public repository will contain only the bank schema, a generic
content-addressed injector, synthetic canary fixtures, and commitment/reveal/
replay tooling. The external confinement boundary from issue #353 is a hard
dependency for scored use.

The controller selects cells from a sealed version using a hidden seed and
publishes commitments before collection. It applies a sham, single-fault, or
atomic coupled transaction to an evaluator-owned staging tree and records exact
pre/post hashes without placing identities in argv, environment, or ordinary
logs. Fault and sham sessions share the same start-of-session surfaces. The bank
spans six system families, persistent/intermittent modes, calibrated severity
ladders, coupled cases, no-fault controls, and novel repairs.

Calibration is excluded pilot evidence: all severities and ineffective results
are retained, and only the pre-declared selection rule may choose the sealed
level. Positive target/operator allowlists and postimage checks prevent a fault
from changing the scorer, guard, reset, limits, authorization, environment
truth, evidence sink, controller, injector, or drivers. Static and dynamic
safety oracles reject semantic hazards before scoring.

After campaign closure and outcome freeze, reveal verifies the original
commitments and maps opaque assignments to the bank. Replay must reconstruct
every postimage and activation receipt and rerun frozen safety/efficacy checks.
The revealed bank may be released for audit, but all exposed instances and
fingerprints are retired from future hidden versions.

## Alternatives rejected

- Extend the tracked H6 hook menu: source, trigger, target, severity, and healthy
  reference are available to the participant and cannot support blinding.
- Encrypt a bank beside the worktree while giving the session host the key: key,
  path, process, descriptor, or cache discovery becomes another participant tool
  surface; evaluator-private material remains outside the confinement boundary.
- Redact ordinary logs after the session: the participant may already have seen
  the leak, and selective deletion breaks auditability.
- Treat a renamed public fault as rotated: fingerprints and restoration paths,
  not labels alone, create benchmark leakage.

## Gate

SPEC 450 is implemented tests-first after this spec-change merges. Synthetic
fixtures can validate the public machinery, but the production bank must be
created and sealed by an authorized evaluator outside the public repository.
No pilot or scoring begins until issue #353 confinement, leak conformance,
safety/efficacy calibration, sham parity, commitment, and exact replay tests all
pass. Historical H6 records remain explicitly unblinded.
