# Worker capability observations

The worker capability modules produce scoped engineering observations for a
Python-only macOS worker profile. They support MON-6, MON-8, MON-12, MON-13 and
TRT-6; their reports do not authorize study collection.

`worker_authority_probe` uses fixed Python operations for reads, subprocess
reads, loose Git objects, writes and executable attempts. Denial requires the
intended operation to start and report a permission error without exposing the
controller sentinel. Startup errors or arbitrary nonzero exits do not pass.
`worker_network_probe` checks an unrestricted loopback control and the confined
connection, retaining process records and stopping its owned listener and child.

`worker_capability` aggregates the required cases against the supplied profile,
with unrestricted controls for denied operations. It binds interpreter, adapter,
profile and probe identities, retains raw captures, and removes only its owned
visible and writable fixture directories. Changed identities, failed controls or
uncertain cleanup prevent a passing report. The report explicitly retains its
limits: selected filesystem and Git routes, TCP and one executable do not prove
exhaustive IPC, descendant containment, runtime closure or independent confinement.

`worker_declaration` verifies admitted runtime and interpreter identities, reserves
fresh bundle/HOME/evidence paths, compiles the worker policy and runs those actual
observations. Failed reservations remain retained and cannot be silently reused.
A successful declaration leaves source capture to the next preparation layer.
The interpreter must run directly under its single executable grant. For a macOS
framework Python, select and hash the Python.app interpreter, since the command
line launcher re-executes it; startup refusal never counts as observed denial.

Tests exercise parser refusals, failed startup, cancellation, listener failure,
actual macOS profiles and a declared typed worker exchanging Arrow data. Actual
sandbox cases are platform-gated; portable result-validation tests run on Linux.
Graph provisioning, complete session accounting and independent study gates remain
separate work under #519.
