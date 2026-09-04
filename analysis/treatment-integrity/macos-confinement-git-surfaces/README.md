# macOS Git-surface confinement capability

This directory retains an **unscored synthetic capability audit** for the
Git-object and alternate-worktree access paths required by TRT-6. It is not a
confirmatory campaign result and does not contain a benchmark fault identity,
real credential, or hardware observation.

Reproduce on macOS with:

```console
uv run python -m aisle.harness.treatment_confinement audit-macos \
  --output /tmp/aisle-macos-confinement-git-surfaces.json
```

The primary retained run is session
`macos-capability-20260901T140119666893Z-c357f2662e82`. Its raw JSON SHA-256 is
`618428ff9f940316abfdc98f4290d03cc481a35ab165ffb949395d8c9b58aca6`.
It isolates both subject-side Git and controller fixture setup from ambient Git
configuration. The earlier development run is also retained without
replacement as `capability-audit.json` (SHA-256
`94a256dc5b8a147433b324941f01308be854bdfa07f5017ed9dac2bcc1dc906f`);
its subject-side environment was isolated, but controller fixture setup still
inherited ambient Git configuration. Both runs produced the same case-level
outcomes.

The three unrestricted baselines demonstrate that the synthetic hidden bytes
are reachable by direct path, alternate worktree, and raw Git-object access
without confinement. With the external profile active, four declared
operations pass and all seven denied operations fail without exposing the
sentinel. The declared controls include reading a visible Git object through
the same selected Apple Git executable and an isolated Git HOME/config
environment. This guards against counting a broken Git runtime as successful
confinement.

`confirmatory_ready` remains `false`. This audit is macOS-only, exercises only
the system Git CLI among possible allowed tools, uses synthetic sentinels, and
does not establish Linux support, vendor authentication/network compatibility,
complete tool-surface coverage, or Claude/Codex end-to-end parity. The imported
Apple `system.sb` profile is a private interface; both it and the adapter binary
are hashed in the raw record.
