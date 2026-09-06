"""BMK-8/BMK-17: report measured process-tree memory with explicit gaps."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.unit


def test_tree_rss_counts_descendants_once_and_excludes_unrelated_processes():
    """BMK-17: sum RSS across the measured process tree, not the entire host."""
    from process_resources import tree_rss

    result = tree_rss("10 1 20\n11 10 30\n12 11 40\n99 1 500\n", 10)
    assert result == {"rss_bytes": 90 * 1024, "processes": 3}


@pytest.mark.parametrize("rows", ["", "11 1 30\n", "10 1 -2\n", "10 1 unknown\n"])
def test_missing_or_invalid_process_evidence_is_not_zero(rows):
    """BMK-8: unreadable or absent process evidence cannot create a zero-memory result."""
    from process_resources import tree_rss

    with pytest.raises(ValueError):
        tree_rss(rows, 10)


def test_sampler_retains_observed_peak_and_marks_it_as_sampled():
    """BMK-8/BMK-17: retain samples and disclose that gaps may hide a larger peak."""
    from process_resources import ResourceSampler

    readings = iter([{"rss_bytes": 100, "processes": 1}, {"rss_bytes": 200, "processes": 2}])
    sampler = ResourceSampler(observer=lambda: next(readings))
    sampler.sample()
    sampler.sample()
    report = sampler.report()
    assert report["status"] == "measured"
    assert report["sampled_peak_rss_bytes"] == 200
    assert report["samples"] == 2
    assert report["exact_peak"] is False


def test_sampler_failure_keeps_memory_unmeasured():
    """BMK-17: a failed observer must not silently publish an apparently complete peak."""
    from process_resources import ResourceSampler

    def fail():
        raise OSError("process inspection unavailable")

    sampler = ResourceSampler(observer=fail)
    sampler.sample()
    report = sampler.report()
    assert report["status"] == "unmeasured"
    assert report["sampled_peak_rss_bytes"] is None
    assert report["errors"]


@pytest.mark.parametrize("reading", [None, [], {"rss_bytes": float("nan"), "processes": 1}])
def test_malformed_observer_result_is_an_explicit_measurement_gap(reading):
    """BMK-17: malformed observations cannot silently stop the sampling thread."""
    from process_resources import ResourceSampler

    sampler = ResourceSampler(observer=lambda: reading)
    sampler.sample()
    assert sampler.report()["status"] == "unmeasured"
    assert sampler.report()["errors"]
