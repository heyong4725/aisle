# Linux quickstart acceptance (BMK-7, BMK-8)

The `benchmark-quickstart` GitHub Actions workflow runs the existing public T0
quickstart twice on separate fresh Ubuntu 24.04 runners: once from a checkout
and once from the committed source archive built by `tools/source_archive.py`.
Linux uses the declared CPU simulation backend. Neither job restores an Actions
uv cache, pre-installs the project environment, or supplies an existing run.
The pinned Dora CLI is installed before the documented quickstart command.

The workflow runs when its definition changes in a pull request and can be
manually dispatched on a chosen revision after it is merged. Both jobs must
succeed. A failed attempt remains a failed check; it is not replaced by a unit
test result. Artifacts retain the quickstart JSON, stderr, archive identity
where applicable, and raw run directories for 30 days, including failed runs.
Download and preserve those artifacts before expiry when citing an attempt.

These are internal `development_public` acceptance runs of a draft candidate,
not independent reproduction, an external-user acceptance, or a released
archive. The quickstart reports its own stage timings and sampled process
memory with the limits in `resource-accounting.md`; Actions logs separately
retain checkout and CLI/bootstrap installation steps. Total cold-download,
installation storage and exact peak memory remain separate BMK-8 measurements.
GitHub-hosted macOS runners do not provide the Metal device required by the
macOS simulation backend; the existing nightly workflow documents that limit.
A Linux success cannot satisfy the macOS gate or any physical-robot criterion.
