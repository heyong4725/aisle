# SPEC 060 — Dataflow validator

Status: DRAFT. Module: `src/aisle/harness/validate.py`. CLI: `harness validate <graph.yaml>` (CON-8).
The most leveraged component: its error messages are the research agent's learning signal.

- VAL-1: Loads a dora dataflow YAML + all manifests; resolves every node id to a manifest (unresolved id = error MANIFEST_MISSING).
- VAL-2: Checks, each with a stable error `code`: NODE_ID_DUPLICATE, INPUT_NO_PRODUCER, SCHEMA_MISMATCH, RATE_INCOMPATIBLE (warning), EMBODIMENT_MISMATCH, ORACLE_LEAK, MOTION_UNGATED, EVAL_MISSING_FOR_MOTION.
- VAL-3: Output JSON: `{ok, errors:[{code, edge|node, detail..., hint}], warnings:[...]}`. Every error MUST include a `hint` naming a registry capability or concrete fix. Exit 0 iff ok.
- VAL-4: SCHEMA_MISMATCH uses the CAP-2 vocabulary; unknown schema names are their own error (SCHEMA_UNKNOWN), never silently passed.
- VAL-5: MOTION_UNGATED: every path terminating in a bridge `joint_cmd`/`gripper_cmd` input MUST traverse the budget-guard node (SPEC 080). The validator REWRITES nothing; it only rejects (rewiring is the composer's job).
- VAL-6: ORACLE_LEAK: `oracle_state` may only be consumed by nodes whose manifest id starts `verifier-`. 
- VAL-7: Golden-corpus tests: `tests/fixtures/graphs/bad/*.yaml` — ≥20 deliberately broken graphs, one per failure mode incl. the expert_t0 node-id typo from the design doc; `tests/fixtures/graphs/good/*.yaml` — ≥3 valid graphs incl. `graphs/expert_t0.yaml`. Every agent-discovered failure class MUST be added to the corpus in the same PR that handles it.

Acceptance: `tests/unit/test_validator.py::test_bad_corpus_all_rejected_with_expected_codes` (VAL-1..6), `::test_good_corpus_passes`, `::test_hints_nonempty` (VAL-3). All unit-marked: the validator MUST NOT import genesis or dora runtime.
