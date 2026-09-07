# Linux quickstart acceptance (BMK-7, BMK-8)

The `benchmark-quickstart` GitHub Actions workflow runs the existing public T0
quickstart twice on separate fresh Ubuntu 24.04 runners: once from a checkout
and once from the committed source archive built by `tools/source_archive.py`.
Linux uses the declared CPU simulation backend. Neither job restores an Actions
uv cache, pre-installs the project environment, or supplies an existing run.
The declared source-pinned Dora CLI and Ubuntu rendering libraries are installed
before the quickstart command. The source installer uses a separate bootstrap
environment containing the pinned Python node API, verifies the immutable source
and lockfile, and records its executable receipt. This declared bootstrap may
populate the fresh uv cache; no cache is restored from an earlier run. The
quickstart installs the project environment and verifies the receipt through
`--runtime-prefix`. Installation JSON and the receipt are retained as artifacts. Even with CPU physics, Genesis constructs an offscreen
renderer. On Ubuntu 24.04 the workflow installs `libegl1`, `libgl1` and
`libgl1-mesa-dri` (including the Mesa EGL dependency) with apt, and retains the
resolved package versions in `system-packages.txt`. These are declared system
prerequisites, separate from the Python lockfile.

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

For upstream debugging (#516), a manual dispatch may supply `dora_commit` as
an exact 40-character SHA from `dora-rs/dora`. A separate runner builds that
CLI with its locked dependencies; the clone/archive runners record its source
commit, lockfile hash, executable hash, and version. The Python node package
continues to use AISLE's pinned release. This is a compatibility diagnostic,
not a paired release installation or benchmark acceptance.

Candidate jobs are named `diagnostic/linux-*`. Their raw stage output is kept
as `candidate-quickstart-observed.json`; `quickstart.json` explicitly reports
`ok: false`, `release_acceptance: false`, and the CLI override even if all runtime
stages pass. A successful diagnostic check cannot satisfy BMK-7/BMK-8 or justify
merging this acceptance PR. After reviewing and validating a corrected immutable
source pin, rerun with an empty `dora_commit` to exercise `benchmark/linux-*`
through the declared installer and verified prefix. No Dora release tag is
required. A pin still marked `candidate` is refused before rollout, even when
its installation identity verifies successfully.
