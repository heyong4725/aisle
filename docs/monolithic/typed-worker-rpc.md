# Typed worker RPC

The internal typed-worker transport lets authored Python nodes consume events and
request outputs while the trusted host owns Dora, output grants, turn accounting
and completion acknowledgements. This layer supports issue #519 and MON-2,
MON-4, MON-6, MON-8, MON-12 and MON-13; it uses the Arrow framing introduced in
PR #522 (CON-4).

`TypedNodeRequests` accepts only ordered `next`, `send` and `stop` requests within
a declared call budget. It delegates to the existing trusted turn wrapper.
Workers cannot publish `turn_done` or undeclared outputs. `RemoteNode` presents
the corresponding policy API without owning a Dora connection.

Primitive Arrow arrays preserve type, validity and numeric range, including
nullable uint64 values. The explicit metadata envelope preserves Dora's datetime
timestamp as a built-in datetime with offset, microseconds and fold. Ordinary
data fields cannot collide with the timestamp encoding. Malformed timestamp and
array declarations are refused; decoding never loads a peer-selected type.
List, struct, dictionary, binary and timestamp Arrow arrays are not supported by
this adapter.

`typed_node_worker.serve` verifies one source declaration, provides one Node
facade, and runs the authored source as a Python script. Script entry guards,
arguments, environment settings and ordinary Python errors are preserved. Logs
remain separate from protocol messages. The source, module identity and worker
configuration are explicit; the parent never imports the authored module.

`supervise_typed_worker` adopts an already launched child and verifies its source
receipt, protocol ordering, reported completion, EOF and exit status against host
observations. It retains raw frames, hashes, partial failures and terminal status.
An early script return cannot claim that host input was exhausted. Cancellation
and setup refusal reap the adopted process.

This change does not select the default harness execution path or provision OS
confinement. Callers own launch admission, runtime/source bundles, stderr capture
and resource enforcement. Evidence therefore retains `confinement_verified:
false` and `confirmatory_ready: false`. Pipe deadlines do not preempt a blocking
trusted Node iterator, and process-group cleanup does not prove cleanup of every
possible descendant. Native Arrow allocation also needs outer resource limits.
The complete launch/session integration and its acceptance remain in #519.

The three `test_typed_node_{requests,worker,supervisor}.py` unit modules exercise
actual child processes with deterministic host events. They cover value and
metadata transport, denied authority, source drift, script semantics, forged
completion, trailing bytes, cancellation and process cleanup. These tests do not
establish simulator success, expert parity or independent confinement acceptance.
