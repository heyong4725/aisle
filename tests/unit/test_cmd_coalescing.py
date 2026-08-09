"""Unit tests for the bridge's pure control-plane logic (SPEC 030 BRG-1,
BRG-2, BRG-3, BRG-5, BRG-6) — sim mocked, no dora or genesis imports
(CON-12)."""

import json

import numpy as np
import pytest

from aisle.nodes.dora_genesis import (
    CommandQueue,
    RateScheduler,
    ResetQuarantine,
    make_bridge_info,
    parse_bridge_config,
    rung_topic_rates,
    segmentation_id_map,
)

pytestmark = pytest.mark.unit

# the production wrist mount (SCN-5 `wrist_rotation_xyzw`, 180 deg about
# X): tests pass it explicitly because build_calibration_v1 requires it —
# a default would let a nominal block describe a different camera than the
# published one and stage 0 would refuse every episode (#110 review)
WRIST_MOUNT = np.diag([1.0, -1.0, -1.0])


def test_coalesce_keeps_latest_and_counts_dropped():
    """BRG-3: commands arriving faster than the tick are coalesced to the
    latest per (kind, env), with the number of superseded commands
    documented as dropped:int."""
    queue = CommandQueue(n_envs=1)
    queue.push("joint", 0, [0.1])
    queue.push("joint", 0, [0.2])
    queue.push("joint", 0, [0.3])
    assert queue.drain() == [("joint", 0, [0.3], 2)]
    assert queue.drain() == []  # drained until new pushes


def test_arrival_order_preserved_across_kinds():
    """BRG-1: pending inputs are serviced in ARRIVAL order — a joint_cmd
    arriving after a gripper_cmd is applied after it (the last-arrived
    command owns overlapping dofs), and vice versa."""
    queue = CommandQueue(n_envs=1)
    queue.push("gripper", 0, [0.5])
    queue.push("joint", 0, [0.1])
    assert [kind for kind, *_ in queue.drain()] == ["gripper", "joint"]
    queue.push("joint", 0, [0.2])
    queue.push("gripper", 0, [0.6])
    assert [kind for kind, *_ in queue.drain()] == ["joint", "gripper"]
    # a re-arrival moves the kind to the back of the order
    queue.push("gripper", 0, [0.7])
    queue.push("joint", 0, [0.3])
    queue.push("gripper", 0, [0.8])
    assert [(k, p) for k, _, p, _ in queue.drain()] == [("joint", [0.3]), ("gripper", [0.8])]


def test_coalesce_routes_per_env():
    """BRG-5: coalescing is per (kind, env) — commands for different envs
    never supersede each other."""
    queue = CommandQueue(n_envs=2)
    queue.push("joint", 0, [0.1])
    queue.push("joint", 1, [0.9])
    queue.push("joint", 1, [1.0])
    assert queue.drain() == [("joint", 0, [0.1], 0), ("joint", 1, [1.0], 1)]


def test_missing_env_id_in_multi_env_is_error():
    """BRG-5: a command without env_id in multi-env mode raises — it is an
    ERROR event, never a defaulted route."""
    queue = CommandQueue(n_envs=2)
    with pytest.raises(ValueError, match="env_id"):
        queue.push("joint", None, [0.1])


def test_out_of_range_env_id_is_error():
    """BRG-5: env_id outside [0, n_envs) is rejected up front — -1 must not
    silently route to the last environment."""
    queue = CommandQueue(n_envs=2)
    with pytest.raises(ValueError, match="outside"):
        queue.push("joint", -1, [0.1])
    with pytest.raises(ValueError, match="outside"):
        queue.push("joint", 2, [0.1])


def test_single_env_defaults_env_id_zero():
    """TC-2: in single-env mode env_id defaults to 0."""
    queue = CommandQueue(n_envs=1)
    queue.push("gripper", None, [0.5])
    assert queue.drain() == [("gripper", 0, [0.5], 0)]


def test_rate_scheduler_hits_contract_rates():
    """BRG-2, TC-4: with a 100 Hz tick, each topic fires at its declared
    contract rate (within the tick's granularity) and camera topics are due
    on only a subset of ticks — never all cameras every step."""
    rates = {"joint_state": 100, "oracle_state": 30, "rgb_overhead": 30, "depth_overhead": 15}
    scheduler = RateScheduler(rates, dt=0.01)
    fired = {topic: 0 for topic in rates}
    render_ticks = 0
    for _ in range(100):  # one simulated second
        due = scheduler.due()
        for topic in due:
            fired[topic] += 1
        if any(t.startswith(("rgb", "depth")) for t in due):
            render_ticks += 1
    assert fired["joint_state"] == 100
    assert fired["oracle_state"] == 30
    assert fired["rgb_overhead"] == 30
    assert fired["depth_overhead"] == 15
    assert render_ticks < 100  # BRG-2: not every tick renders


