# External review requests

Four gates in this program cannot be simulated, replayed, or self-attested.
Each request under `requests/` names the gate, the reviewer's required
independence, the exact artifacts with SHA-256, the reviewer's tasks, what
they sign, and what a signed record unblocks. A completed review lands as
`records/<request_id>.json` written by the reviewer; until then every
request is `open` and the gate stays pending.

| request | gate | unblocks |
|---|---|---|
| sta-12-statistical-review | STA-12 | protocol freeze and any confirmatory session |
| clm-12-terminology-review | CLM-12 | claim-evidence release readiness |
| rpr-10-external-reproduction | RPR-10, RPR-11 | reproduction claim and DOI archive |
| bmk-21-external-user | BMK-21 | benchmark v1 release audit |

Owner self-review, synthetic signatures, and admin merges satisfy none of
them. The maintainer may answer documented questions and must not edit a
reviewer's environment, record, or result in place.
