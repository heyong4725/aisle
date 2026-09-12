"""MON-12/MON-13: quota claims do not replace retained request-to-attempt execution evidence."""

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "claimed",
    [None, {"ok": True, "service_verified": True, "frontend_authorization_verified": True}],
)
def test_refusal_execution_requires_original_chain(claimed):
    """MON-13: a recorded green journal cannot authorize an evidence-free refused session."""
    from aisle.harness.frontend_qualification import audit_refusal_execution

    with pytest.raises(ValueError):
        audit_refusal_execution(
            {"artifacts": {}, "record": {"tool_audit": claimed}, "admission": {}}
        )
