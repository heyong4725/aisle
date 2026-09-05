# Safety exposure ledger (SPEC 470, issue #351)

Instrument pilot for the exposure ledger and its analyzer. Evidence kind:
`simulation`. Nothing here is a physical-safety result, a prevention claim,
or a confirmatory campaign; it is the recorded exposure surface of one
expert graph on eight development seeds, regenerated from retained traces.

## Layers, stated separately (SFE-1, SFE-14)

- Declared topology: every declared command path traverses the guard
  (VAL-5, elsewhere).
- Gateway kinematic enforcement: `proposals.by_decision` counts pass, clamp,
  refuse, hold on received requests. A clamp alters a kinematically illegal
  proposal; it knows nothing about medicine identity.
- Observed kinematic outcome: `collisions` are a pose-displacement proxy
  from the verifier's own geometry; contact instrumentation is `unmeasured`.
- Verifier semantic detection: `wrong_object_events` are deliveries whose
  observed identity differs from the assigned target. They are detected
  outcomes after the fact, never prevented commands.

## Records

`records/sfe-exposure-pilot-01/`: `graphs/expert_t1.yaml`, tier T1, rung L1,
Franka, teleport reset, oracle verifier, seeds 0..7. `ledger.json.gz` is the
SFE-2 ledger (every proposal, decision, receipt, attempt, delivery,
collision-proxy row with hashes); `report.json` is the SFE-6 analyzer
output; `manifest.json` and `episodes.jsonl` are the run's own records. Raw
Arrow traces (23 MB) are retained outside the worktree under
`~/aisle-private/raw/sfe-exposure-pilot-01/`; their SHA-256 values are in
the ledger's `trace_hashes`.

## What the pilot measured

| quantity | value | unit |
|---|---|---|
| episodes randomized / started / included | 8 / 8 / 8 | episode |
| manipulation attempts (incomplete) | 8 (0) | attempt |
| deliveries | 8 | verifier-observed tray entry |
| wrong-object events | 0 | detected outcome |
| received / valid / malformed proposals | 14694 / 14694 / 0 | proposal |
| interventions (clamp / refuse / hold) | 0 / 0 / 0 | proposal |
| workspace: proposed out / intervened / received out / observed | 0 / 0 / 0 / 0 | proposal or event |
| collision proxy inside the scored window | 0 | episode |
| displacement first seen after the verdict | 3 | row, descriptive |
| controller class of every proposal | classical (by content hash) | SFE-7 |

Zero-event bounds (SFE-8, exact one-sided 95%, unit = included episode
with at least one event): wrong-object 0/8, upper bound 0.312; collision
proxy 0/8, upper bound 0.312. Delivery (0/8) and attempt (0/8) denominators
are reported descriptively beside them. Eight episodes bound nothing
tighter than about 31%; the ledger's purpose is the denominator, not a
small number.

## Regenerate

```bash
uv run harness exposure ledger --run runs/sfe-exposure-pilot-01 \
  --campaign-id sfe-exposure-pilot-01 \
  --source-map analysis/safety-exposure/source-map.json \
  --output analysis/safety-exposure/records/sfe-exposure-pilot-01/ledger.json.gz
uv run harness exposure analyze \
  --ledger analysis/safety-exposure/records/sfe-exposure-pilot-01/ledger.json.gz \
  --output analysis/safety-exposure/records/sfe-exposure-pilot-01/report.json
```

`source-map.json` classifies command producers by content hash (SFE-7). A
producer whose hash is not listed is `unknown` and stays visible.

## Not done here

The fixed-proposal guard ablation (SFE-9 to SFE-12) is registered under
`analysis/freeze/sfe-held-command-ablation-v1/` and not yet run. The
SFE-14 occurrence audit of claim wording is not yet mechanised. Hardware
ledgers are absent (`hardware_pending`, SFE-15).
