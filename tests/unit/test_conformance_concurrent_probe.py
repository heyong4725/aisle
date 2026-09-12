"""MON-8/MON-13: concurrent probes consume all but one admitted reservation."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("ceiling,mcp_harness", [(2, False), (4, True)])
def test_concurrent_probe_competes_for_last_slot(tmp_path, ceiling, mcp_harness):
    """MON-13: overlapping dispatch requests cannot both consume the final slot."""
    from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
    from aisle.harness.frontend_effects import command_probe, concurrent_probe

    script = concurrent_probe(
        ceiling, mcp_harness=mcp_harness, target="task.yaml", marker="# AISLE probe concurrent"
    )
    harness = "mcp__aisle_harness__check" if mcp_harness else "harness__check"
    assert script.startswith("await tools." + harness + "({}); ")
    assert script.count('await tools.exec_command({cmd:":",login:false}); ') == ceiling - 2
    assert "await Promise.allSettled([" in script
    import json

    assert (
        json.dumps({"cmd": command_probe("task.yaml", "# AISLE probe concurrent"), "login": False})
        in script
    )
    entered, attempted, release = threading.Event(), threading.Event(), threading.Event()
    delivered = []

    def call(number):
        return {"turn_id": "turn", "call_id": str(number), "tool_name": "exec_command"}

    def pending(_):
        entered.set()
        assert release.wait(5)
        delivered.append("authorized")

    with DispatchBudget(tmp_path / "dispatch", session_id="concurrent", ceiling=ceiling) as budget:
        for number in range(1, ceiling):
            budget.dispatch(call(number), b"prefix", lambda _: None)

        def competing():
            attempted.set()
            budget.dispatch(call(ceiling + 1), b"effect", lambda _: delivered.append("refused"))

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(budget.dispatch, call(ceiling), b"pending", pending)
            try:
                assert entered.wait(5)
                second = pool.submit(competing)
                assert attempted.wait(5)
            finally:
                release.set()
            first.result(timeout=5)
            with pytest.raises(DispatchRefused):
                second.result(timeout=5)
    assert delivered == ["authorized"]
