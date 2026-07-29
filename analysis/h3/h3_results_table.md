| Arm | Tier | Held-out pass@1 | Session end | Tokens | Wall h | First success (min) | Tokens@1st | Precision fails (holdout) | Reuse | Flags |
|---|---|---|---|---|---|---|---|---|---|---|
| L | S1 | 0.500 | token_budget | 1000075 | 3.66 | 100.8 | 660989 | 0 | — | — |
| W | S1 | 0.375 | agent_done | 470584 | 5.24 | 146.7 | 164705 | 0 | — | — |
| W | S2 | 0.333 (partial) | token_budget | 757619 | 1.76 | 101.4 | 716177 | misplaced 1 | — | wipe_leak, holdout_partial |
| W | S3 | 0.000 | token_budget | 750074 | 2.08 | 48.9 | 536079 | wrong_slot 7 | — | wipe_leak |

Verdict (arm L S2+S3 time-to-first-success <= 0.5x arm W's (ADR-h3 §7)): pending
- caveat: W/S2: wipe_leak
- caveat: W/S2: holdout_partial
- caveat: W/S3: wipe_leak
