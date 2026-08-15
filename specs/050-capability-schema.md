# SPEC 050 — Capability manifest schema and registry

Status: DRAFT. Files: `registry/schema/capability.schema.json`, `registry/manifests/*.yaml`.

- CAP-1: Manifest fields (all required unless noted): id, kind(node|subgraph), provides[], requires[], inputs{name:{schema, rate_hz, is_clock?, turn_edge?}}, outputs{name:{schema, latency_class}}, params{name:{type,default,range?}} (optional), embodiment{arm[],gripper}, safety_class(perception|decision|motion), eval{suite,pass_rate,last_run}|null, origin(hub|agent-authored), source(path or pip ref). `is_clock` is boolean and defaults false; `turn_edge` is `forward` (default) or `episodic` (ADR-30 §1.3: reply/verdict/result/violation back-edges consumed at the receiver's NEXT turn opening — the declaration that lets TC-6/TC-7 request-reply and action cycles terminate under the turn barrier). `is_clock: true` declares a simulated-turn watermark input (ADR-30), not an ordinary state sample: the graph MUST wire it with a positive explicit `queue_size` and `queue_policy: backpressure`; the source MUST be the bridge simulation clock or a validated clock participant; and the node MUST preserve the turn stamp when it closes its causal work. Note the transport honesty caveat (ADR-30 §2, verified at the pinned dora rev): `backpressure` widens the effective cap 10x and then DROPS OLDEST — the barrier hang plus watchdog abort is the protocol's enforcement, not the queue itself. A latest-wins/drop-oldest state topic MAY drive a control cadence but MUST NOT serve as the structural turn barrier. Each watermark's parallel metadata lists `closed_outputs: list[str]` and `emitted_counts: list[int]` MUST enumerate EVERY output port of the node — equal length, lexical port order, unique names, nonnegative counts, count 0 ordinary, omission malformed — and a participant closes turn k only after receiving every count declared by forward upstreams for turn k and by episodic producers for turn k-1.
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
- CAP-6: `eval` may be null only while origin=hub AND safety_class!=motion... exception: the two sim drivers ship with M0 evalcards generated from TC-A1..A3 runs. A registering skill's shipped `min_pass_rate` (SPEC 070 `eval.yaml`) MUST be at or above the registry floor `REGISTRY_MIN_PASS_RATE` = 0.5 (ADR-37), and registration MUST refuse a sub-floor declaration at LOAD, before the eval rollout is spent. Without the floor the threshold that certifies a skill is chosen by the candidate: `harness skill register` already refuses a measured rate below `min_pass_rate`, so a skill shipping 0.0 registers at pass_rate 0.0 and the gate reports `ok` — observed live, `t2-scan-tsm` entered the desk-H3 L/T2-r2 campaign library at 0.0 (`analysis/h3/desk/desk_analysis.json`). This is #228's argument one layer down: there the exam paper was editable by the candidate, here the passing grade was. The floor is a MINIMUM and not a replacement — a skill declaring a stricter bar is still held to its own — and it is calibrated against the corpus, not chosen in the abstract: both registered skills independently chose 0.5, so no mainline skill is de-registered by it (asserted, not assumed: `::test_every_shipped_skill_meets_the_registry_floor`). Raising the floor later is a further spec-change, and evicts any shipped skill below the new value — which is why that test names the eviction rather than only the rule.

Acceptance: `tests/unit/test_manifests.py::test_all_lint` (CAP-1..3), including
positive and malformed `is_clock` declarations; `::test_search_cli_json`
(CAP-4), `::test_registry_completeness` (CAP-5 — the curated list exactly,
extras must be evalcarded agent-authored skills, curated gap asserted);
`tests/unit/test_skill_register.py` (the registration path), including
`::test_a_self_declared_floor_below_the_registry_floor_is_refused`,
`::test_a_stricter_self_declared_floor_still_governs`, and
`::test_every_shipped_skill_meets_the_registry_floor` (CAP-6 floor, ADR-37).
