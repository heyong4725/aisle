# Paired T1 L2 pilot candidate

This proposed surface is `t1-l2-pilot-v1`. It uses rendered overhead RGB and
same-stamp sensor depth, the existing pinned L2 identity/refusal implementation,
and Franka pick-and-place primitives. Its current perception-eligibility failure
remains a pilot limitation. No threshold or task substitution is authorized here.

The public goal contains `target_med`, `tier` and `timeout_s`. A trusted projection
removes seeds, simulator identity and reset request identifiers. Policies do not
receive oracle verdicts. The controller buffers completion records until the
fixed simulation deadline before advancing to another reset. Both candidates
retry using plan-completion feedback and the registered grace/budget.

Use the selected `allowlist.json`, `interface-map.json` and `treatment-table.json`.
The monolithic deliverable is `experts/monolithic/pilot_t1_l2.py`; the typed
deliverable begins at `graphs/pilot_t1_l2_typed.yaml`. Trusted projection, judge,
reset and guard implementations are outside participant edit authority.

The candidates are maintainer-derived, not independent expert artifacts. Their
provenance, task feasibility, whole-runtime non-oracle boundary, and exact
frontend conformance must be reviewed and qualified before pilot collection.
These documents do not attest those gates or authorize confirmatory use.

Regenerate bound records with `harness monolith table --task-surface
t1-l2-pilot-v1 --write`; check graph/interface agreement with `harness monolith
interface --task-surface t1-l2-pilot-v1`.

For isolated L2 workers, prepare a dedicated public model cache with
`uv run python -m aisle.harness.pilot_model_cache --root <controller-root>
--source-snapshot <pinned-identity-snapshot> --output <fresh-cache-root>`.
The source must be the pinned `snapshots/<revision>` directory in an existing
Hugging Face cache, with its corresponding `trees/<revision>.json` listing.
The command copies files named and verified by the controller's `models.lock`
and a filtered tree listing containing only those files. The pinned Hub loader
requires this metadata even when snapshot bytes are already present. Preparation
checks file sizes and LFS digests and includes the filtered metadata hash in its
receipt; it does not download files or grant access. Keep the cache outside
the controller and participant source roots. Include it in the shared runtime
inventory and read-only worker policy. Bind the returned `worker_environment`
values (`HF_HOME` and the two offline flags) in the prepared typed candidate's
`detected-pose.env` before validation, snapshotting and admission. Record the
resolved cache path and runtime identity with the session inputs.

Each worker still gets a fresh empty home and its normal network restrictions.
Do not grant access to the operator's whole cache or copy its tokens, private
files or unpinned models. A missing or changed pinned model must fail startup.
Direct engineering graphs can use the installed local cache; that does not
establish that an isolated worker has the required declared model access.
