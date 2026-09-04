# ADR-52 — One frozen `actuation-gateway` owns driver authority

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #350.

## Decision

The mechanically testable agent-to-actuation boundary is named
`actuation-gateway`. It is an evaluator-owned process outside participant
deliverables and is the only component holding an authenticated simulator or
device-driver capability. Typed dataflow and monolithic broker requests converge
on the same gateway handler. The gateway reruns the pinned guard/limit policy;
it does not trust graph topology, a `safe` topic name, a manifest, or a claimed
guard identity.

The driver accepts only the current authenticated gateway epoch. Participant
processes, dora/coordinator nodes, imports, sockets, dynamic swaps, tools, and
children cannot access or inherit the capability. A driver-side lease/watchdog
holds or stops on gateway crash, timeout, disconnect, or starvation, and the
controller-owned audit stream must reconcile each request, decision, and driver
receipt.

A machine-readable threat model gives the participant all authority actually
available through both campaign confinement profiles, including arbitrary code,
dynamic nodes, malformed messages, allowed network/tools, and deliberate
process failure. Synthetic fake-driver conformance exercises every in-scope
bypass without making a hardware claim. Kernel/host compromise, administrator
malice, firmware compromise, and physical tampering are explicit assumptions,
not silently implied protections.

## Claim boundary

AISLE will remove `unbypassable` from project and paper claims. The supported
wording, only after conformance passes, is: under the reviewed v1 participant
threat model and tested confinement profiles, only the frozen
`actuation-gateway` holds driver authority and tested missing/malformed traffic
fails closed. Semantic correctness of an authorized target is excluded and
belongs to issue #352. Device watchdog and physical stop behavior remain
hardware-pending.

## Alternatives rejected

- VAL-5 alone: it proves paths in a declared graph, not process, import, socket,
  coordinator, driver, credential, or dynamic-node isolation.
- Trust the in-graph budget-guard identity: participant code can spoof names or
  seek alternate bridge paths unless the receiver authenticates authority.
- Put driver credentials in the participant sandbox: read denial then becomes
  the only safety mechanism and differs across agent paths.
- Treat any crash as fail-closed: a driver may continue a held command unless an
  independent lease/watchdog expires and its receipt is measured.

## Gate

SPEC 460 is implemented tests-first after this spec-change and issue #353's
confinement contract merge. Human ratification reviews both the protected
property and all exclusions. No scoped actuation claim is allowed until the same
attack catalog passes on Claude and Codex profiles with complete audit/receipt
evidence; physical behavior remains open until real hardware execution.
