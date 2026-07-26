# H1 findings — composition experiment (design doc §8.2.4, hypothesis §6 H1)

Protocol: `tools/h1_protocol.py` per ADR-h1-protocol. Both arms N=20
fresh sessions, treatment pinned at commit `abd2e9d3`, task T1,
zero-shot = first-validate-consumed graph is valid AND its 8-seed T1
rollout produces >=1 episode. Arms: `claude` = claude-fable-5 (Claude
Code CLI), `codex` = gpt-5.6-sol (codex CLI; the model the account
serves — see Limitations). Full per-attempt table: `h1_table.md`;
raw summaries: `h1_results_claude.json`, `h1_results_codex.json`
(copies; runs/ is gitignored).

## Headline

| | claude | codex |
|---|---|---|
| Zero-shot valid (schema) | **20/20** | **20/20** |
| Zero-shot valid AND launching | 3/20 (15%) | 13/20 (65%) |
| H1 target (>=80%) | not met | not met |
| Mean validate calls | 1.0 | 2.0 |
| Pass@1 of launched graphs | 0.75–0.875 | 0.75–1.0 (median 0.875) |
| Workspace violations / timeouts / infra errors in results | 0 / 0 / 0 | 0 / 0 / 0 |

## The single dominant mechanism

Every attempt in both arms composed a schema-valid graph on the first
try (40/40). Every zero-shot miss in both arms — 17 claude, 7 codex —
is the SAME failure: the agent composed the
`detector-openvocab` + `pose-estimator` perception stack, whose hub
manifests declare `source: pip:dora-yolo` / `pip:dora-pose` — packages
not installed in the frozen environment. The graph validates (the
validator checks typed wiring, not node installability) and the
dataflow exits with zero episodes. Perfectly bimodal, 40/40 attempts:

- `oracle-pose` composition → launched, every time (16/16), pass@1
  0.75–1.0, failures only in the known `collision`/`dropped` classes.
- detector-stack composition → `no_episodes`, every time (24/24).

The entire 50-point gap between the arms is the perception CHOICE
(claude picked the detector stack 17/20; codex 7/20), not wiring
skill. Under the protocol the agent may not roll out, so it has no
signal to discover the gap; nothing in the registry marks those nodes
as unavailable, and T1's contract permits the L1 oracle rung.

## Interpretation against H1

H1's composition claim splits in two:

1. **Typed composition works** — 100% first-validate validity across
   both arms and both perception strategies. The validator-as-compile-
   loop mechanism did its job; no agent ever wired nonsense.
2. **"Valid and launching" failed the 80% bar in both arms** because
   launching is partly a REGISTRY-HONESTY property, not an agent
   property: the registry advertised capabilities the environment
   cannot run. This is the experiment working as designed — the
   registry's deliberate maturity gaps surfaced immediately as the
   dominant composition risk.

## Composition-failure taxonomy (Phase 1 DoD)

| Class | Seen | Count | Note |
|---|---|---|---|
| `uninstalled-capability` | both arms | 24/40 | valid graph, node source `pip:` pkg absent; validator blind to it |
| schema mismatch | never | 0 | |
| missing producer / dangling edge | never | 0 | |
| oracle-isolation violation | never | 0 | |
| motion-gate bypass | never | 0 | |
| workspace violation (files beyond graph) | never | 0 | |

Actionable follow-up (post-experiment; MUST NOT land while any arm at
this treatment might re-run): a validator installability check — an
`INSTALL_MISSING` error (or warning + manifest `available: false`)
when a node's `source: pip:` package is not importable in the frozen
env. That single check converts 24/40 zero-shot misses into actionable
compile-loop errors, which per mechanism above would have raised both
arms' zero-shot rate to their valid rate (100%) or forced an informed
perception choice.

## Limitations

- Model asymmetry: the arms compare *vendor-served* frontier CLIs
  (claude-fable-5 vs gpt-5.6-sol), not matched checkpoints; the
  account rejects explicit legacy codex model pins (400), so the
  served model is pinned and recorded instead.
- Mean validate calls differ structurally (codex's CLI pattern issued
  a validate after writing the final graph; claude validated once) —
  cycles are comparable within-arm, not across.
- Single scene family (T1), single embodiment (franka), oracle
  verifier only.

## Protocol integrity

No workspace violations, no session timeouts, no runner errors in the
recorded results. Three codex-path infra defects were found and fixed
BEFORE any recorded codex attempt (stdin inheritance under detached
launch; dead model pin; native-sandbox blocking the shim's snapshot
write — all attributed as InfraError by design, records discarded, arm
re-run from attempt 0 at the same pinned treatment).