def test_rate_scheduler_is_deterministic():
    """CON-5: two schedulers with identical config produce identical due
    sequences."""
    rates = {"a": 30, "b": 15}
    first = RateScheduler(rates, dt=0.01)
    second = RateScheduler(rates, dt=0.01)
    sequence_a = [first.due() for _ in range(50)]
    sequence_b = [second.due() for _ in range(50)]
    assert sequence_a == sequence_b


def test_bridge_info_shape():
    """BRG-6 + BRG-8: bridge_info carries exactly the contract fields as
    JSON, including the VER-8 v1 `calibration` block (SPEC 040) built
    from the REALIZED camera state — the verifier's stage 0 refuses
    without it, so it is part of the exact shape."""
    from aisle.verifier.calibration import build_calibration_v1

    calibration = build_calibration_v1(
        [0.55, 0.0, 1.20],
        [0.55, 0.0, 0.20],
        (640, 480),
        55.0,
        [0.0, 0.0, 0.05],
        (320, 240),
        70.0,
        WRIST_MOUNT,
    )
    info = json.loads(
        make_bridge_info(
            embodiment="franka",
            n_dof=9,
            n_envs=1,
            genesis_version="1.2.3",
            env_hash="a" * 64,
            # required, not defaulted: a caller that forgot to wire the cfg
            # flag must fail loudly, not attest "off" while free-running
            step_without_reset=False,
            calibration=calibration,
        )
    )
    assert info == {
        "contract": "v0",
        "embodiment": "franka",
        "n_dof": 9,
        "n_envs": 1,
        "genesis_version": "1.2.3",
        "platform": info["platform"],  # host-dependent, non-empty
        "env_hash": "a" * 64,
        # ADR-25: bring-up mode leaves an attestation footprint in traces
        "step_without_reset": False,
        # BRG-8: nested, verbatim — the verifier compares it field-wise
        "calibration": calibration,
        # TC-9: the rung is attested in the TRACE, not only in the graph
        "perception": "L0",
        "segmentation_ids": {},
    }
    assert info["platform"]
    assert info["calibration"]["calibration_version"] == 1


def test_bridge_config_from_env():
    """BRG-1: bridge configuration (seed, embodiment, n_envs) comes from
    node environment variables with sane defaults."""
    cfg = parse_bridge_config({})
    assert (cfg.seed, cfg.embodiment, cfg.n_envs) == (0, "franka", 1)
    cfg = parse_bridge_config({"AISLE_SEED": "7", "AISLE_EMBODIMENT": "so101", "AISLE_N_ENVS": "4"})
    assert (cfg.seed, cfg.embodiment, cfg.n_envs) == (7, "so101", 4)


def test_step_without_reset_defaults_off():
    """CON-5/ADR-25 (issue #71): the bridge holds at sim step 0 until the
    first reset by DEFAULT — the pre-reset free-run that made two attested
    expert_s1 runs diverge is bring-up-only, opted into explicitly."""
    assert parse_bridge_config({}).step_without_reset is False
    assert parse_bridge_config({"AISLE_STEP_WITHOUT_RESET": "1"}).step_without_reset is True
    assert parse_bridge_config({"AISLE_STEP_WITHOUT_RESET": "0"}).step_without_reset is False


def test_mobile_rejects_batched_envs():
    """SPEC 210 MOB-1/ADR-13 (PR #14 review): the kinematic base is single-
    env per bridge; mobile with n_envs > 1 is rejected at startup rather
    than mislabel one global base_pose under every env. Fixed-base profiles
    and single-env mobile pass."""
    from aisle.nodes.dora_genesis import require_single_env_for_mobile

    with pytest.raises(ValueError, match="batched envs"):
        require_single_env_for_mobile("mobile", 4)
    require_single_env_for_mobile("mobile", 1)  # ok
    require_single_env_for_mobile("franka", 8)  # fixed-base batching unaffected


def test_non_integral_env_id_is_error():
    """BRG-5: fractional and boolean env_id values are rejected, never
    silently coerced into a route (0.7 must not become env 0)."""
    queue = CommandQueue(n_envs=2)
    for bad in (0.7, 1.0, True, "1"):
        with pytest.raises(ValueError, match="env_id"):
            queue.push("joint", bad, [0.1])


