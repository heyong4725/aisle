| Arm | Cell | Held-out pass@1 | Session end | Tokens | Wall h | First success (min) | Tokens@1st | Delivery fails | Placement fails | Reuse | Flags |
|---|---|---|---|---|---|---|---|---|---|---|---|
| L | T1 | 1.000 | agent_done | 165142 | 0.38 | 8.3 | 151110 | 0 | 0 | — | — |
| L | T2 | 0.500 | agent_done | 642730 | 3.11 | — | — | 0 | 0 | — | frozen_drift, treatment_drift |
| L | T2-r2 | 0.000 (partial) | token_budget | 801331 | 0.11 | — | — | 0 | 0 | — | holdout_partial |
| L | T3 | — (partial) | token_budget | 702685 | 1.22 | — | — | 0 | 0 | s3-driver-v1 | holdout_partial, treatment_drift |
| L | T3-r2 | — (partial) | token_budget | 751913 | 0.84 | — | — | 0 | 0 | s3-driver-v1 | holdout_partial |
| L | T4 | 1.000 | agent_done | 213988 | 0.69 | — | — | 0 | 0 | — | treatment_drift |
| L | T4-r2 | 1.000 | agent_done | 192712 | 0.35 | 14.9 | 161819 | 0 | 0 | — | — |
| W | T1 | 1.000 | agent_done | 231032 | 0.53 | 8.8 | 199001 | 0 | 0 | — | wipe_leak |
| W | T2 | 0.000 | agent_done | 660823 | 1.38 | 34.8 | 452974 | 0 | 0 | — | wipe_leak, treatment_drift |
| W | T3 | — (partial) | token_budget | 700212 | 0.67 | — | — | 0 | 0 | s3-driver-v1 | wipe_leak, holdout_partial |
| W | T4-r2 | 1.000 | agent_done | 172790 | 0.67 | 14.5 | 141708 | 0 | 0 | — | wipe_leak |

Verdict (arm L T2+T3+T4 time-to-first-success <= 0.5x arm W's (ADR-h3 §7)): pending
- caveat: L/T2: frozen_drift
- caveat: L/T2: treatment_drift
- caveat: L/T2-r2: holdout_partial
- caveat: L/T3: holdout_partial
- caveat: L/T3: treatment_drift
- caveat: L/T3-r2: holdout_partial
- caveat: L/T4: treatment_drift
- caveat: W/T1: wipe_leak
- caveat: W/T2: wipe_leak
- caveat: W/T2: treatment_drift
- caveat: W/T3: wipe_leak
- caveat: W/T3: holdout_partial
- caveat: W/T4-r2: wipe_leak
