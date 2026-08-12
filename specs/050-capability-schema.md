# SPEC 050 — Capability manifest schema and registry

Status: DRAFT. Files: `registry/schema/capability.schema.json`, `registry/manifests/*.yaml`.

- CAP-1: Manifest fields (all required unless noted): id, kind(node|subgraph), provides[], requires[], inputs{name:{schema, rate_hz, is_clock?}}, outputs{name:{schema, latency_class}}, params{name:{type,default,range?}} (optional), embodiment{arm[],gripper}, safety_class(perception|decision|motion), eval{suite,pass_rate,last_run}|null, origin(hub|agent-authored), source(path or pip ref). `is_clock` is boolean and defaults false. `is_clock: true` declares a simulated-turn watermark input (ADR-30), not an ordinary state sample: the graph MUST wire it with a positive explicit `queue_size` and `queue_policy: backpressure`; the source MUST be the bridge simulation clock or a validated clock participant; and the node MUST preserve the turn stamp when it closes its causal work. A latest-wins/drop-oldest state topic MAY drive a control cadence but MUST NOT serve as the structural turn barrier. Each watermark's parallel metadata lists `closed_outputs: list[str]` and `emitted_counts: list[int]` MUST have equal length, lexical port order, unique output names, and nonnegative counts; a participant closes only after receiving every declared same-turn message from every causal upstream.
- CAP-2: `schema` values come from a closed vocabulary in `registry/schema/schemas.toml` mapping name → Arrow type + shape (e.g. pose7d_f32 → Float32[7]). `sim_turn_u64` is UInt64[1]: its value is ADR-30's globally monotonic `turn_id`, while metadata carries the turn's `sim_time_ns`. Adding a schema name is a Class C change.
- CAP-3: JSON Schema validation: `harness/registry.py lint` validates every manifest; CI runs it (marker unit).
- CAP-4: `harness/registry.py search --provides grasp_planning [--embodiment franka] [--installed]` returns matching manifests as JSON (CON-8). Every match carries `launchable` — pip sources installed, path sources a regular file contained by the root (issue #39: search advertised uninstalled `pip:` nodes with no flag, the H1 discovery-surface gap; `analysis/h1/h1_findings.md`) — and `--installed` narrows to launchable matches; the validator's MANIFEST_MISSING hint recommends it. Graph-context checks (arm/base/evalcard) remain the validator's (VAL-2).
- CAP-5: The CURATED core registry is pinned exactly: the 12 initial manifests
  — camera-source, oracle-pose, detector-openvocab, ocr-label, pose-estimator,
  grasp-planner-topdown, ik-trajectory, arm-driver-sim, gripper-driver-sim,
  task-state-machine, verifier-oracle, reset — plus additions made by
  spec-change (SPEC 200 §11.4 retail set; future family sets). The curated id
  list is single-sourced in `registry/schema/curated_core.toml` (Class C).
  Beyond the curated core, the registry admits ONLY registered skills
  (design doc §8.4, enforced per CAP-7). Deliberate gap: no CURATED
  capability provides a rearrangement skill (design doc §3); an
  agent-authored rearrangement skill is the intended fill.
- CAP-7: Registration governance (T18): `harness skill register` MUST refuse
  any id on the curated list regardless of current registry file state
  (deleting a core manifest opens nothing), and every non-curated manifest
  MUST be origin=agent-authored with a non-null evalcard — the registration
  path is the only way past the curated core.
- CAP-6: `eval` may be null only while origin=hub AND safety_class!=motion... exception: the two sim drivers ship with M0 evalcards generated from TC-A1..A3 runs.

Acceptance: `tests/unit/test_manifests.py::test_all_lint` (CAP-1..3), including
positive and malformed `is_clock` declarations; `::test_search_cli_json`
(CAP-4), `::test_registry_completeness` (CAP-5 — the curated list exactly,
extras must be evalcarded agent-authored skills, curated gap asserted);
`tests/unit/test_skill_register.py` (the registration path).