def test_reset_clock_is_injected():
    """CON-5: the bridge's reset timing uses an injected clock — the main
    entrypoint takes it as a parameter defaulting to time.perf_counter,
    never calling a wall clock ad hoc inside the loop."""
    import inspect
    import time as time_module

    from aisle.nodes.dora_genesis import main

    parameter = inspect.signature(main).parameters["clock"]
    assert parameter.default is time_module.perf_counter


def test_reset_quarantine_holds_then_releases():
    """BRG-4: after arm() the quarantine reports active for exactly ticks
    holds (one consumed per tick), then releases so normal command
    application resumes — the window that drops the ended episode's stale
    joint_cmds so they cannot drive the just-homed arm off home."""
    q = ResetQuarantine(3)
    assert q.hold() is False  # not armed: commands apply normally
    q.arm()
    assert [q.hold() for _ in range(4)] == [True, True, True, False]
    # re-arming restarts the full window (a second reset mid-window)
    q.arm()
    assert q.hold() is True
    q.arm()
    assert sum(q.hold() for _ in range(5)) == 3  # exactly `ticks` holds


def test_reset_quarantine_zero_ticks_never_holds():
    """A zero-tick quarantine (settle disabled) never quarantines."""
    q = ResetQuarantine(0)
    q.arm()
    assert q.hold() is False


def test_perception_rung_from_env_defaults_l0():
    """TC-9: the rung comes from the graph node's env, defaults to L0, and is
    read case-insensitively. An UNRECOGNIZED rung refuses rather than falling
    back to L0 — a silent fallback would publish ground-truth pose to a graph
    that asked not to have it and report the result under the rung it typo'd."""
    assert parse_bridge_config({}).perception == "L0"
    assert parse_bridge_config({"AISLE_PERCEPTION": "l1"}).perception == "L1"
    assert parse_bridge_config({"AISLE_PERCEPTION": " L2 "}).perception == "L2"
    with pytest.raises(ValueError, match="unknown perception rung"):
        parse_bridge_config({"AISLE_PERCEPTION": "L3"})


def test_bridge_publishes_only_what_the_rung_permits():
    """TC-9: the bridge does not PUBLISH a topic the rung forbids — the other
    half of VAL-8, which only rejects a graph that consumes one. The validator
    can be bypassed (instrumented run copies, hand-edited graphs); a topic that
    is never on the wire cannot be consumed by accident."""
    l0 = rung_topic_rates("L0", is_mobile=False)
    assert "poses" in l0 and "seg_overhead" not in l0
    l1 = rung_topic_rates("L1", is_mobile=False)
    assert "poses" not in l1 and l1["seg_overhead"] == 15
    l2 = rung_topic_rates("L2", is_mobile=False)
    assert "poses" not in l2 and "seg_overhead" not in l2
    # the rung is orthogonal to embodiment: mobile still adds its base topics
    assert {"base_pose", "base_scan"} <= set(rung_topic_rates("L1", is_mobile=True))
    # and the rung never disturbs the topics it says nothing about
    for rung in ("L0", "L1", "L2"):
        assert {"rgb_overhead", "depth_overhead", "joint_state", "oracle_state"} <= set(
            rung_topic_rates(rung, is_mobile=False)
        )


def test_segmentation_and_depth_are_co_scheduled_on_every_tick():
    """TC-9, CON-5: `seg_overhead` and `depth_overhead` MUST be due on the
    same ticks, because an L1 estimate masks the segmentation and indexes the
    depth. If they could fire independently the estimator would either pair
    across ticks (measuring a scene that never existed) or wait forever."""
    scheduler = RateScheduler(rung_topic_rates("L1", is_mobile=False), dt=0.01)
    seg_ticks, depth_ticks = [], []
    for tick in range(1000):
        due = scheduler.due()
        if "seg_overhead" in due:
            seg_ticks.append(tick)
        if "depth_overhead" in due:
            depth_ticks.append(tick)
    assert seg_ticks == depth_ticks
    assert len(seg_ticks) == 150  # 15 Hz over 10 s (TC-4)


