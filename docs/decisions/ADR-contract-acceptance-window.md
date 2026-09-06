# ADR-contract-acceptance-window — Nominal-load contract capture

Status: Accepted (issue #497).

TC-4 defines rates under nominal load but does not define the startup boundary.
For TC-A1, measure a complete ten simulated seconds after a fixed one simulated
second of startup, separately reporting the wall time spent in that startup
interval. Keep all startup records and validate their schemas and turn metadata;
only rate measurement excludes that fixed interval. This lets lazy physics and
camera initialization finish without selecting a window based on observed speed.
Require every rate-bearing topic to cover the entire measurement window, enforce
its simulated-time ±20% band and wall-clock 0.5× floor unconditionally, and reject
missing sequence numbers. A later stall remains part of the measured window.
All TC-A1–A3 fixtures use BRG-1 lockstep, including the boot reset at turn zero.
The passive recorder waits for complete simulated coverage (A1), all twenty
forwarded reset replies (A2), or the live-oracle-derived action result (A3).
A separate 600-second process deadline bounds startup and protocol hangs; it is
never evidence of completion. No production threshold or specification changes.
