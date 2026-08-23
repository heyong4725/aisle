# T4 increment two: first successful post-delivery recovery (2026-08-23)

Run t4-inc2-recovery-r2 (graph-attested AISLE_T4_INCREMENT_TWO, seeds
0,4,8): **ep-0000r succeeded** — the full ADR-32 §3 loop executed for
the first time: A misdelivered, "that's the wrong one — take it back",
the return-planner's tray-pick placed A at a shelf slot through the
standard guard-gated stack (place_xy plan redirect), B delivered, and
the amended judge scored it success under expects_return (13.7 sim-s).
The `return_item` mechanism, the recovery goal chain, the step-3
script, and the VER amendments all work end to end. wrong_object 0.

No-regression (same graph, toggle off): 3/3, corrections exact —
inc-1 behavior byte-preserved.

Two client-side defects measured, both mine, both open:
1. **Goal-1 script/goal mismatch in inc-2 mode**: the client sets
   goal-1's target to A (the misdelivery), but the human-sim's
   non-recovery script still CORRECTS to B on corrected seeds — the
   machine then works toward B against an A-goal (seeds 0/4 goal-1
   `collision` at ~6 s vs 3/3 success in inc-1 mode minutes earlier).
   Fix: the inc-2 goal-1 episode_meta must suppress the correction
   (`inc2_goal1: true` → the human insists on A; the correction
   arrives POST-delivery as designed).
2. **Record-count termination**: recovery records count toward
   `len(seeds)` so the run ended after 3 records, leaving seed 4's
   recovery and seed 8 unrun. Fix: count only non-recovery records.
