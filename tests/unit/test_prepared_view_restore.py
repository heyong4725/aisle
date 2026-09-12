"""MON-13: prepared matrix cases restore bound inputs and retain consumed views."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "accept"))
pytestmark = pytest.mark.unit


def setup_views(base):
    for name in ("controller", "runtime", "retained", "typed", "monolithic"):
        (base / name).mkdir()
    views = {arm: base / arm for arm in ("typed", "monolithic")}
    for arm, view in views.items():
        (view / "source").write_text(arm)
        (view / "source").chmod(0o640)
        (view / "empty").mkdir()
    return {
        "root": base / "controller",
        "views": views,
        "tool_runtime": {"trees": {str(base / "runtime"): {}}},
    }


def test_restored_case_keeps_original_inputs_and_archives_all_changes(tmp_path):
    """MON-13: each case starts from the captured bytes, modes and directory inventory."""
    from conformance_run_fixture import capture_run_views, restore_run_views

    setup = setup_views(tmp_path)
    baseline = capture_run_views(tmp_path, setup, tmp_path / "retained/pristine")
    for view in setup["views"].values():
        (view / "source").write_text("case edit")
        (view / "source").chmod(0o600)
        (view / "extra").write_text("case output")
    archive = tmp_path / "retained/views-0001"
    restore_run_views(tmp_path, setup, baseline, archive)
    for arm, view in setup["views"].items():
        assert (view / "source").read_text() == arm
        assert (view / "source").stat().st_mode & 0o777 == 0o640
        assert not (view / "extra").exists() and (view / "empty").is_dir()
        assert (archive / "consumed" / arm / "source").read_text() == "case edit"
        assert (archive / "consumed" / arm / "extra").read_text() == "case output"


@pytest.mark.parametrize("fault", ["bytes", "mode", "extra", "redirected", "wrong_view"])
def test_baseline_drift_refuses_before_moving_any_view(tmp_path, fault):
    """MON-13: a changed baseline or redirected destination cannot rewrite the next case."""
    from conformance_run_fixture import capture_run_views, restore_run_views

    setup = setup_views(tmp_path)
    destination = tmp_path / "retained/pristine"
    baseline = capture_run_views(tmp_path, setup, destination)
    if fault == "bytes":
        (destination / "typed/source").write_text("changed")
    elif fault == "mode":
        (destination / "typed/source").chmod(0o600)
    elif fault == "extra":
        (destination / "typed/extra").write_text("undeclared")
    elif fault == "redirected":
        (destination / "typed/source").unlink()
        (destination / "typed/source").symlink_to(setup["views"]["typed"] / "source")
    else:
        setup["views"]["typed"] = setup["root"]
    archive = tmp_path / "retained/views-0001"
    with pytest.raises(ValueError):
        restore_run_views(tmp_path, setup, baseline, archive)
    assert not archive.exists()
    assert (tmp_path / "typed/source").read_text() == "typed"
    assert (tmp_path / "monolithic/source").read_text() == "monolithic"
