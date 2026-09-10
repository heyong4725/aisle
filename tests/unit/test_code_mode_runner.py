"""MON-13: the controller owns the pinned host and both process lifetimes."""

import asyncio
import hashlib
import sys

import pytest

from aisle.harness.frontend_dispatch import DispatchBudget

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("failure", ["digest", "host_exit"])
def test_host_failure_never_launches_frontend(tmp_path, failure):
    """MON-13: drift or startup failure cannot fall back to an unmediated frontend."""
    from aisle.harness.code_mode_runner import run_code_mode_app_server

    marker = tmp_path / "host-started"
    host = tmp_path / "host"
    host.write_text(f"#!{sys.executable}\nimport pathlib\npathlib.Path({str(marker)!r}).touch()\n")
    host.chmod(0o700)
    expected = hashlib.sha256(host.read_bytes()).hexdigest() if failure == "host_exit" else "0" * 64
    with DispatchBudget(tmp_path / "dispatch", session_id="matched", ceiling=1) as budget:
        with pytest.raises(ValueError, match="host"):
            asyncio.run(
                run_code_mode_app_server(
                    host={"path": str(host), "sha256": expected},
                    dispatch=budget,
                    delegated_tools=set(),
                    output=tmp_path / "code-mode",
                    references={},
                    argv=["must-not-launch"],
                    cwd=tmp_path,
                    env={},
                    protocol_output=tmp_path / "frontend",
                    thread_params={},
                    input_items=[],
                    handle_call=lambda *_: pytest.fail("unmediated frontend call"),
                    timeout_s=2,
                )
            )
    assert marker.exists() == (failure == "host_exit")
    assert not (tmp_path / "frontend").exists()
