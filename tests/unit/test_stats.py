"""Unit tests for the replicate statistics helper (CON-5 layer (d) as
amended by ADR-26; M0-2). Reference p-values computed with
scipy.stats.fisher_exact (two-sided) and hardcoded here so the helper
stays dependency-free."""

import pytest

from aisle.harness.stats import fisher_exact_two_sided

pytestmark = pytest.mark.unit


def test_identical_counts_do_not_reject():
    """M0-2: identical success counts are maximally compatible."""
    assert fisher_exact_two_sided(46, 50, 46, 50) == pytest.approx(1.0)


def test_m0_scale_small_flip_passes():
    """M0-2: a few boundary flips at n=50 must not reject at p < 0.01 —
    scipy reference: fisher_exact([[46, 4], [50, 0]]) -> p ~= 0.11746."""
    p = fisher_exact_two_sided(46, 50, 50, 50)
    assert p == pytest.approx(0.117463, abs=1e-5)
    assert p >= 0.01


def test_m0_scale_large_divergence_rejects():
    """M0-2: a real rate difference rejects — scipy reference:
    fisher_exact([[40, 10], [50, 0]]) -> p ~= 0.0011868."""
    p = fisher_exact_two_sided(40, 50, 50, 50)
    assert p == pytest.approx(0.0011868, abs=1e-6)
    assert p < 0.01


def test_symmetry_and_bounds():
    """The test is symmetric in its two samples and always a probability."""
    a = fisher_exact_two_sided(3, 8, 7, 9)
    b = fisher_exact_two_sided(7, 9, 3, 8)
    assert a == pytest.approx(b)
    assert 0.0 <= a <= 1.0
    # scipy reference: fisher_exact([[3, 5], [7, 2]]) -> p ~= 0.153435
    assert a == pytest.approx(0.153435, abs=1e-5)


def test_degenerate_inputs_are_refused():
    """Malformed counts fail loudly, never return a quiet p-value."""
    with pytest.raises(ValueError):
        fisher_exact_two_sided(5, 4, 1, 4)  # successes > n
    with pytest.raises(ValueError):
        fisher_exact_two_sided(-1, 4, 1, 4)
    with pytest.raises(ValueError):
        fisher_exact_two_sided(0, 0, 1, 4)  # empty sample
