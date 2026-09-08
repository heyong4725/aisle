# Bound typed worker launch

`typed_execution_bundle` and `typed_worker_launch` provide the execution layer
for the four declared typed node implementations. They support MON-2, MON-6,
MON-12 and MON-13 as prerequisites for #519.

The bundle builder copies the four participant scripts as data alongside a
fixed controller dependency list. It does not follow authored imports or copy
participant-selected helpers. Its externally retained receipt binds the exact
inventory, file origins, content and read-only modes. Session admission must
bind that receipt and the trusted controller revision.

The launcher requires the bundle, bound runtime and interpreter, private HOME,
canonical hidden source roots, exact read/execute grants, denied external
networking and verified adapter evidence. It checks these inputs before spawn
and after supervision, and retains the profile, launch configuration, stderr,
RPC journal and terminal result. Authored errors remain module results;
identity drift and invalid authority remain infrastructure exclusions.

The host supervisor owns the worker's pipes and process-group cleanup, including
cancellation. Its pipe deadline does not preempt a blocking trusted event
iterator or native decoder. The caller must provide outer resource limits and
session lifecycle control; this layer does not prove exhaustive descendant
containment.

Tests start all four baseline scripts and exercise the full launch path with an
explicitly synthetic adapter. They prove process wiring and refusal behavior,
not independent OS confinement, full-session accounting or study admission.
Graph provisioning and the matched-session integration remain in #519.
