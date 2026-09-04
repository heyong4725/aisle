# ADR-57 — Compare diagnostic evidence with sealed paired fault sessions

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #349.

## Decision

AISLE will compare `typed_evidence` with `logs_only` over the same running typed
robot system, sealed fault assignment, source/edit surface, repair broker,
budgets, evaluator, and authority. Both arms receive identical conventional
process-log producers, fields, filters and retention plus the same episode-
outcome schema; realized bytes may diverge after agent actions. The treatment
adds the frozen bundle of typed failure taxonomy, topic traces/probes, schemas/
manifests, structured guard statistics, topology validation, and validated graph
operations. The claim is about that bundle, not one feature or typed versus
monolithic runtime.

Each fresh session submits one immutable diagnosis before any repair action.
The co-primary session outcomes are exact component localization and accepted
held-out repair/recovery. Both registered superiority rules must pass for a
supporting result. Fault sessions include multiple families, intermittent and
coupled instances, and novel-code repairs; indistinguishable shams measure false
alarms and unnecessary action. At least two independently supplied agent systems
run opaque paired assignments in concealed temporal blocks.

The sealed bank, assignment and injector ledgers, fault menu, healthy references,
and repair oracle remain outside participant views. Every diagnosis, mutation,
failure, exclusion, held-out result, and transcript is retained. The instrument
audit must cover truth mapping, repair/quality/safety scoring, timers,
exclusions, evidence filters, leakage, statistics, confusion matrices, and
published transforms before collection.

Historical H6 3/3 is motivation only: it used public env faults, one session per
class, healthy references, no logs-only arm, no shams, and source-readable fault
hooks. It is permanently tagged `historical_unblinded` and cannot enter any
power, calibration, or treatment estimate.

## Alternatives rejected

- Compare new sessions with historical H6: time, exposure, faults, and controls
  are not exchangeable.
- Give logs-only fewer repair primitives: that changes capability, not evidence.
- Credit diagnosis after a repair works: repair outcome leaks fault identity.
- Pool fault episodes as replicates: the autonomous session is the treatment
  unit.
- Treat no mutation in a sham as sufficient: an agent may still falsely accuse
  a component, so diagnosis and action false alarms are separate.

## Gate

SPEC 510 is implemented tests-first only after this spec-change and #345/#348/
#353/#354 merge. Human approval creates no fault result. Collection stays locked
until the bank, treatment surfaces, powered allocation, instrument audit,
independent reviews, and externally timestamped freeze pass at immutable hashes;
#349 stays open until released records regenerate every result.
