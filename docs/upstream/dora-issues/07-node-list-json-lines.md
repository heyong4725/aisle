# Docs/UX: `dora node list --format json` emits JSON Lines, not a JSON array

**Filed as [dora-rs/dora#2922](https://github.com/dora-rs/dora/issues/2922) (2026-07-31).**

**Versions:** dora-cli 1.0.0-rc.4 (git rev `7eb4a5f8b`).

`dora node list -d <df> --format json` prints one JSON object per line:

```
{"node":"budget-guard","status":"Running","pid":"54353",...}
{"node":"dora-genesis","status":"Running","pid":"54375",...}
```

Callers expecting `--format json` to mean "the output parses as one JSON document"
(a common convention) fail with a single `json.loads`. Ask: either document the JSONL
shape where the flag is described, or add `--format json-array` (or make `json` an array
and `jsonl` the line-oriented form). No strong preference — just make the contract
explicit; we now parse line-wise.
