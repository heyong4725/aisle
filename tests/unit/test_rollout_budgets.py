def test_per_episode_wall_override_replaces_only_the_wall_budget():
    """Lockstep VLA eval (ADR-38 amendment): with inference inside the
    turn, wall per episode scales with model latency while SIM budgets
    are unchanged — the override touches only the wall number."""
    from aisle.harness.rollout import resolve_budgets

    sim_s, wall_s = resolve_budgets("T0", "oracle")
    sim2, wall2 = resolve_budgets("T0", "oracle", per_episode_wall_override_s=5400)
    assert sim2 == sim_s and wall2 == 5400
    assert resolve_budgets("T0", "oracle", per_episode_wall_override_s=None) == (sim_s, wall_s)
