"""MON-8/MON-13: every refused route is exercised with the full admitted surface."""

import pytest
from test_matched_conformance_session import (
    test_admitted_actual_session_composes_source_execution_and_effect as _session,
)
from test_matched_conformance_session import (
    test_admitted_budget_refusal_is_independent_of_frontend_hook as _hook,
)

pytestmark = pytest.mark.accept


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("route", ["subagents", "harness_child"])
def test_available_child_with_all_frontend_features(tmp_path, arm, route):
    """MON-8/MON-13: frontend waiting permits the owned child's third-attempt effect."""
    _session(tmp_path, arm, False, route, unified=True)


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize(
    "route",
    [
        "native",
        "native_edit",
        "harness",
        "mcp",
        "continued_input",
        "subagents",
        "harness_child",
        "nested",
        "hosted",
    ],
)
def test_refused_route_with_all_frontend_features(tmp_path, arm, route):
    """MON-8/MON-13: each excess effect reaches its own refusing boundary."""
    _session(tmp_path, arm, True, route, unified=True)


@pytest.mark.parametrize("arm", ["typed", "monolithic"])
@pytest.mark.parametrize("fault", ["hook_absent", "hook_changed"])
def test_hook_refusal_with_all_frontend_features(tmp_path, arm, fault):
    """MON-8/MON-13: hook changes cannot weaken refusal in the admitted configuration."""
    _hook(tmp_path, arm, fault, unified=True)
