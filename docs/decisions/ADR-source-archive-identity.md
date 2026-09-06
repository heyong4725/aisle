# ADR-source-archive-identity — Portable Git content evidence

Status: PROPOSED — engineering interpretation; no release or collection approval.

For BMK-7/BMK-8/BMK-13, a development source archive may establish its Git revision by retaining the original commit/tree objects and verifying every committed file against their object hashes. The quickstart checks source identity before execution and again before submission. This avoids creating a synthetic checkout or accepting an arbitrary revision string. The derived revision is source provenance; publication authenticity and signed evaluator receipts are separate requirements. The record explicitly sets publisher_authenticated false. The ordinary missing-provenance refusal remains unchanged for archives without this evidence, and untracked/runtime files are not covered by this source proof.
