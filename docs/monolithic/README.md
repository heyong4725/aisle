# Monolithic control surface (SPEC 440)

The equal-capability control arm for the typed-versus-monolithic study
(issues #344, #346, #347). Status: infrastructure built and shaken out;
**expert parity is not passed** — both experts share an author and the
parity seeds were not revealed by a separate operator (MON-9), so the
MON-10 gate stays blocked by construction (see `parity-protocol.json`).

| artifact | role |
|---|---|
| `src/aisle/nodes/monolith_broker.py` | trusted broker node: loads one module, delivers observations, validates actions, publishes into budget-guard (MON-5) |
| `src/aisle/monolith/primitives.py` | frozen primitive API v1.0 re-exporting the typed nodes' own implementations (MON-4) |
| `src/aisle/monolith/confinement.py` | in-process denial of trusted imports / process / socket / files, integrity check of trusted callables (MON-7 record layer; OS confinement is #353) |
| `graphs/monolithic_t1.yaml` + `graphs/turn_plans/monolithic_t1.json` | the frozen monolithic T1 graph: expert_t1.yaml with the four agent nodes replaced by the broker |
| `src/aisle/harness/monolith.py`, `harness monolith ...` | launcher (`run`, `check`, `describe`), MON-1 table render, MON-4 map check, MON-10 parity gate |
| `experts/monolithic/expert_t1.py` | the monolithic expert (same author as the typed expert; see `experts.json`) |
| `treatment-table.json` / `treatment-table.md` | MON-1 treatment-difference table and generated rendering with hashes |
| `interface-map.json` | MON-4 field-by-field map; `harness monolith interface` fails on any mismatch with either graph |
| `allowlist.json` | MON-3 single-file allowlist and the typed editable set |
| `parity-protocol.json` | MON-10 frozen rule, seeds, commands, and the preconditions that are honestly false |
| `primitive-api.md` | the document a monolithic-arm agent receives |

Not built here: the session-level evidence envelope and preflight tuple
(MON-8, MON-12, MON-13 — depend on #345/#353), OS-level confinement
(MON-6, #353), and the protocol freeze (MON-15 — after CON-14 approval of
SPEC 440). ADR-61 records the interpretation choices.
