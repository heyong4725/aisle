# ADR-44 — the local-agent arm: pre-registered expectations (issue #285 C3/C4)

Status: ACCEPTED (CON-15 — expectations registered BEFORE any local
session runs). Follows ADR-43 (#282, the two-unit ledger) and the
ADR-38 bring-up pattern: the deliverable is the INTEGRATION plus an
honest baseline, not a working agent.

## Why a weaker agent is measurement, not economy

H4 asks whether the typed substrate beats script-level iteration.
Frontier agents are a poor probe: they succeed either way, and the
substrate's contribution hides inside their competence. A3 measured
the effect from the other side (params-only matched full authorship at
half the tokens — schema-as-subsidy). A capability floor tests how far
the subsidy extends: **if a ~27B-class local model can compose valid
typed graphs while being unable to write a working monolithic script,
that is H4 evidence frontier agents structurally cannot produce.**

## The arm

- Backend: ollama on this machine; model pinned by name AND digest in
  every record (`local_backend`, `local_model_digest` — adapter
  treatment extras, tools/agent_adapters.py). First candidate was
  gemma3:27b; SUBSTITUTED before any scored run (amendment, measured
  reason): the runtime rejects tool calls for it ("does not support
  tools", HTTP 400) and the driver's loop is tool-call-shaped. The arm
  runs **qwen3:30b** (digest recorded per run) — a 30B-class
  mixture-of-experts, tools-capable, temperature 0. Same class, same
  floor expectation.
- Driver: `tools/local_agent.py` — one bash tool, claude-shaped
  stream-json so the campaign tee/ceiling path is unchanged; the
  `local` adapter's ledger enforces on `tokens_generated` (ADR-43;
  `tokens_new` is never reported for this arm).
- No credential seam; the adapter records that explicitly.

## Pre-registered expectations (the floor, stated before running)

- **Composition (H1-protocol, T1, n=5 attempts, seeds 0..7 scoring):**
  frontier agents measured 15% (Claude) / 65% (Codex) zero-shot
  launchable. EXPECT the local arm BELOW 15% launchable — plausibly
  0/5 — and expect schema-valid well above launchable, mirroring H1's
  shape. A 0/5 baseline is the floor being measured, not a failure.
- **Session mechanics:** expect tool-call format errors and max-turn
  exhaustion as the dominant terminal modes; both are recorded classes,
  not infra.

## C4 — the replay measurement (the genuinely new capability)

CON-5's thesis is same seed ⇒ same result; the agent has always been
the irreducibly nondeterministic component. Local inference exposes a
sampler seed. Protocol: run ONE composition session twice at the same
seed, same prompt, same pinned model digest, temperature 0, fresh
workspaces. Compare (a) the event streams, (b) the deliverable graph
bytes. REPORT the outcome either way:
- Bit-identical → the project's first replayable outer loop; record
  the conditions (single-request concurrency; Metal batching caveats).
- Divergent → record WHERE (first differing turn) and the mechanism if
  traceable; a divergent replay under a pinned seed is a finding about
  the runtime, not a protocol failure.

No pass/fail threshold: both baselines are reporting-only.
UNATTESTED dev measurements (ADR-24).
