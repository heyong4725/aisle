# macOS confinement capability audit

This directory contains a **synthetic, unscored capability audit** for SPEC 420
TRT-3/TRT-5/TRT-6. It tests whether a controller-owned macOS `sandbox-exec`
profile can preserve declared visible/output operations while denying synthetic
hidden absolute, parent-traversal, symlink, subprocess, and write paths.

It is not a confirmatory agent session and does not establish Linux support,
vendor-network or credential compatibility, Claude/Codex parity, git-object or
alternate-worktree denial, or complete treatment integrity. The report keeps
`confirmatory_ready` false and preserves these limitations.

Regenerate a fresh timestamped audit:

```bash
uv run python -m aisle.harness.treatment_confinement audit-macos \
  --output analysis/treatment-integrity/macos-confinement/capability-audit-new.json
```

The command refuses to overwrite an existing record. Compare the new report's
case outcomes, denial-detection rate, false-alarm rate, platform identity,
adapter hash, imported `system.sb` hash, compiled-profile hash, and policy id.
Temporary synthetic hidden bytes and the compiled profile are removed after the
audit; only their hashes, exposure booleans, and role-scoped policy metadata are
retained.
