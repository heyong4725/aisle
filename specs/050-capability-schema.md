# SPEC 050 — Capability manifest schema and registry

Status: DRAFT. Files: `registry/schema/capability.schema.json`, `registry/manifests/*.yaml`.

- CAP-1: Manifest fields (all required unless noted): id, kind(node|subgraph), provides[], requires[], inputs{name:{schema, rate_hz}}, outputs{name:{schema, latency_class}}, params{name:{type,default,range?}} (optional), embodiment{arm[],gripper}, safety_class(perception|decision|motion), eval{suite,pass_rate,last_run}|null, origin(hub|agent-authored), source(path or pip ref).
- CAP-2: `schema` values come from a closed vocabulary in `registry/schema/schemas.toml` mapping name → Arrow type + shape (e.g. pose7d_f32 → Float32[7]). Adding a schema name is a Class C change.
- CAP-3: JSON Schema validation: `harness/registry.py lint` validates every manifest; CI runs it (marker unit).
- CAP-4: `harness/registry.py search --provides grasp_planning [--embodiment franka]` returns matching manifests as JSON (CON-8).
- CAP-5: Initial registry: 12 manifests — camera-source, oracle-pose, detector-openvocab, ocr-label, pose-estimator, grasp-planner-topdown, ik-trajectory, arm-driver-sim, gripper-driver-sim, task-state-machine, verifier-oracle, reset. Deliberate gap: NO rearrangement skill (design doc §3).
- CAP-6: `eval` may be null only while origin=hub AND safety_class!=motion... exception: the two sim drivers ship with M0 evalcards generated from TC-A1..A3 runs.

Acceptance: `tests/unit/test_manifests.py::test_all_lint` (CAP-1..3), `::test_search_cli_json` (CAP-4), `::test_registry_completeness` (CAP-5 — exactly the 12 ids, and asserts the gap).
