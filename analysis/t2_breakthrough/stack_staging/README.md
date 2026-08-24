# Lane-0 breakthrough stack — staged for registration (step 2, in progress)

The 0.375-scoring stack verbatim from
runs/t2_breakthrough_r3/fleet_4/worktree_0 (2026-08-23): the
lockstep-ported t2-scan skills + manifests, the lane's deliverable
graph (content-pinned here per the era-fragility lesson), and the
46-line far-first read-ladder executor diff the skills depend on.

Registration plan (pre-registered BEFORE any suite run):
- eval graph = lane0_deliverable.yaml content, staged as
  graphs/eval_t2_stack.yaml; n=8 suite on dev seeds 8..15 (holdout
  100..107 stays scoring-only); min_pass_rate 0.5 (ADR-37); run ONCE,
  evalcard = measured rate, park with record if under floor.
- The executor diff rides the same PR as a REVIEWED change: it breaks
  test_far_side_faces_lead_with_pitched_entries (the reordered ladder's
  entry set differs from stock) — reconcile by flag-scoping the
  reorder (graph-attested env) or updating the test with measured
  justification. Never merge with a red test, never delete the pin
  silently.
