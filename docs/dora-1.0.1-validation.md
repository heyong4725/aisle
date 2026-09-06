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
