# Typed node host

`aisle.harness.typed_node_host` connects a hash-bound typed worker declaration
to a controller-owned Dora node and turn wrapper. It supports MON-6, MON-12 and
MON-13 as a prerequisite for #519.

The host accepts a canonical, size-limited configuration with an exact SHA-256.
It rejects duplicate or extra fields, undeclared modules and outputs, invalid
budgets, changed launch assets, and configuration or evidence paths that overlap
worker authority. Configuration purposes remain restricted to engineering
`expert_parity` records. This does not grant expert authorship or study admission.

The host owns Dora attachment and turn acknowledgements; authored code executes
in the worker. Attachment is deferred until the started worker requests its
transport. Runtime verification therefore finishes before Dora announces the
host ready and starts the first turn deadline. An engineering integration failure
exposed the previous eager attachment order; a deterministic process test pins
attachment after worker spawn without extending the turn watchdog.

The launcher retains worker evidence and the host adds its configuration receipt.
The configuration is checked again after execution. The CLI emits one JSON
verdict and exits successfully only when that verdict is successful.

Standalone tests use a fixture transport and a synthetic adapter with real worker
processes. They verify host wiring, turn ownership, refusals and startup order.
Graph replacement, session provisioning, total accounting and independent study
prerequisites remain separate work under #519. Outer lifecycle/resource limits
remain the caller's responsibility, as described in `typed-worker-launch.md`.
