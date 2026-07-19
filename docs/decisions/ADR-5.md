# ADR-5: consolidated interpretations from the T03 audit (CON-15)

Genuine ambiguities surfaced by the T03 audit and the T02 retroactive
review, resolved as follows (defects found alongside were fixed in code, not
recorded here):

**VAL-6 is edge-consumption semantics, not provenance taint.** The graph
`camera-source/oracle_state → verifier-oracle → episode_result →
task-state-machine` is LEGAL: task-state-machine's manifest declares the
`episode_result` input, TC-8 makes the oracle verifier's episode_result the
sanctioned ground-truth verdict, and HAR-3's in-context retries require
lifecycle nodes to consume it. The provenance boundary is the verifier's
manifest contract — its only output is the episode_result verdict (json), so
raw ground-truth state cannot flow past it without a manifest change, which
is a frozen-set/Class C event. Pinned by the good-corpus graph
`verifier_feedback_loop.yaml`. The issue #2 conflict (oracle-pose as a
sanctioned oracle *perception* rung) remains open for human decision;
isolation is NOT weakened here — oracle-pose consuming oracle_state stays in
the bad corpus.

**Unwired manifest-declared inputs are legal.** A graph node wiring none (or
a subset) of its manifest inputs validates: dora permits input subsets,
source nodes (camera-source) have zero inputs by design, and CAP-1 declares
a node's ports, not wiring obligations. A dead node is a composition-quality
concern for the composer (T08+), not a contract violation; `requires[]` is
likewise capability-level guidance for the composer, deliberately not an
edge-validation input. Pinned by `test_unwired_manifest_inputs_are_legal`.

**CON-6 layout is a required set, not an exhaustive allowlist.** The listed
directories MUST exist; auxiliary artifacts (.github/, templates/,
tests/fixtures/, docs/) are permitted. Flagged for human decision, NOT
deleted: `aisle-spec-package.zip`, `Aisle.zip`, and the duplicate root-level
`Project_AISLE_Experiment_Design.md` predate T01 and appear to be import
leftovers — deleting tracked files needs owner sign-off.

**Graph nodes may omit `outputs`.** Sink nodes (a verifier at the end of a
chain) legitimately produce nothing the graph consumes; when `outputs` is
present it must be a unique list of non-empty strings.

**`search` with zero matches is ok:true.** CON-8 ties exit code to the
operation outcome; an empty result set is a successful query, not an error
(misconfigured roots DO fail — that distinction is load-bearing).

**Registry mechanics carried over from T02** (retro review items, ratified
as interpretations rather than silently kept): manifest id == filename stem
as the uniqueness mechanism; closed enums for latency_class
{hard_rt, soft_rt, best_effort}, arm {franka, so101}, gripper
{parallel, any} — all Class C to extend, which is the intended friction; the
`pip:` source-ref prefix; eval.pass_rate bounded [0,1].

**OPEN — needs human ratification (do not treat as settled):**
ik-trajectory emits joint_cmd/gripper_cmd yet is safety_class `decision`.
Current policy: `motion` means "executes actuation" (the drivers); emitters
and gates are `decision`. Consequences if ratified: only drivers need
evalcards under CAP-6, and VAL-5 sinks are exactly the driver inputs. If
reversed (motion = produces commands): ik-trajectory and budget-guard need
evalcards, and the guard exemption in ADR-4 must be rethought. CAP-6 and
SPEC 080 semantics hang on this either way — flagged in the T02 retro
review as decision item 1.
