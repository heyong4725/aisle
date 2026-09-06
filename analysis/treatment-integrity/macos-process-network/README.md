# macOS process and TCP capability audit

Issue #350 follow-up (TRT-6, THR-10): the v3 capability audit adds a real
loopback TCP read and an executable outside the declared allowlist. Each
denial has an unrestricted positive control using the same command. The
controller serves only a synthetic canary; no hidden benchmark material or
real credentials are accessed. Existing filesystem, Git-object, alternate
worktree, and declared-output controls remain in the matrix.

The retained `capability-audit.json` records five unrestricted baseline
exposures, four declared operations, and nine denied operations. All pass.
The compiled profile, adapter binary, imported system profile, platform,
case outcomes, and output digests are retained in the raw record.

Reproduce on macOS into a new path:

```console
uv run python -m aisle.harness.treatment_confinement audit-macos \
  --output /tmp/aisle-macos-process-network.json
```

The v3 attestation requires the expanded case set; earlier v1/v2 records
remain historical and do not authorize a v3 command. This is an unscored
capability result for this macOS profile. It does not establish exhaustive
IPC/process coverage, Linux support, vendor authentication/network access,
real driver isolation, or Claude/Codex campaign parity. `confirmatory_ready`
remains false, and #350 remains open.
