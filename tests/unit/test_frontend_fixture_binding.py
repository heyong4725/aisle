"""MON-12/MON-13: retain and compare selected frontend fixture inputs."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.unit


@pytest.mark.parametrize("mutation", ["edit", "delete", "symlink", "create_absent"])
def test_fixture_drift_is_reported(tmp_path, mutation):
    """MON-13: changed, missing and substituted inputs invalidate the probe."""
    import frontend_codex_probe as probe

    path = tmp_path / "settings.json"
    path.write_text("{}")
    expected = probe._fixture_snapshot(tmp_path, ["settings.json"], ["hook.py"])
    assert probe._fixture_errors(tmp_path, expected) == []
    if mutation == "edit":
        path.write_text('{"changed":true}')
    elif mutation == "delete":
        path.unlink()
    elif mutation == "symlink":
        path.rename(tmp_path / "target")
        path.symlink_to(tmp_path / "target")
    else:
        (tmp_path / "hook.py").write_text("pass")
    assert probe._fixture_errors(tmp_path, expected)


def test_missing_required_fixture_is_not_recorded_as_absent(tmp_path):
    """MON-13: only explicitly absent fixture inputs may be absent."""
    import frontend_codex_probe as probe

    with pytest.raises((OSError, ValueError)):
        probe._fixture_snapshot(tmp_path, ["settings.json"], [])


def test_unexpected_present_fixture_is_refused(tmp_path):
    """MON-13: absence is a checked fixture property."""
    import frontend_codex_probe as probe

    (tmp_path / "hook.py").write_text("pass")
    with pytest.raises(ValueError):
        probe._fixture_snapshot(tmp_path, [], ["hook.py"])