def test_segmentation_id_map_uses_the_scene_map_not_entity_indices():
    """TC-9: the ids in `seg_overhead` are genesis's own segmentation map, NOT
    entity indices. These numbers are MEASURED from the desk scene (seed 3):
    entity 5 is amoxicillin and its seg id is 16. A consumer that masked on
    the entity index would select other geometry — observed as robot links
    whose pixel counts were identical across different object layouts."""
    idx_dict = {0: -1, 1: (0, 0), 5: (4, 4), 6: (4, 5), 16: (5, 16), 17: (6, 17)}
    ids = segmentation_id_map(idx_dict, {"amoxicillin": 5, "ibuprofen": 6})
    assert ids == {"amoxicillin": [16], "ibuprofen": [17]}
    # a multi-link entity contributes every one of its ids, sorted (CON-5)
    assert segmentation_id_map({7: (4, 7), 5: (4, 4)}, {"robot": 4}) == {"robot": [5, 7]}
    # an entity with no rendered geometry maps to an empty list, never a guess
    assert segmentation_id_map(idx_dict, {"tray": 99}) == {"tray": []}


def test_reset_path_publishes_only_what_the_rung_permits():
    """TC-9: the reset path publishes DIRECTLY, off the scheduler, so gating
    only the scheduler is not enough. `publish` gates on the rung's topic set
    itself, which is what makes every direct call safe by construction.

    Before this, `publish("poses")` after each reset put ground-truth pose on
    an L1 wire once per episode — at the freshest possible moment, and into the
    trace the recorder keeps. `rung_topic_rates` was correct and the bug was
    entirely in the path that bypassed it, which is why the rung test above
    passed while the bridge leaked."""
    from aisle.nodes.dora_genesis import RESET_PUBLISH

    # oracle_state is verifier-only at every rung (ADR-26) and stays
    assert "oracle_state" in RESET_PUBLISH
    assert "oracle_state" in rung_topic_rates("L1", is_mobile=False)
    # `poses` is published on the reset path at L0 and must be filtered out
    # above it — the gate is membership in the rung's own topic set
    assert "poses" in RESET_PUBLISH
    permitted = {rung: set(rung_topic_rates(rung, is_mobile=False)) for rung in ("L0", "L1", "L2")}
    assert {t for t in RESET_PUBLISH if t in permitted["L0"]} == {"oracle_state", "poses"}
    assert {t for t in RESET_PUBLISH if t in permitted["L1"]} == {"oracle_state"}
    assert {t for t in RESET_PUBLISH if t in permitted["L2"]} == {"oracle_state"}


def test_segmentation_id_map_handles_every_genesis_segmentation_level():
    """TC-9: the seg_key shape depends on `VisOptions.segmentation_level`, read
    from genesis's own construction — `geom` -> (entity, link, geom), `link`
    (the default) -> (entity, link), `entity` -> a BARE int. The entity index
    is first in every shape, so all three resolve.

    An earlier version required a tuple, so at `segmentation_level="entity"`
    every object mapped to an empty id list, the L1 estimator refused every
    pose, and the episode died on a timeout with one stderr line as the clue.
    A silent downgrade to "refuse everything" is the worst of the three."""
    entity_idx = {"amoxicillin": 5}
    # link level (genesis default), as measured on the desk scene
    assert segmentation_id_map({0: -1, 16: (5, 16)}, entity_idx) == {"amoxicillin": [16]}
    # geom level
    assert segmentation_id_map({0: -1, 16: (5, 16, 2)}, entity_idx) == {"amoxicillin": [16]}
    # entity level: a bare int, not a tuple
    assert segmentation_id_map({0: -1, 16: 5}, entity_idx) == {"amoxicillin": [16]}
    # background (-1) is never an entity, at any level
    assert segmentation_id_map({0: -1}, {"bg": -1}) == {"bg": []}
    # numpy integers are ints too: genesis hands back its own scalar types
    assert segmentation_id_map({16: np.int64(5)}, entity_idx) == {"amoxicillin": [16]}


def test_bridge_info_carries_the_l1_id_map():
    """TC-9/BRG-6: at L1 the id map travels in bridge_info, so a consumer never
    derives genesis's numbering. Pinned separately from the L0 shape test
    because the L1 path through the JSON was otherwise unexercised."""
    info = json.loads(
        make_bridge_info(
            embodiment="franka",
            n_dof=9,
            n_envs=1,
            genesis_version="1.2.3",
            env_hash="a" * 64,
            step_without_reset=False,
            calibration={"calibration_version": 1},
            perception="L1",
            segmentation_ids={"amoxicillin": [16], "ibuprofen": [17]},
        )
    )
    assert info["perception"] == "L1"
    assert info["segmentation_ids"] == {"amoxicillin": [16], "ibuprofen": [17]}
