"""Graph test for reset-anchored startup determinism (CON-5, BRG-1, ADR-25).

Issue #71: two attested expert_s1 runs at one pin diverged because the
bridge's first physics step raced the first reset request — one run
stepped once before its reset landed (reset_done at sim 0.01 s), the
other did not (sim 0.00 s), and everything downstream diverged from the
one-tick phase shift. The trace pair proved post-reset physics is a pure
function of the seed: the two joint_state streams were bit-identical
modulo the offset.

This test pins the fix: the bridge MUST NOT step physics before the
first reset, and each reset MUST re-anchor the publish-cadence grid —
both independent of the wall-clock arrival time of the requests. State
values are compared with a GPU ULP-noise tolerance (see the test
docstring: the Metal backend is not bit-deterministic).
Marker `graph`: launches a dora dataflow twice.
"""

import importlib.util
import shutil

import pytest

pytestmark = [
    pytest.mark.graph,
    pytest.mark.skipif(
        importlib.util.find_spec("genesis") is None or shutil.which("dora") is None,
        reason="sim extra or dora CLI not installed",
    ),
]

# physics-truth topics only: render topics go through the GPU and get no
# bit-exactness contract from CON-5 (the verifier never reads them)
PHYSICS_TOPICS = ("joint_state", "gripper_state", "oracle_state", "poses")
# contract rates (TC table): used for a DENSITY check over the observed
# sim span — wall-based floors are wrong under load, where rtf collapses
# and a wall window holds far fewer sim ticks than nominal (first CI run
# of this test: 0.4 s wall of seed-7 window held 0.04 s of sim)
TOPIC_HZ = {"joint_state": 100, "gripper_state": 100, "oracle_state": 30, "poses": 15}
DRIVER_TICK_S = 0.05  # conftest wires the driver to dora/timer/millis/50
EARLY_SPACING, LATE_SPACING = 40, 97
TAIL_S = 8.0  # wall capture time after the second reset
# a window must span at least this much SIM time to prove anything;
# below it the machine is too loaded for a meaningful run (rtf < ~0.05)
MIN_SPAN_NS = {0: int(0.1e9), 1: int(0.4e9)}


def _run_pair_member(tmp_path, dataflow, name: str, spacing_ticks: int) -> list[dict]:
    """One bridge run whose driver issues reset(seed=7) then reset(seed=11),
    `spacing_ticks` driver ticks apart, counted from bridge_info so the
    requests arrive while the loop is live (not queued behind the genesis
    build). Returns the recorder's rows in file (arrival) order."""
    run_dir = tmp_path / name
    run_dir.mkdir()
    rec = run_dir / "records.jsonl"
    graph = dataflow.write(
        run_dir,
        rec,
        driver_env={
            "DRIVER_MODE": "reset",
            "DRIVER_RESET_SEEDS": "7,11",
            "DRIVER_RESET_SPACING": spacing_ticks,
        },
        driver_waits_for_bridge_info=True,
        step_without_reset=False,  # the production startup path IS the test
        duration_s=TAIL_S + spacing_ticks * DRIVER_TICK_S * 2,
    )
    # sequential on purpose: concurrent dataflows would need port-isolated
    # dora coordinators and contend for the GPU during the genesis builds
    dataflow.run_until_settled(graph, rec, deadline_s=420)
    return dataflow.read(rec)


def _windows(records: list[dict]) -> list[dict[str, dict[int, dict]]]:
    """Per reset: {topic: {sim_ns relative to that reset: record}} for every
    physics record between that reset_done and the next. Keyed by the
    bridge's own sim stamps (not recorder arrival), so cross-channel
    reordering and a dropped row cannot misalign the comparison.

    Both bounds are EXCLUSIVE: at a reset's own stamp the bridge publishes
    that tick's old-episode rows AND the new episode's post-reset snapshot
    (oracle_state/poses) with the same stamp — since the reset lands at a
    different stamp in each run, those boundary rows can never align
    across runs and belong to neither window's comparison."""
    resets = [r for r in records if r["id"] == "reset_done"]
    assert len(resets) == 2, f"expected 2 reset_done, got {len(resets)}"
    bounds = [int(r["metadata"]["sim_time_ns"]) for r in resets] + [None]
    out = []
    for start, end in zip(bounds, bounds[1:], strict=False):
        window: dict[str, dict[int, dict]] = {t: {} for t in PHYSICS_TOPICS}
        for r in records:
            if r["id"] not in PHYSICS_TOPICS:
                continue
            t_ns = int(r["metadata"]["sim_time_ns"])
            if t_ns > start and (end is None or t_ns < end):
                window[r["id"]][t_ns - start] = r
        out.append(window)
    return out


