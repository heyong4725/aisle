# MON-1 treatment-table baseline

This directory retains the current fail-closed baseline for SPEC 440 MON-1.
The table is intentionally empty because neither arm's frozen surface artifacts
exist yet. It is blocked evidence, not a parity result and not experimental data.

Validate a populated table with:

```bash
uv run pytest tests/unit/test_monolithic_treatment.py -q
```
