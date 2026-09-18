# ADR-pilot-public-observations — Public observations for the admitted L2 candidate

Status: PROPOSED; Class C review required before merge (CON-10).

The candidate and launch machinery merged in #581/#582 and the L2 primitive
merged in #588 supersede #578's separate task-surface selector and primitive
implementation. Preserve those merged implementations. The remaining BND-2,
MON-4 and MON-13 boundary is the policy-visible data: the rollout client emits
seed-bearing goals and reset request metadata, and the original L2 templates
route those records into the policy region.

The `t1-l2-realistic` candidate now names copies of its paired graphs that route
calibration, goals and reset notifications through a controller-owned projection.
It exposes enumerated L2 calibration fields, `target_med`, `tier`, `timeout_s`,
and public goal/time metadata. Reset is a constant notification. Trusted reset,
guard and evaluator nodes retain their original inputs. The frozen expert
graphs and the existing L2 primitive and monolithic deliverable are unchanged.

Retain main's realistic verifier and its full-horizon lifecycle. The template's
`episode_result` edge is rewritten to the realistic verifier by rollout staging;
the oracle remains a scoring instrument. Typed graph edits may use the public
boundary and policy-internal edges, but cannot bypass the projection, alter
trusted instrument inputs, or alias the template oracle-result edge onto a
different input name or scalar YAML form that would evade the realistic-verifier
rewrite.

The separate model-cache preparer copies only hash-pinned public identity files
and the downloader's necessary tree metadata. Its output must be included in
the caller's admitted runtime receipt and read policy, with its returned offline
environment bound before validation and snapshot creation. The preparer does
not confer a read grant or provide confinement evidence.

Earlier registrations and evidence remain historical. Existing candidate-table
hashes must fail closed after this change; an updated registration and exact
execution-context evidence are required before using the revised candidate in
a pilot. This amendment does not refresh private seeds, approve collection, or
establish independent parity or confinement.