def test_observation_stream_is_reset_anchored(tmp_path, dataflow):
    """CON-5/BRG-1/ADR-25: two runs whose reset requests arrive at different
    live wall times (40 vs 97 driver ticks after the build) hold the world
    at sim step 0 until the first reset, and produce a BIT-identical physics
    stream for episode 0 (same payload hash at every reset-relative sim
    stamp) — a cold process's first episode is a pure function of
    (env, graph, seed).

    State values get the guarantee Genesis on Metal actually provides:
    equality within tolerance, NOT bit-exactness. Iterating this test
    measured occasional single-ULP joint_state flips between two COLD
    runs of the same seed (joint 0: -1.5411730203e-7 vs -1.5411731624e-7
    rad at step 7 in one pair; a different pair diverged one ULP only
    at step 74 after the second reset; the first pair ran 4 s
    bit-identical) — GPU parallel-reduction ordering is not
    deterministic, so bit-exact CON-5 is unattainable on this backend
    and attested pairs drift by chaos-amplified ULP noise over minutes
    (issue #71). What IS exact, and what this test pins bit-hard: the
    reset anchoring itself (first reset at sim 0, no pre-reset physics)
    and the publish-cadence stamp grids."""
    early = _run_pair_member(tmp_path, dataflow, "early", spacing_ticks=EARLY_SPACING)
    late = _run_pair_member(tmp_path, dataflow, "late", spacing_ticks=LATE_SPACING)

    for name, records in (("early", early), ("late", late)):
        resets = [r for r in records if r["id"] == "reset_done"]
        first_ns = int(resets[0]["metadata"]["sim_time_ns"])
        assert first_ns == 0, (
            f"{name}: bridge stepped physics before the first reset "
            f"(reset_done at sim {first_ns} ns)"
        )
        # nothing but the BRG-6 startup announcement may precede the first
        # reset in the recorder's file order — a pre-reset physics row means
        # the hold leaked (ADR-25 decision 1)
        first_reset_pos = next(i for i, r in enumerate(records) if r["id"] == "reset_done")
        leaked = [r["id"] for r in records[:first_reset_pos] if r["id"] in PHYSICS_TOPICS]
        assert not leaked, f"{name}: physics published before the first reset: {leaked}"

    # both reset windows (seed 7 and seed 11) must match run-to-run on the
    # overlap of reset-relative stamps; the early run's seed-7 window is
    # shorter (its second reset lands sooner), which caps the overlap
    early_windows, late_windows = _windows(early), _windows(late)
    for window_idx, seed in ((0, 7), (1, 11)):
        early_w = early_windows[window_idx]
        late_w = late_windows[window_idx]
        for topic in PHYSICS_TOPICS:
            shared = sorted(set(early_w[topic]) & set(late_w[topic]))
            assert shared, f"seed {seed} {topic}: no shared post-reset stamps"
            span_ns = shared[-1] - shared[0]
            assert span_ns >= MIN_SPAN_NS[window_idx], (
                f"seed {seed} {topic}: shared window spans only {span_ns / 1e9:.3f} sim-s "
                f"(need {MIN_SPAN_NS[window_idx] / 1e9:.1f}) — machine too loaded for a "
                "meaningful comparison, rerun on an idler box"
            )
            # density over the OBSERVED span: half nominal absorbs jitter
            # while proving a real contiguous stretch was compared
            floor = max(3, int(TOPIC_HZ[topic] * span_ns / 1e9 * 0.5))
            assert len(shared) >= floor, (
                f"seed {seed} {topic}: only {len(shared)} shared stamps across "
                f"{span_ns / 1e9:.3f} sim-s, need {floor}"
            )
            # cadence is reset-anchored in BOTH windows: over the shared
            # span the two runs must publish on the identical stamp grid
            limit = shared[-1]
            assert {t for t in early_w[topic] if t <= limit} == {
                t for t in late_w[topic] if t <= limit
            }, f"seed {seed} {topic}: stamp grids differ — cadence not reset-anchored"
            for rel_ns in shared:
                a, b = early_w[topic][rel_ns], late_w[topic][rel_ns]
                # GPU ULP-noise tolerance (see docstring); oracle_state and
                # poses exceed the recorder's 64-element value cutoff, so
                # they are covered by the cadence assertions above only
                if "values" in a and "values" in b:
                    for j, (u, v) in enumerate(zip(a["values"], b["values"], strict=True)):
                        assert abs(u - v) < 1e-6, (
                            f"seed {seed} {topic}[{j}] at t+{rel_ns} ns: "
                            f"{u} vs {v} exceeds the GPU ULP-noise tolerance"
                        )
