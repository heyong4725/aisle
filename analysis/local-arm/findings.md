# Local-arm baseline + replay findings (issue #285 C3/C4; ADR-44)

Runner: `tools/local_baseline.py`. Model: qwen3:30b @ ad815644918f
(ollama; the ADR-44 substitution from gemma3:27b, recorded before any
scored run — the runtime rejects tool calls for gemma3). Prompt: the
EXACT H1 task prompt (sha-pinned in manifest.json). Fresh pinned
worktree per session; the ADR-43 ledger records `tokens_generated`
(no `tokens_new` is reported for this arm, by design). UNATTESTED dev
measurement.

## C3 — the capability floor: 0/5, as pre-registered

| attempt | turns | tokens_generated | graph written | valid |
|---|---|---|---|---|
| 0 | 32 | 29,154 | yes | **no** (GRAPH_INVALID: not even a `nodes:` mapping) |
| 1–4 | 5 each | 5,725 each | **no** | no |

Pre-registered expectation: below H1's frontier 15% launchable,
plausibly 0/5. Measured: **0/5 valid — and 0/5 schema-valid**, which
is BELOW the H1 shape (frontier agents were 40/40 schema-valid; their
cliff was launchability). The floor's mechanisms are legible in the
transcripts:

- **Tool-use grounding failure (attempts 1–4):** the model ANSWERED IN
  CHAT with a yaml block instead of using its bash tool to write the
  file — then stopped, having done nothing to the workspace. The
  described graph also hallucinated a node id absent from the registry
  and an `edges:` key the schema does not have.
- **Structural schema failure (attempt 0):** 32 turns of genuine tool
  use produced a file the validator rejects at the outermost level.

Reading for H4: at this capability level the typed substrate's subsidy
cannot land, because the agent cannot reliably OPERATE the tools that
deliver it. The H4-decisive stratum — an agent that can compose
against the schema but cannot write a working script — sits somewhere
ABOVE this floor and below the frontier; this baseline brackets it
from below, which is what a floor is for.

## C4 — the replay: bit-identical, first time in the project

Two sessions, same seed (7), same prompt, same model digest,
temperature 0, DIFFERENT fresh worktrees:

- **Event streams: bit-identical** (`streams_identical: true`, no
  divergent line). The project's outer loop has been replayed for the
  first time — the agent, always the irreducibly nondeterministic
  component, ran twice and produced the same session.
- Deliverables: n/a (neither replay wrote a graph — consistent with
  the floor; the identical streams ARE the deliverable comparison).
- Mechanism honestly stated: at temperature 0 ollama decodes
  greedily, so determinism holds with or without the seed pin (the
  unseeded attempts 1–4 were ALSO token-identical to each other).
  The identity additionally required tool outputs that happened not
  to embed workspace paths in these short trajectories; attempt 0's
  long trajectory shows workspace-dependent divergence remains
  possible for sessions that touch path-bearing output. Replayability
  is therefore CONDITIONAL — greedy decoding + path-clean tool
  traffic — and those conditions are now measured, not assumed.

## Ledger note (ADR-43 exercised end to end)

This is the first arm whose enforcement unit is `tokens_generated`;
the adapter refused nothing silently (no credential seam, recorded),
and the same stream shape drove the campaign tee path unchanged.
