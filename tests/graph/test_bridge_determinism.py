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
EARLY_SPACING, LATE_SPACING = 40, 97
TAIL_S = 8.0  # wall capture time after the second reset
# CON-5 layer (c): the NORMATIVE comparison window is the first 1.0 s
# of sim time after each reset — enforced in full (a capture that does
# not cover the window is inadmissible: rerun, not compare). Beyond
# 1.0 s the comparison continues informatively at the same tolerance
# while this fixture graph stays contact-free.
NORMATIVE_WINDOW_NS = int(1.0e9)


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
        # Issue #94: anchor the capture tail to observed reset evidence,
        # not the driver's nominal wall schedule (which stretches under
        # suite load and used to race the recorder deadline).
        recorder_wait_for=("reset_done", 2),
        duration_s=TAIL_S,
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
        # the hold leaked (ADR-25 decision 1). Checked PER RUN (PR #88
        # re-review: an earlier edit left this on the loop's stale tail).
        first_reset_pos = next(i for i, r in enumerate(records) if r["id"] == "reset_done")
        leaked = [r["id"] for r in records[:first_reset_pos] if r["id"] in PHYSICS_TOPICS]
        assert not leaked, f"{name}: physics published before the first reset: {leaked}"

    # CON-5 layer (a) (ADR-26): the FIRST post-reset snapshot is
    # seed-derived and must be bit-identical across runs. The first reset
    # lands at sim 0 in both runs (gate above), so its oracle_state/poses
    # snapshot rows are exactly the stamp-0 rows — unambiguous here,
    # unlike the reset-2 boundary the windows exclude.
    for topic in ("oracle_state", "poses"):
        snaps = []
        for records in (early, late):
            rows = [
                r["sha256"]
                for r in records
                if r["id"] == topic and int(r["metadata"]["sim_time_ns"]) == 0
            ]
            assert rows, f"{topic}: no reset-1 snapshot at sim 0"
            snaps.append(rows[0])
        assert snaps[0] == snaps[1], (
            f"{topic}: first post-reset snapshot differs across runs (CON-5 layer (a))"
        )

    # both reset windows (seed 7 and seed 11) must match run-to-run. The
    # CON-5 layer (c) contract is enforced over the FULL normative window
    # (first 1.0 sim-s post-reset); a capture that does not cover it is
    # inadmissible. Shared stamps beyond the window are compared
    # informatively at the same tolerance but do not gate.
    early_windows, late_windows = _windows(early), _windows(late)
    beyond_window_diffs: list[str] = []
    for window_idx, seed in ((0, 7), (1, 11)):
        early_w = early_windows[window_idx]
        late_w = late_windows[window_idx]
        for topic in PHYSICS_TOPICS:
            shared = sorted(set(early_w[topic]) & set(late_w[topic]))
            assert shared, f"seed {seed} {topic}: no shared post-reset stamps"
            assert shared[-1] >= NORMATIVE_WINDOW_NS, (
                f"seed {seed} {topic}: shared coverage ends at "
                f"{shared[-1] / 1e9:.3f} sim-s — the capture does not cover the "
                "full 1.0 s normative window (CON-5 layer (c)): inadmissible, rerun"
            )
            normative = [t for t in shared if t <= NORMATIVE_WINDOW_NS]
            # density over the normative window: half nominal absorbs
            # jitter while proving a real contiguous stretch was compared
            floor = max(3, int(TOPIC_HZ[topic] * (NORMATIVE_WINDOW_NS / 1e9) * 0.5))
            assert len(normative) >= floor, (
                f"seed {seed} {topic}: only {len(normative)} shared stamps inside "
                f"the normative window, need {floor}"
            )
            # cadence is reset-anchored across the WHOLE shared span
            limit = shared[-1]
            assert {t for t in early_w[topic] if t <= limit} == {
                t for t in late_w[topic] if t <= limit
            }, f"seed {seed} {topic}: stamp grids differ — cadence not reset-anchored"
            for rel_ns in shared:
                a, b = early_w[topic][rel_ns], late_w[topic][rel_ns]
                # every desk-fixture physics payload is <= 64 elements, so
                # the recorder records values for ALL four topics — their
                # absence would silently void layer (c), so it fails loudly
                assert "values" in a and "values" in b, (
                    f"seed {seed} {topic}: recorder omitted values — layer (c) "
                    "cannot be checked; fix the capture, do not skip"
                )
                worst = max(abs(u - v) for u, v in zip(a["values"], b["values"], strict=True))
                if rel_ns <= NORMATIVE_WINDOW_NS:
                    assert worst < 1e-6, (
                        f"seed {seed} {topic} at t+{rel_ns} ns: diff {worst} exceeds "
                        "the GPU ULP-noise tolerance inside the normative window"
                    )
                elif worst >= 1e-6:
                    beyond_window_diffs.append(
                        f"seed {seed} {topic} t+{rel_ns / 1e9:.2f}s diff {worst:.2e}"
                    )
    if beyond_window_diffs:
        # informative only: divergence beyond the normative window is
        # layer (d)'s regime (chaos), recorded for the log
        print(f"beyond-window divergences (informative): {beyond_window_diffs[:10]}")
