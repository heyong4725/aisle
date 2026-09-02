# Synthetic later-arm contamination capability

This directory retains an **unscored synthetic** TRT-12 capability audit. It
does not contain an agent campaign, benchmark fault, credential, or physical
robot observation, and `confirmatory_ready` is explicitly `false`.

The primary retained session is
`contamination-20260901T150926942258Z-a4ccf6c9023e`. The raw JSON SHA-256 is
`6b8b13e328c7ee00a910cf4c10ef48a1c384cea3f0c228a999ea4352a2b8b1d1`.
All six predecessor channels contain the seeded sentinel before view
construction: a Git worktree, filesystem cache, filesystem HOME, transcript,
later-commit analysis path, and Git deliverable ref. The fresh later-arm view
is exported from the exact earlier commit using a sorted allowlist of regular
blobs, contains no `.git` namespace, and has zero sentinel exposures.

Reproduce with:

```console
uv run python -m aisle.harness.treatment_contamination audit-synthetic \
  --output /tmp/aisle-contamination-audit.json
```

The builder records the full baseline commit/tree, every visible path, file
mode and content hash, its own implementation hash, the selected Git binary
hash, and a content-derived view identity. Existing destinations, short or
unknown commits, unsafe paths, duplicate/unsorted allowlists, missing paths,
directories, symlinks, and Git links fail before view creation.

The earlier `audit.json` development observation is preserved rather than
silently replaced (raw SHA-256
`f5781c5ae61072911979299405290e9959e07dbb3a0223acf7562ac88fc9df94`).
It has the same case outcomes but predates the final object-format hardening;
`audit-object-format-hardened.json` is the review target.

TRT-12 remains incomplete until this builder is wired into both actual agent
launch paths and paired campaign dry runs demonstrate the sentinel exclusion.
TRT-7 must separately enforce cache, HOME, credential, environment, descriptor,
and socket isolation around the exported filesystem view.
