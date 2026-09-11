"""MON-12/MON-13: multi-session collections have separate bounded acquisition limits."""

import json

import pytest
from test_conformance_profile_binding import bound_profile

pytestmark = pytest.mark.unit


def test_disk_collection_rechecks_reads_without_retaining_all_payloads(tmp_path):
    """MON-13: disk-backed input acquisition must not return stale cached evidence."""
    from aisle.harness import frontend_conformance as profiles

    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    bound = profiles.read_bound_profile(
        root, reference, candidate=candidates["typed"], launch=launches["typed"], arm="typed"
    )
    assert not isinstance(bound["files"], dict)
    name = next(iter(bound["profile"]["fixture_files"]))
    original = (root / name).read_bytes()
    assert bound["files"][name] == original
    (root / name).write_bytes(original + b"changed")
    with pytest.raises(ValueError, match="drift"):
        bound["files"][name]


@pytest.mark.parametrize("overflow", [None, "collection", "file", "inventory"])
def test_collection_budget_does_not_replace_per_file_limit(tmp_path, monkeypatch, overflow):
    """MON-12/MON-13: a larger collection is readable, while each byte and inventory limit holds."""
    from aisle.harness import frontend_conformance as profiles

    root, candidates, launches, reference, _ = bound_profile(tmp_path)
    profile = json.loads((root / reference["path"]).read_bytes())
    names = set(profile["controller_files"]) | set(profile["fixture_files"])
    sizes = [(root / name).stat().st_size for name in names]
    total, largest = sum(sizes), max(sizes)
    assert total > largest
    monkeypatch.setattr(profiles, "MAX_INPUT_BYTES", largest - 1 if overflow == "file" else largest)
    monkeypatch.setattr(
        profiles,
        "MAX_COLLECTION_BYTES",
        total - 1 if overflow == "collection" else total,
        raising=False,
    )
    monkeypatch.setattr(
        profiles,
        "MAX_COLLECTION_FILES",
        len(names) - 1 if overflow == "inventory" else len(names),
        raising=False,
    )
    kwargs = dict(candidate=candidates["typed"], launch=launches["typed"], arm="typed")
    if overflow is None:
        bound = profiles.read_bound_profile(root, reference, **kwargs)
        assert set(bound["files"]) == names
        assert sum(map(len, bound["files"].values())) > profiles.MAX_INPUT_BYTES
    else:
        with pytest.raises(ValueError):
            profiles.read_bound_profile(root, reference, **kwargs)
        # The in-memory verifier must enforce the same limits as filesystem acquisition.
        with pytest.raises(ValueError):
            profiles.verify_profile_inputs(
                (root / reference["path"]).read_bytes(),
                {name: (root / name).read_bytes() for name in names},
                reference=reference,
                **kwargs,
            )
