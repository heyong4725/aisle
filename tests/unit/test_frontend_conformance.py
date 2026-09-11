"""MON-8/MON-12/MON-13: frontend coverage is an admitted, evidenced property."""

import pytest
from test_matched_session import _app_server_launch_pair, prepared_pair

pytestmark = pytest.mark.unit


def test_complete_coverage_requires_verified_profile_at_pair_admission(tmp_path):
    """MON-8/MON-13: a positive ceiling alone cannot admit complete tool coverage."""
    from aisle.harness.matched_session import AdmissionError, admit_pair

    control, candidates, roots = prepared_pair(tmp_path)
    launches = _app_server_launch_pair(candidates)
    for arm, launch in launches.items():
        candidates[arm]["budget"].update(frontend_tool_ceiling=3, frontend_coverage="complete")
        launch["provider"] = {
            "base_url": "http://127.0.0.1:1234/v1",
            "requires_openai_auth": False,
        }
    with pytest.raises(AdmissionError, match="conformance|coverage"):
        admit_pair(control, candidates, roots, launches=launches)


@pytest.mark.parametrize("policy", [True, 1, "verified", "COMPLETE", ""])
def test_unknown_frontend_coverage_policy_cannot_be_ignored(tmp_path, policy):
    """MON-8/MON-13: typos and non-string policies cannot silently become partial admission."""
    from aisle.harness.matched_session import AdmissionError, admit_pair

    control, candidates, roots = prepared_pair(tmp_path)
    launches = _app_server_launch_pair(candidates)
    for candidate in candidates.values():
        candidate["budget"]["frontend_coverage"] = policy
    with pytest.raises(AdmissionError, match="conformance|coverage"):
        admit_pair(control, candidates, roots, launches=launches)


def test_complete_coverage_cannot_bypass_verification_without_launch_binding(tmp_path):
    """MON-13: an omitted frontend launch cannot establish a verified route inventory."""
    from aisle.harness.matched_session import AdmissionError, admit_pair

    control, candidates, roots = prepared_pair(tmp_path)
    for candidate in candidates.values():
        candidate["budget"]["frontend_coverage"] = "complete"
    with pytest.raises(AdmissionError, match="conformance|coverage"):
        admit_pair(control, candidates, roots)
