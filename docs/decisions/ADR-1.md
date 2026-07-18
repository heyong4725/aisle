# ADR-1: trace_check scoping pre-M0 (HAR-9 interpretation)

HAR-9 says trace_check "exits nonzero listing uncovered MUSTs", but the
self-host test (`tests/unit/test_trace_check_selfhost.py`) must pass from T01
onward, while most specs are unimplemented until T02–T10. Interpretation
chosen (CON-15): an ID is MUST-bearing iff its requirement text (bullet plus
indented continuation lines, blank lines allowed inside a list item) contains
RFC 2119 MUST/REQUIRED/SHALL; uncovered MUSTs may be deferred in
`tools/trace_waivers.toml` (id + reason, reviewed like code, removed by the
implementing task's PR); `--strict` ignores waivers and `--specs NNN-MMM`
scopes the universe by spec number — the M0 gate (M0-4) runs
`--strict --specs 000-080`. Docstring tokens only count as citations when
they appear in a test callable's docstring (test_* functions/methods — module,
class, and helper docstrings are ignored, per PR 1 cross-review) and their
prefix matches a defined spec prefix (so SHA-256/UTF-8 prose is ignored);
citations or waivers of undefined IDs, duplicate IDs, unparseable test files,
and an empty specs/ are all hard errors, so coverage cannot be faked by typo
and the gate cannot silently go green. trace_check verifies traceability, not
test adequacy — whether a citing test meaningfully verifies its requirement
is what human review and CON-16 cross-review are for. Process/conduct rules (CON-10,
CON-11, CON-14, CON-15) are waived as human-review-enforced; whether proxy
tests are feasible is revisited at T10.
