"""SPEC 080 BG-4: guard latency budget — p99 < 2 ms per command over 10k
messages, measured on the full clamp path including forward kinematics."""

import time

import numpy as np
import pytest

from aisle.nodes.budget_guard import clamp_joint_cmd, load_limits

pytestmark = pytest.mark.unit


def test_guard_p99_latency_under_2ms():
    """BG-4: <2 ms p99 per command on this machine, 10k varied commands
    (seeded, CON-5) through position + velocity + workspace checks."""
    limits = load_limits("franka")
    rng = np.random.default_rng(1)
    last = np.asarray(limits.fallback_qpos, dtype=np.float32)
    lo, hi = np.asarray(limits.q_min), np.asarray(limits.q_max)
    samples = []
    for _ in range(10_000):
        cmd = (last + rng.standard_normal(9) * 0.05).astype(np.float32)
        if rng.random() < 0.2:  # periodic gross violations keep FK search hot
            cmd = rng.uniform(lo - 1, hi + 1).astype(np.float32)
        t0 = time.perf_counter()
        safe, _ = clamp_joint_cmd(cmd, last, limits, timed_out=False)
        samples.append(time.perf_counter() - t0)
        last = safe
    p99 = float(np.quantile(np.asarray(samples), 0.99))
    assert p99 < 0.002, f"guard p99 latency {p99 * 1e3:.3f} ms >= 2 ms (BG-4)"
