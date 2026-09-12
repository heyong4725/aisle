# ADR-pilot-non-oracle-surface — Bind the paired non-oracle surface for the #347 pilot

Status: PROPOSED — implementation in progress for #347; Class C review required
before merge. This records a CON-15 interpretation of the accepted pilot-first
ADR-66. It does not authorize collection or satisfy an independent review.

## Problem

The matched runner currently binds the T1 L1 graphs and primitive surface.
Both policy regions receive simulator segmentation; the monolithic primitive
API exposes only the L1 pose session. The BND calibration instead names a T1
L2 short-composition candidate and a T2 engineering candidate. The matched
runner rejects T2. Registering the existing L1 fixture as the pilot would
change the task and privilege boundary preserved by ADR-66 and BND-2.

## Decision

Implement a named T1 L2 pilot surface derived from the current short-composition
candidate. Its current perception-eligibility failure stays a recorded pilot
limitation. The implementation must not tune identity thresholds or select a
replacement task using pilot outcomes (BND-7/BND-10).

Both arms receive the same rendered RGB, sensor depth, public camera calibration,
robot state, task goal and permitted guard feedback. Both use the existing
`l2_pose.L2Session`, pinned identity model, refusal thresholds, grasp planner,
trajectory solver and streamer (MON-4). The monolithic worker requests an L2
session through an explicit data-only handle; that handle exposes RGB/depth
operations and no simulator-segmentation operation. Model objects remain in
the trusted broker and are loaded once per broker, with independent pose state
for each requested session.

The paired pilot graphs must exclude simulator segmentation and privileged
scene identity from policy observations. Raw oracle results remain with the
trusted evaluator and rollout controller. Policy lifecycle and feedback must
be defined and checked for both arms without exposing verdict content or an
oracle-dependent signal during an attempt (BND-2/BND-3). Removing a YAML input
alone does not establish this: runtime files, environment, worker requests,
model inputs, traces and tool replies also require inspection.

The proposed controller projection copies only the T1 target, tier and timeout,
and the explicitly enumerated L2 camera calibration fields. It strips private
seed/reset fields and incoming transport extensions, emits a constant reset
notification, and does not relay oracle results. A graph-declared
`fixed-horizon-t1-v1` rollout lifecycle buffers oracle results until the reset
simulation timestamp plus the registered timeout before writing completion
records or advancing the reset handshake. Buffering the completion file is
necessary because the outer runner uses it to decide when to stop the graph.
The original oracle verdict remains unchanged. This does not establish full
timing isolation: paired graphs, turn routes, policy-visible feedback, traces,
and runtime confinement still require integrated evidence. Missing results stay
missing; no synthetic successful verdict is substituted at the deadline.

Pilot staging also restricts authored topology to the registered public source
topics and policy-internal edges. Inputs to reset, verifier and projection stay
bound to the trusted baseline. Policy commands may still enter the shared guard,
feedback may enter the rollout recorder, and turn acknowledgements may enter
the barrier. This preserves graph engineering within the policy region without
granting a path around the public projection. These checks complement normal
schema/topology validation; they do not replace runtime isolation evidence.

The surface selection must be a controller-owned, named binding, carried through
admission, the editable allowlist, interface and treatment documents, check/run
commands, worker preparation and retained evidence. An unsupported selection,
changed artifact or mixed-arm surface must refuse before execution (MON-1,
MON-8/MON-13). The legacy L1 engineering fixture remains separately identified;
its frontend conformance results cannot attest changed controller bytes or a
new execution context.

The selected pilot artifact must be registered before the first of six sessions
per arm. Use a fresh campaign and seed commitment, one bound agent system,
alternating arms, fixed budgets and retained failed attempts. Analysis records
remain pilot-only and descriptive; engineering executor evidence must not be
promoted to confirmatory eligibility (STA-3/CSE-8). Wrong-object outcomes,
guard bypass or an unattested environment stop the pilot as specified in #347.

## Validation and review

Required evidence includes direct/worker L2 primitive parity; refusal of
segmentation, hidden model access and changed trusted callables; paired graph
and feedback boundaries; surface binding through real admission and execution;
and retained paired simulation evidence. Validate the final integrated code
once against the repository gates, then qualify its exact frontend/controller
context before collection. Historical conformance receipts remain historical.

Any frozen-set change requires the CON-7 env-change workflow. Independent expert
provenance, statistical review, private-evaluator evidence and confirmatory
collection gates stay separately pending. This implementation cannot supply
those attestations itself.
