| Arm | Cell | Held-out pass@1 | Session end | Tokens | Wall h | First success (min) | Tokens@1st | Delivery fails | Placement fails | Reuse | Flags |
|---|---|---|---|---|---|---|---|---|---|---|---|
| L | S1 | 0.500 | token_budget | 1000075 | 3.66 | 100.8 | 660989 | 0 | 0 | — | — |
| L | S2 | 0.000 | token_budget | 750238 | 1.43 | — | — | 0 | wrong_slot 3 | — | treatment_drift |
| L | S3 | 0.000 | token_budget | 751798 | 0.98 | — | — | 0 | wrong_slot 3 | — | treatment_drift, residue_leak |
| L | S3-r2 | 0.000 | agent_done | 383794 | 1.01 | 23.2 | 322847 | 0 | wrong_slot 8 | — | treatment_drift, unattested_metric |
| L | S3-r3 | 0.000 | agent_done | 253776 | 1.19 | 29.9 | 168016 | 0 | wrong_slot 8 | — | runtime_drift |
| W | S1 | 0.375 | agent_done | 470584 | 5.24 | 146.7 | 164705 | 0 | 0 | — | — |
| W | S2 | 0.333 (partial) | token_budget | 757619 | 1.76 | 101.4 | 716177 | 0 | misplaced 1 | — | wipe_leak, holdout_partial, unattested_metric |
| W | S2-r2 | 0.000 (no deliverable) | token_budget | 751655 | 0.55 | — | — | 0 | 0 | — | — |
| W | S3 | 0.000 | token_budget | 750074 | 2.08 | 48.9 | 536079 | 0 | wrong_slot 7 | — | wipe_leak, unattested_metric |
| W | S3-r2 | 0.000 | token_budget | 750887 | 1.53 | — | — | 0 | wrong_slot 1 | — | — |

Verdict (arm L S2+S3 time-to-first-success <= 0.5x arm W's (ADR-h3 §7)): pending
- caveat: L/S2: treatment_drift
- caveat: L/S3: treatment_drift
- caveat: L/S3: residue_leak
- caveat: L/S3-r2: treatment_drift
- caveat: L/S3-r2: unattested_metric
- caveat: L/S3-r3: runtime_drift
- caveat: W/S2: wipe_leak
- caveat: W/S2: holdout_partial
- caveat: W/S2: unattested_metric
- caveat: W/S3: wipe_leak
- caveat: W/S3: unattested_metric
