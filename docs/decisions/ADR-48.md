# ADR-48 — Treatment integrity v3 uses an external confinement adapter

Status: PROPOSED — owner review required under CON-14. Date: 2026-08-31.
Issue: #353.

## Decision

Confirmatory sessions will be identified by a complete treatment manifest, not
only commit, model name, and prompt hash. The manifest covers visible files,
served model and CLI/runtime binaries, configuration and tool authority,
environment/backend, isolated state and credentials, prior context, budgets,
randomization block, host load, confinement policy, and retained outputs.
Preflight refuses unresolved identity; postflight rehashes critical state and
classifies drift as infrastructure exclusion.

The current isolated HOME repair closes accidental operator-memory discovery
but deliberately does not stop an agent that knows an absolute path. The v3
boundary therefore requires a campaign-owned external confinement adapter with
a verified policy. Both Claude and Codex run through that adapter with matched
filesystem/process/network/tool authority; their built-in permission modes are
not themselves the scientific boundary. The concrete macOS adapter and profile
will be selected by an unscored capability spike after this contract is
approved. If no adapter can both seal hidden paths and support the required
vendor/runtime access, confirmatory sessions remain blocked rather than falling
back to unrestricted execution.

Hidden seeds, faults, injector ledgers, reference repairs, and same-experiment
findings live outside the visible worktree and its git object namespace.
Session archives retain transcripts, tool logs, deliverables, ideas, manifests,
budget/randomization records, and exclusions while scrubbing credential bytes.
Mutation coverage, not an allowlist assertion alone, is the gate.

## Alternatives rejected

- Prompt-only prohibition: auditable as an instruction, not a read boundary.
- Empty HOME alone: already implemented and explicitly leaves absolute-path,
  process, socket, and git-object channels open.
- Agent-native sandboxes as the sole boundary: the two CLIs expose different
  policy semantics, weakening treatment parity and placing enforcement inside
  the subject being measured.
- Copy-only worktrees without external confinement: hidden files remain
  reachable by absolute path or another repository ref.

## Gate

Implementation follows tests-first after this spec-change merges. The adapter
spike is unscored infrastructure evidence and cannot enter a confirmatory table.
Issue #350 may reuse the verified process boundary for actuation testing, but
must independently define which actuation paths are in scope.
