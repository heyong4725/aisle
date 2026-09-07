# Monolithic worker RPC

The internal worker transport lets a controller module invoke parent-owned
primitives through Arrow IPC. It supports the existing primitive API and
controller callbacks without transferring Python objects or importing the
controller module into the parent process. This is one implementation layer
for issue #519 (CON-4, MON-3, MON-4, MON-8, MON-12, MON-13).

`WorkerSupervisor` adopts an already launched child with binary stdin/stdout
pipes. The caller supplies the OS confinement, runtime, stderr retention and
resource policy. `worker.serve` receives controller source, constructs the
controller with a `PrimitiveClient`, and handles sequential events. The parent
checks primitive requests against an explicit member allowlist, typed handles
and call limits, then validates returned actions through the broker's existing
validation function.

The transport accepts bounded trees of built-in data and numeric NumPy arrays.
Messages have a fixed Arrow schema and length framing; arbitrary Python object
serialization is forbidden. Command and primitive request identities must
advance in order. A denied primitive request terminates the worker session even
if authored code tries to catch the denial.

The supervisor retains raw messages, hashes, an index and terminal status in a
fresh evidence directory. Partial incoming frames survive protocol failures.
Initialization binds the source hash and API version. Pipe deadlines stop and
reap a nonreplying child; successful close requires an acknowledgement and zero
exit status.

These modules do not change the default harness execution path. Their evidence
explicitly records `confinement_verified: false`: adopting a process does not
prove its launch policy. The pipe deadline does not preempt a trusted primitive
call, and message limits do not establish a native Arrow parser memory bound.
Process-group cleanup does not prove teardown of descendants that create new
sessions. These responsibilities remain part of the complete session integration
and acceptance work in #519.

The five `test_monolith_{wire,requests,proxy,worker,supervisor}.py` unit modules
exercise serialization refusals, primitive authority and state, proxy typing,
real child execution, deadline cleanup, action refusal and raw evidence
retention. Planner fixtures establish delegation, not simulator success or
independent confinement acceptance.
