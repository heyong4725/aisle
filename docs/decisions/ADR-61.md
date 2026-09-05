# ADR-61 — The monolithic control surface is an in-process broker over the typed nodes' own primitives

Status: PROPOSED — owner review required under CON-14 (SPEC 440 is itself
unapproved). Date: 2026-09-05. Issues: #344, #346, #347.

## Context

SPEC 440 asks for an equal-capability monolithic arm: one ordinary module
against a frozen primitive API, with robot capability, trusted evaluation,
authority, budgets and evidence held identical to the typed arm. Several
readings were open: whether the module runs in its own process behind an
RPC broker or in-process; how the module is named and attested; how far
in-process confinement should go before issue #353's OS policy exists; and
whether a same-author expert pair may be used at all.

## Decision

1. **In-process broker.** `monolith-broker` is one lockstep dora node that
   replaces the four agent-editable nodes of `expert_t1.yaml`. It executes
   the module in its own process space with restricted builtins and hands
   it a `Primitives` object whose methods delegate to the very function
   objects the typed nodes call (`L1Session`, `plan_grasp`, `StagedPlan`,
   `StageStreamer`). An RPC boundary would have forced a second
   implementation of the primitives' argument marshalling and given the two
   arms a different failure surface; the same-object guarantee is what
   MON-4 asks for and is pinned by test.
2. **Graph-attested module path.** `AISLE_MONOLITH_MODULE` lives in the
   graph env of the broker node, so the authored graph hash attests which
   module ran; the launcher stamps a copy under `graphs/out/` and the
   rollout runner records the executed copy's hash. A relative value is
   anchored at the repository root because dora runs the graph copy from
   the run directory.
3. **Interface exactness is derived, not declared twice.** `harness monolith
   interface` computes the semantic edges crossing the typed agent region
   from `expert_t1.yaml` and the broker's wiring from `monolithic_t1.yaml`
   and fails if `interface-map.json` differs from either. The 1 Hz tick
   both arms derive from the lockstep clock is the semantic field; the
   clock itself is a MON-1 transport row. The T1 broker is therefore wired
   without `gripper_state` and `rgb_overhead`, which the typed T1 region
   does not consume either; the broker supports them for other tiers.
4. **In-process confinement is a record layer, not a sandbox.** The module
   runs with a strict `__import__` (denying trusted AISLE assets, the dora
   runtime, YAML, process/socket/network/FFI/loader/filesystem modules), no
   `open`, and an integrity check of trusted callables after every
   callback. A refusal writes `monolith_invalid.json` beside the run's
   results and stops the node before the next command. Known residual
   routes — numpy's own file I/O, a literal type walk to `builtins` — are
   exactly what issue #353's OS policy must close; MON-6/MON-7 are not
   claimed complete.
5. **Same-author experts are shakeout-only.** `experts.json` records that
   both experts share an author and `parity-protocol.json` marks the MON-9
   preconditions false, so `harness monolith parity` reports `blocked`
   even on identical outcomes. Runs are tagged `expert_parity` (MON-11) and
   never pooled.

## Consequences

The infrastructure needed by #346 (candidate round), #347 (confirmatory
sessions) and BMK-4/5 exists and is exercised end to end in simulation.
The parity gate cannot pass until an independent author writes one arm's
expert and a separate operator reveals the seeds; the session-level
envelope, preflight tuple and freeze (MON-8, MON-12, MON-13, MON-15) wait
on #345/#353 and CON-14 approval. `docs/monolithic/treatment-table.md` is
generated with hashes and checked in CI, so any edit to a listed surface
must regenerate it.
