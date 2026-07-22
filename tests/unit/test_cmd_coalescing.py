"""Unit tests for the bridge's pure control-plane logic (SPEC 030 BRG-1,
BRG-2, BRG-3, BRG-5, BRG-6) — sim mocked, no dora or genesis imports
(CON-12)."""

import json

import pytest

from aisle.nodes.dora_genesis import (
    CommandQueue,
    RateScheduler,
    ResetQuarantine,
    make_bridge_info,
    parse_bridge_config,
)

pytestmark = pytest.mark.unit


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
    """BRG-6: bridge_info carries exactly the contract fields as JSON."""
    info = json.loads(
        make_bridge_info(
            embodiment="franka",
            n_dof=9,
            n_envs=1,
            genesis_version="1.2.3",
            env_hash="a" * 64,
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
    }
    assert info["platform"]


def test_bridge_config_from_env():
    """BRG-1: bridge configuration (seed, embodiment, n_envs) comes from
    node environment variables with sane defaults."""
    cfg = parse_bridge_config({})
    assert (cfg.seed, cfg.embodiment, cfg.n_envs) == (0, "franka", 1)
    cfg = parse_bridge_config({"AISLE_SEED": "7", "AISLE_EMBODIMENT": "so101", "AISLE_N_ENVS": "4"})
    assert (cfg.seed, cfg.embodiment, cfg.n_envs) == (7, "so101", 4)


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
