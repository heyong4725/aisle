# Dora 1.0.1 validation

Runtime change: PR #496, base runtime commit `32bef41`. CLI and Python API
both report 1.0.1 on macOS arm64, CPython 3.13.15, Genesis 1.2.3.

The initial bridge acceptance run overlapped the unit suite and reported
`joint_state` at 12.463 Hz. Its retained recorder trace had 99 joint samples
and a single 6.789-second gap between sim stamps 30 ms and 40 ms, when the
first overhead render is due. The next-largest joint-state gap was 25.7 ms.
That points to startup/first-render cost rather than sustained transport
throughput, although cold-cache and contention effects were not separately
controlled in that first run.

An isolated 25-second free-run probe used the same conformance driver, seed 7,
all contract camera topics, and the recorder completion sentinel. It measured:

| Topic | Full-window wall Hz | After first cameras + 2 s, wall Hz | Sim Hz after that point |
|---|---:|---:|---:|
| joint_state | 98.12 | 98.78 | 100.00 |
| gripper_state | 98.12 | 98.78 | 100.00 |
| rgb_overhead | 29.74 | 29.63 | 30.00 |
| rgb_wrist | 29.74 | 29.63 | 30.00 |
| depth_overhead | 14.88 | 14.81 | 15.00 |
| oracle_state | 29.75 | 29.63 | 30.00 |
| poses | 14.88 | 14.81 | 15.00 |

The unchanged acceptance test was then rerun without concurrent unit tests:

```bash
uv run --extra sim --locked pytest tests/accept/test_contract.py::test_schema_conformance -q
# 1 passed in 311.45s
```

This is successful reproduction of the existing test on a warmed local host,
not proof that the cold-start failure is fixed, nor a nightly or attested
lockstep conformance claim. The legacy fixture runs in free-run mode, while
BRG-1 requires lockstep for acceptance; it also asserts TC-4's hardware
wall-clock band instead of the specified simulation-rate/liveness rules.
Per CON-13, those test changes are paused in
[spec-conflict #497](https://github.com/heyong4725/aisle/issues/497).

The remaining throughput work is to resolve the fixture migration, then
measure cold and warm starts separately with a capture boundary that includes
all required protocol events and enough simulated-time coverage. Preserve the
TC-4 0.5x wall liveness floor and the full simulated-time rate band. No threshold
was changed and no failing sample was reclassified as a pass.

## Issue #497: lockstep contract acceptance

The follow-up fixture on `fix/497-lockstep-acceptance` uses the production
turn barrier for A1–A3, with a turn-zero boot reset and turn-accounted reset
service / verifier replies. A1 now enforces TC-4's simulation-rate band and
wall-clock liveness floor over a complete ten simulated seconds after a fixed
one simulated second of startup (see
[the capture-window ADR](decisions/ADR-contract-acceptance-window.md)).
The recorder awaits a simulated-time horizon rather than assuming a wall
window or sample count implies sufficient coverage. A2 awaits every forwarded
reset reply, and A3 awaits the live-oracle-derived action result.

On macOS arm64 with Dora CLI/API 1.0.1, two consecutive four-case acceptance
runs passed (94.17 s and 80.08 s). A1 measured 88.50 / 87.98 Hz joint-state,
26.55 / 26.40 Hz RGB, and 13.28 / 13.20 Hz depth wall-clock; simulation rates
were exactly 100, 30, and 15 Hz over ten simulated seconds. The fixtures also
checked SO-101 schema, twenty resets with repeated-seed snapshot identity,
and the goal/feedback/result lifecycle. These are isolated local runs;
they do not establish cold-cache reliability or throughput under concurrent
simulation load. Synthetic regression traces separately prove that a delay
in the fixed startup interval does not shorten coverage, while the same delay
inside the measurement interval still fails the wall-clock floor.
