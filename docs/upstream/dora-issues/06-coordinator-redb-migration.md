# UX: `dora up` fails on stale `coordinator.redb` schema — offer auto-recreate

**Filed as [dora-rs/dora#2921](https://github.com/dora-rs/dora/issues/2921) (2026-07-31).**

**Versions:** dora-cli 1.0.0-rc.4 (git rev `7eb4a5f8b`), upgrading from an older local install.

`dora up` prints "started dora coordinator" but the coordinator dies immediately when
`~/.dora/coordinator.redb` predates the current schema:

```
redb schema version mismatch: database at `/Users/<u>/.dora/coordinator.redb` has v2,
but this binary expects v3. Delete the file and restart to create a fresh database,
or use `--store memory` to bypass persistence.
```

The message is good — but `dora up` itself only reports a connect timeout, so the real
error is buried in `out/dora-coordinator.txt`, and every user who upgrades across the
schema bump hits it. Ask: surface the coordinator's startup error in `dora up`'s output,
and/or offer `dora up --recreate-store` (or auto-recreate with a warning, since the file
is a cache).
