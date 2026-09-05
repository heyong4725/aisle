# SPEC 440 monolithic-control records

| file | what it is |
|---|---|
| `treatment-table-baseline.json` | the pre-build fail-closed baseline (empty, blocked). Retained so the populated table cannot silently replace the gap record. |
| `treatment-table-v1.json` | generated MON-1 record (`aisle.monolithic-treatment.v1`) with per-row set digests of both arms' artifacts; `status: shakeout`, not frozen. Regenerate with `uv run harness monolith table --write`. |
| `experts-v1.json` | generated MON-9 provenance record: both experts, their component digests, `blind: false`, `frozen: false`. |
| `shakeout-01/` | engineering shakeout (MON-11 purpose `expert_parity`): typed `expert_t1.yaml` and the monolithic expert on T1 seeds 0..7, one episode each, teleport reset, oracle verifier, `--no-idea-gate`. `parity.json` is the `harness monolith parity` output; its gate is `blocked` by the MON-9 preconditions regardless of outcome. |

None of this is a parity result, a treatment effect, or experimental data:
both experts share an author, the seeds were chosen by that author, and the
spec is not CON-14 approved. Raw run directories (traces, dora logs) live
under `~/aisle-private/raw/mon-shakeout-01` and `~/aisle-private/raw/typed-shakeout-01`.

Validate a populated table with:

```bash
uv run harness monolith table
uv run pytest tests/unit/test_monolithic_treatment.py tests/unit/test_monolith_surface.py -q
```
