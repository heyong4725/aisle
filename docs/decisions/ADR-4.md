# ADR-4: validator interpretations for T03 (SPEC 060)

Interpretations chosen (CON-15) where SPEC 060 is ambiguous or blocked:
(1) VAL-7's good corpus ships three valid graphs now; `graphs/expert_t0.yaml`
joins at T08 — it does not exist yet and its oracle rung is blocked on the
issue #2 spec conflict. (2) Graph node ids ARE manifest ids (VAL-1); wiring
fewer inputs than a manifest declares is legal (idle ports), but an input
port absent from the manifest is a SCHEMA_MISMATCH. (3) VAL-5 is enforced as
immediate-upstream: a motion driver's joint_cmd/gripper_cmd input must be fed
directly by `budget-guard` (whose manifest lands at T07; until then no motion
graph validates, which is correct — there is no guard to traverse). Any other
source — another node, a timer, an unresolvable id — is MOTION_UNGATED, and
the check runs before all schema/producer checks so it is never masked.
Only `dora/timer/millis/<N>` (N > 0) is recognized as a dora builtin source;
dora's extended input form `{source: ..., queue_size: N}` is unwrapped.
(4) RATE_INCOMPATIBLE is checked where a producer rate is knowable — timer
sources (dora/timer/millis/N) against the consumer's declared rate_hz with
the TC-4 ±20% band; manifest outputs carry latency_class, not rates, so
node-to-node edges are not rate-checked. (5) The target arm profile comes
from `--embodiment` (default franka), matching the M0-5 flag pattern.
(6) An unloadable or structurally invalid graph file reports code
GRAPH_INVALID — a loader failure distinct from VAL-2's check codes.
(7) `--allow-unproven` (design doc §8.2.1) downgrades EVAL_MISSING_FOR_MOTION
to a warning; the harness never sets it for agent runs.

Two constraints this places on later tasks: (a) T07 MUST declare the
budget-guard manifest with safety_class != motion — its inputs are literally
named joint_cmd/gripper_cmd (BG-1), so a motion-classed guard would flag its
own inputs MOTION_UNGATED and trip EVAL_MISSING_FOR_MOTION; the guard gates
motion, it does not execute it. (b) The TC acceptance runs that generate the
sim-driver evalcards (T05, per ADR-3) MUST land before T08 — until those
evalcards replace `eval: null`, every graph containing a sim driver fails
EVAL_MISSING_FOR_MOTION and `expert_t0.yaml` cannot validate without the
agent-forbidden `--allow-unproven` flag.
