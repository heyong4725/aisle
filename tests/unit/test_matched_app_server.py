"""MON-12/MON-13: preserve primary failures in the shared App Server runner."""

import pytest
from test_matched_tools import _controller

pytestmark = pytest.mark.unit


def test_cleanup_retention_failure_preserves_frontend_failure(tmp_path, monkeypatch):
    """MON-12: failed report retention must not replace the original frontend failure."""
    from aisle.harness import matched_app_server as runner

    controller, _, _ = _controller(tmp_path, "typed")

    def fail_frontend(*args, **kwargs):
        raise ValueError("original frontend failure")

    def fail_retention(*args, **kwargs):
        raise OSError("secondary report retention failure")

    monkeypatch.setattr(runner, "run_app_server", fail_frontend)
    monkeypatch.setattr(runner, "_json", fail_retention)
    from pathlib import Path

    channel = (
        Path(controller.plan["ambient_bindings"]["typed"]["environment"]["HOME"]) / "tool-channel"
    )
    channel.mkdir()
    with pytest.raises(ValueError, match="original frontend failure") as caught:
        runner.run_authorized_app_server(
            controller,
            ["unused"],
            cwd=tmp_path,
            env={},
            launch={
                "app_server": {
                    "baseInstructions": "system prompt",
                    "developerInstructions": "research contract",
                }
            },
            budget={
                **controller.plan["arms"]["typed"]["budget"],
                "wall_ceiling_s": 1,
                "frontend_tool_ceiling": 1,
            },
            references={},
        )
    assert any("secondary report retention" in note for note in caught.value.__notes__)
