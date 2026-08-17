# ADR-39 — the determinism layer for learned-policy inference

Status: ACCEPTED 2026-08-17 (issue #268). Extends the CON-5 determinism
contract with the layer the SmolVLA bring-up made live. Continues ADR-24
(attestation), ADR-26 (physics outcomes are statistical), ADR-38 (chunk
preemption).

## Context

The determinism contract layers nondeterminism sources and states what each
promises. Orchestration is one layer; physics is another, where ADR-26 already
concedes that Metal cannot promise bit-identical long-horizon outcomes and
defines full-episode results as statistical.

A learned policy adds a third source that no layer named: sampling, batching,
and non-deterministic kernels. Until #263 this was hypothetical. It is not now.

Investigating it turned up two concrete defects rather than one abstract gap.

**1. ADR-38's staleness floor was unreachable.** The node called
`queue.offer(chunk, obs_ns, obs_ns)`, so `now_ns - obs_ns` was identically
zero and rule 4 could never drop a chunk. The queue's own unit test passes
explicit stamps and is green, so nothing failed — the rule was correct and
uninvoked, the pattern this project keeps rediscovering (report appendix F6).

Under ADR-30 lockstep with synchronous inference the zero delta is factually
right: no sim time elapses between observing and emitting inside one turn. That
is exactly why it was invisible, and exactly why it mattered — the moment
observation and emission are separated, which is the reason action chunks
exist, the protection silently would not have been there.

**2. Inference was unseeded.** `select_chunk` ran under `torch.no_grad()` with
no seed. A policy of this class may sample during action selection, so the same
graph, seed and environment could produce different trajectories — the single
thing CON-5 forbids, arriving through a source the contract did not cover.

## Decision

**The inference layer promises reproducibility on one device, from sim-time
inputs only.** Concretely:

1. **Every staleness decision is a function of SIM time.** `obs_ns` is the
   stamp of the frame the chunk was computed from; `now_ns` is the current
   turn's stamp. Deriving either from wall time is forbidden: a loaded host
   would drop a different set of chunks and execute a different trajectory with
   every recorded seed identical — the wall-versus-sim coupling class that has
   already produced one flaky test in this project.
2. **The sampler is seeded, and seeded from a reproducible value** — the
   observation's sim stamp. Not wall time, not a global counter, not absent.
3. **Weights are pinned by revision hash** in the manifest and carried into the
   attestation (ADR-38, extended here to mean the pin must be a commit hash
   before any measured claim, not a moving ref).
4. **What is NOT promised: bit-identical inference across devices.** MPS
   reductions are not reproducible; this is why VER-5 pins all *verifier* model
   inference to CPU, with the stated reason that a verdict source must not
   flicker across replays. A policy is not a verdict source, so it is not
   pinned to CPU — but a replay claim is therefore scoped to the device that
   produced it, and the device belongs in the run record.

## Consequences

- A zero-shot baseline is replayable on its own host, which is the condition
  for it to anchor the fine-tuning comparison it exists to anchor. Without this
  the baseline could have failed to replay before it was ever used.
- Rule 4 is now live. Under synchronous lockstep inference it still never
  fires, because the delta is genuinely zero — the difference is that it *can*.
- Seeding removes the sampler as a divergence source; it does not make two
  devices agree, and this ADR says so rather than implying otherwise.
- A source-level test guards the call site rather than only the rule. That is
  crude, and it is the specific defect that existed: the unit test of the rule
  was green throughout.

## Alternatives considered

- **Pin policy inference to CPU, as VER-5 does for the verifier.** Rejected for
  now: the verifier's argument is that a *judge* must not flicker, which does
  not transfer to a policy — and a 450M model on CPU changes the latency
  profile that chunking exists to manage. Revisit if cross-device replay
  becomes a requirement rather than a convenience.
- **Derive staleness from wall time**, which is what "a slow inference must not
  act on a stale world" literally suggests. Rejected: it would import
  host-load-dependence into the executed trajectory and break CON-5 in the act
  of enforcing ADR-38. The barrier already provides wall-clock protection via
  the watchdog; the staleness floor is the sim-time half.
- **Leave inference unseeded and declare outcomes statistical, as ADR-26 does
  for physics.** Rejected: physics nondeterminism is a property of the
  simulator we cannot remove, whereas sampler nondeterminism is one line to
  fix. A contract should concede only what it must.
