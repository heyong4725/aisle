# Bound typed validation

`aisle.harness.typed_validation` runs the existing validator against a captured
typed deliverable without placing authored code on the Python import path.
It supports MON-2, MON-6, MON-8, MON-12 and MON-13 as a prerequisite for #519.

`build_validation_bundle` copies the controller's validator import closure.
Verification compares both the receipt and the bundle inventory to the current
controller sources. `validation_command` builds an isolated Python command
against the snapshot from `typed_snapshot`; constructing this command alone
does not authorize execution.

`run_validation` additionally requires a bound runtime, interpreter hash,
private HOME, exact code/snapshot read grants, denied external networking,
protected source roots and a matching verified adapter profile. It checks those
inputs before launch, retains stdout, stderr and launch records, and rechecks
immutable inputs after the child exits. Ordinary validation failure remains a
tool result; drift, malformed output and mismatched exit status remain
infrastructure exclusions. Timeout and cancellation reap the owned child and
retain its terminal record. This is process-group cleanup, not exhaustive
descendant containment.

`verify_validation_binding` checks the reusable declaration and rejects overlap
between validator assets and participant authority. Session admission must still
bind the declaration and reserve fresh snapshot storage. That integration,
full-session accounting and independent study prerequisites remain in #519.

The standalone tests exercise real validator subprocesses using an explicitly
synthetic adapter. They demonstrate launch wiring and refusal behavior, not
independent OS confinement or permission to collect study results.
