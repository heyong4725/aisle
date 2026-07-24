# SPEC 310 — Contract extension: force sensing, balances, tool changer

Status: DRAFT, gated with SPEC 300 (PW-0). Class C once stable (extends SPEC 010).
All SPEC 010 rules (TC-1..4 units, metadata, rate semantics) apply unchanged.

Force/torque (the workstation arm is force-controlled; our contract is
position-only today):
- FT-1: New topic `wrist_ft` out Float32[6] (Fx,Fy,Fz,Tx,Ty,Tz; N and N·m,
  EE frame) @ ≥200 Hz in sim (rate TBD-SPIKE if solver-bound); TC-2 metadata.
- FT-2: New input `joint_cmd_mode` metadata key on `joint_cmd`:
  mode ∈ {position (default), impedance} with stiffness params; the bridge
  MUST reject impedance commands with an ERROR event if the embodiment/solver
  cannot honor them (honesty over silent fallback, BRG-7 spirit).
- FT-3: Budget-guard extension: max |F| and |T| thresholds from limits.toml;
  exceeding = clamp-to-stop (freeze motion, emit violation) — powder tools near
  glass vessels make force limits the primary safety mechanism for this family.

Balances:
- BAL-1: Topics `balance_mass_a`, `balance_mass_b` out Float32[1] (grams)
  @ 10 Hz, metadata {stable: bool, resolution_g}. In sim: oracle mass + noise
  model per PW-3. `stable` mirrors real analytical-balance settling semantics;
  the sim noise model MUST include settling delay (configurable) so dosing
  loops learn to wait — this is a known real-instrument behavior.
- BAL-2: Tare is a dora SERVICE (request_id pattern): request `tare` payload
  UInt8[1] (balance id), reply `tare_done` with metadata {balance, t_ms}.

Tool changer:
- TOOL-1: Tool change is a dora SERVICE: request `tool_change` payload UInt8[1]
  (tool id from `scenes/tools.toml`), reply `tool_done` metadata {tool,
  success, t_ms}. In sim: detach/attach tool mesh at EE. The bridge MUST NOT
  accept joint_cmd during an in-flight tool change (reject with ERROR event).
- TOOL-2: `scenes/tools.toml` defines each tool: id, capacity_mg range,
  mesh, EE transform. Registry manifests reference tool ids, letting the
  validator check tool-capability consistency (a graph whose dosing-planner
  assumes tool 2 while tool_change requests tool 0 is rejectable — new
  validator check TOOL_MISMATCH, added to VAL-2's code list).

Acceptance: tests/accept/test_contract_bench.py::test_ft_stream_schema (FT-1),
::test_impedance_reject_when_unsupported (FT-2), ::test_balance_settling (BAL-1),
::test_tare_service (BAL-2), ::test_tool_change_lifecycle_and_cmd_lockout (TOOL-1);
tests/unit/test_validator_tool_mismatch.py (TOOL-2).
