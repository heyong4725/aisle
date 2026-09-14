# L2 public observations and model cache

The `t1-l2-realistic` candidate uses `graphs/pilot_t1_l2_typed.yaml` and
`graphs/pilot_t1_l2_monolithic.yaml`. These graph copies preserve the realistic
verifier and the existing L2 implementations while projecting the policy's
goal, camera-calibration and reset inputs. The trusted evaluator receives the
full task record; policy nodes receive only the public fields described in
`interface-map-t1-l2.json`. Typed edits that bypass this boundary are refused.

Prepare an isolated cache from the pinned public identity-model snapshot:

```sh
uv run --locked python -m aisle.harness.pilot_model_cache \
  --root /absolute/controller/repository \
  --source-snapshot /absolute/public-cache/models--ORG--MODEL/snapshots/REVISION \
  --output /absolute/fresh-runtime-cache
```

The source snapshot revision and file digests must match `models.lock`. The
source cache must retain its `trees/REVISION.json` metadata. Only pinned model
files and the required public downloader metadata are copied; extra cache files
and credentials are excluded. Existing output paths are refused.

The JSON result names the files and returns `HF_HOME`, `HF_HUB_OFFLINE=1` and
`TRANSFORMERS_OFFLINE=1`. Bind that environment in the worker/controller contexts
that load the model before producing snapshots and admission records. Include
the prepared cache in their runtime receipts and read-only policies; do not
grant the whole operator cache. The typed node's declared environment must
match the prepared execution context through normal configuration validation.

This command prepares data only. It does not download models, supply a policy
grant, qualify an execution context, update a registration, or authorize study
collection. Use a fresh registration binding the updated candidate table and
controller bytes before collecting a pilot.
