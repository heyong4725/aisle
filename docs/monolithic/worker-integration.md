# Monolithic worker integration

The monolithic CLI and Dora broker can execute the authored controller in a
source-bound worker. This supports MON-3, MON-4, MON-6, MON-8, MON-12 and MON-13
under #519.

`harness monolith check` and `harness monolith run` accept `--worker-config` and
`--worker-config-sha256` together. A partial selection is an infrastructure refusal.
The configuration binds the authored source, embodiment, runtime, declared
capabilities and separate check/run evidence roots. With neither option, the
existing local execution path remains available.

The broker retains observation/action validation and turn metadata. Worker
construction and callbacks run through the existing worker supervisor; syntax or
other authored failures remain module failures, while invalid launch identities
and infrastructure failures refuse execution. Worker resources close on normal
exit, callback failure and constructor failure.

`prepare_monolithic_run` captures the current authored module into a private
output and fills a reserved empty worker bundle. It rejects redirected output,
protected-source/runtime overlap and reused bundles before writing preparation
artifacts. `retain_worker_attempt` preserves the module, configuration and raw
worker evidence even when the precheck fails before a simulation starts.

Configurations retain MON-11's `expert_parity` purpose. These engineering helpers
do not establish expert provenance, matched-session admission, exhaustive process
cleanup, total simulator accounting or pilot/confirmatory readiness. The enclosing
controller must enforce and retain those prerequisites separately.
