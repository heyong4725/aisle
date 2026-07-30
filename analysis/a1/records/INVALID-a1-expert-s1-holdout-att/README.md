# INVALID — evidence-only record (PR #72 review)

This run is NOT a valid A1 cell and establishes nothing about CON-5:
it executed from the live repo tree after a branch switch (pre-ADR-24
code at `05f7598`; no `env_fingerprint`, `env_attested: null`), so its
environment tuple is unmatched against every other run. Committed
solely so the unexplained 0/8 observation that motivates issue #71 is
auditable. The determinism question is answerable only by back-to-back
attested reruns with identical tuples (issue #71's proposed protocol).
