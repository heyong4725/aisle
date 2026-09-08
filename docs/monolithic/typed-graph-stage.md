# Typed graph staging and evidence

The graph staging modules support MON-2, MON-6, MON-8, MON-12 and MON-13 under
#519. They prepare and check private engineering artifacts; they do not admit a
matched session or authorize study collection.

`stage_typed_graph` requires successful normal validation of the exact retained
snapshot and matching worker bundle source. It replaces authored process entries
with bound typed hosts, preserves validated routes and the turn plan, and refuses
changes to trusted executable settings. Authored arguments and environment values
become worker configuration; expansion uses only declared values. They are removed
from the trusted host's process settings. Generated hosts retain the
`expert_parity` purpose required by MON-11 for engineering shakeouts. This label
does not certify expert authorship or authorize study collection.

`verify_graph_stage` checks the closed inventory, snapshot binding and host config
hashes. `preflight_graph_stage` also checks every worker's launch authority, shared
runtime and disjoint private state before graph startup. Host startup repeats its
own checks. Staging and preflight reports explicitly withhold execution/session
admission and confirmatory readiness.

The rollout adapters bind retained validation and transport to the same authored
graph, controller and embodiment. Sequential relaunch selection requires a fresh
stage with the same snapshot and runtime; ordinary rollout gates remain required.
These helpers do not implement the enclosing session's total resource budget.

`audit_graph_stage` reconciles host/worker results, launch inputs, RPC frame hashes
and file inventories after workers stop. An authored module failure can retain a
valid audit; missing or inconsistent infrastructure evidence cannot.
`retain_graph_stage` copies raw evidence into a fresh destination, detects changed
bytes or inventories, and retains collection errors even when the audit failed.
Callers require both audit and collection integrity. Neither result independently
proves process-tree termination, exhaustive IPC containment or complete session
accounting.
