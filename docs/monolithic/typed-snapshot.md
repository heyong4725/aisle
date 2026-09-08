# Typed validation snapshots

`aisle.harness.typed_snapshot` captures the typed deliverable for validation
without importing authored implementations. It supports MON-2, MON-6, MON-12
and MON-13 and supplies a prerequisite for the matched-session work in #519.

`build_typed_validation_snapshot(controller_root, participant_root, output)`
reads the controller's typed editable allowlist, registry schemas, manifests
and referenced sources, then overlays the exact participant-editable files.
Authored manifests are copied verbatim so the ordinary validator retains its
normal diagnostics. They cannot request additional files from the controller.
Input reads traverse directory descriptors with symlink following disabled;
the final descriptor must refer to a regular file. Captured inputs are read
again before a receipt is issued to reject changes during collection.

The receipt records input origins, content hashes and snapshot file modes.
`verify_typed_validation_snapshot` checks the retained receipt and inventory.
`archive_typed_snapshot` copies those bytes into a fresh evidence directory,
retains the original snapshot identity and rechecks its source before returning.

The snapshot is data, not an execution authorization or an OS confinement
attestation. Its caller must bind the receipt to the admitted session, protect
the output and evidence directories, and confine any validator or worker.
Concurrent changes that occur and revert between observations are not ruled
out by content checks. Full session accounting and external study gates remain
tracked in #519.
