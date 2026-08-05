"""Replicate statistics for CON-5 layer (d) (ADR-26): outcome
distributions are compared with exact tests, never per-seed equality.
Dependency-free on purpose — a verdict-adjacent helper must not float
with scipy versions (unit tests pin scipy-derived reference values)."""

from math import comb


def fisher_exact_two_sided(successes_a: int, n_a: int, successes_b: int, n_b: int) -> float:
    """Two-sided Fisher exact test p-value for two success counts.

    The 2x2 table is [[successes_a, n_a - successes_a],
    [successes_b, n_b - successes_b]]; under the null (equal rates) the
    first cell is hypergeometric with the table's margins fixed. The
    two-sided p sums every table whose probability does not exceed the
    observed one (the scipy.stats.fisher_exact convention; reference
    values pinned in tests/unit/test_stats.py)."""
    for successes, n in ((successes_a, n_a), (successes_b, n_b)):
        if n <= 0 or successes < 0 or successes > n:
            raise ValueError(f"malformed sample: {successes}/{n}")
    total_successes = successes_a + successes_b
    total = n_a + n_b

    def table_prob(x: int) -> float:
        return comb(n_a, x) * comb(n_b, total_successes - x) / comb(total, total_successes)

    lo = max(0, total_successes - n_b)
    hi = min(n_a, total_successes)
    observed = table_prob(successes_a)
    # 1 + 1e-12: float noise must not exclude tables tied with the observed
    return min(
        1.0, sum(p for x in range(lo, hi + 1) if (p := table_prob(x)) <= observed * (1 + 1e-12))
    )
