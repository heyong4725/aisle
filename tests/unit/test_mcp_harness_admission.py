"""MON-8/MON-13: the inherited MCP harness is an explicit matched launch property."""

import pytest
from test_matched_session import _app_server_launch_pair, prepared_pair

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "fault", [None, "without_provider", "not_boolean", "different_arms", "no_harness"]
)
def test_mcp_harness_binding_is_admitted_and_rechecked(tmp_path, fault):
    """MON-8/MON-13: both arms bind the same owned MCP authority and provider budget."""
    from aisle.harness.matched_session import AdmissionError, admit_pair, verify_plan

    root, candidates, views = prepared_pair(tmp_path)
    launches = _app_server_launch_pair(candidates)
    for arm, launch in launches.items():
        candidates[arm]["budget"]["frontend_tool_ceiling"] = 4
        if fault != "no_harness":
            candidates[arm]["policy"]["allowed_external_tools"].append("harness.check")
        launch["provider"] = {"base_url": "http://127.0.0.1:1234/v1", "requires_openai_auth": False}
        launch["mcp_harness"] = True
        if fault == "without_provider":
            del launch["provider"]
        elif fault == "not_boolean":
            launch["mcp_harness"] = "true"
    if fault == "different_arms":
        del launches["monolithic"]["mcp_harness"]
    if fault is not None:
        with pytest.raises(AdmissionError):
            admit_pair(root, candidates, views, launches=launches)
    else:
        plan = admit_pair(root, candidates, views, launches=launches)
        assert verify_plan(plan, root, views)["launch_bindings"] == launches
