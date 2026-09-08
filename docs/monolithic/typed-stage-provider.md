# Typed worker provisioning and relaunch preparation

These helpers support MON-2, MON-6, MON-8, MON-12 and MON-13 for #519. They
connect a successfully validated snapshot to fresh worker declarations and the
typed graph staging APIs.

`provision_typed_workers` selects authored node instances from their source paths,
including renamed nodes and repeated source instances. It allocates separate
worker HOME and bundle reservations and observes each worker's actual capability
through `provision_worker_declaration`. Failed provisioning retains its receipt.

`prepare_typed_stages` fills reserved empty bundles with the validated sources,
binds the declared runtime and adapter, builds the transport stages and preflights
every host. It refuses caller-supplied source manifests, overlapping protected
state and nonempty reservations. It does not replace capability receipts.

`TypedStageProvider` provisions each sequential launch on demand. Repeated or
out-of-order indices are refused; a preparation failure or cancellation makes the
provider terminal. Each successful relaunch has fresh allocation and evidence
roots while retaining the same snapshot binding. The graph staging layer retains
the MON-11 `expert_parity` purpose for these engineering artifacts.

The enclosing matched-session controller must authorize the supplied identities,
enforce total tool and simulator budgets, finish prior-launch cleanup, and retain
failed attempts. These helpers do not establish expert provenance, parity,
complete session evidence or pilot/confirmatory readiness.
