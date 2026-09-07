# Worker launch and runtime records

The RPC transport needs a launcher that checks the code and authority supplied
by its caller. `aisle.monolith.worker_launch` builds a standalone worker bundle,
checks its exact file inventory against this controller revision, and launches
it through the existing verified adapter. It supports MON-6, MON-8, MON-12 and
MON-13; issue #519 tracks the enclosing matched-session integration.

`build_worker_bundle(path)` creates a fresh bundle containing only the worker's
implementations. `capture_runtime(roots)` in `aisle.harness.matched_runtime`
records disjoint canonical runtime trees, including directories, modes, file
hashes and symlinks. Links must resolve inside the declared trees, including links whose internal
target is currently absent (for example, omitted optional framework headers).
Their link text and resolved target remain recorded; later target creation or
retargeting invalidates the record. Missing external targets are still refused.
Verification recomputes the inventory, so added files, changed modes and changed targets
invalidate the record.

`launch_worker(...)` requires the bundle and runtime records, a matching policy
and adapter attestation, interpreter identity, declared private HOME environment,
hidden source roots, a fresh private evidence directory, and positive worker
budgets. It refuses broader read/write/execute grants, external networking,
source or evidence exposure, and drift before spawning. The process receives an
isolated Python invocation with the bundle and declared runtime import roots.
The launcher does not run site-directory discovery on those roots.

The context yields the existing `WorkerSupervisor`. It retains the launch inputs,
adapter record, stderr and RPC evidence, reaps the worker through the supervisor,
and rechecks immutable inputs after teardown. Exceptions produce a retained
failure record once the evidence directory has been created.

The caller still owns session admission, capability-evidence production and the
larger process lifecycle. This API does not establish independent confinement,
expert parity, pilot or confirmatory readiness. Its pipe deadlines do not preempt
trusted primitive computation or prove cleanup of descendants that create their
own process sessions; those remain part of #519's integration work.

The unit tests exercise actual worker RPC through an explicitly synthetic
adapter, refusal before spawn for input/authority drift, and checks after worker
teardown. Runtime tests cover file additions and changes, mode changes, and links
that would escape the declared trees. These tests are not OS-confinement evidence.
