# Development source archives

BMK-7/BMK-8 packaging path (pre-release). Build a portable source archive from
an explicit committed revision:

```bash
uv run python tools/source_archive.py --revision HEAD --output /tmp/aisle-source.tar
```

Extract into a new directory and run `uv run python tools/quickstart.py` there,
with the documented uv and Dora prerequisites. The builder exports committed
blobs, including executable modes and symlink contents; working-tree edits are
excluded. An existing output file is refused. SHA-1 Git repositories are the
supported source format; submodules and reserved provenance paths are refused.

The archive includes `.aisle-source.json`: the original Git commit object and
its tree objects. Quickstart verifies their Git object hashes and checks every
tracked file's bytes and mode against that tree before the run and when building
the submission. It records the derived revision and provenance digest alongside
the existing execution graph/environment evidence. No `.git` directory is created.
Plain source archives without this evidence still lack the required provenance.

This establishes source content identity, not publisher authentication, a signed
execution receipt, or blind-evaluation eligibility. Runtime/untracked files are
outside the source proof. Release consumers must obtain the expected revision or
archive digest through the release's authenticated publication channel. All
release, platform, independent-user, and private-evaluator gates still apply.
