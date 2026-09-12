"""MON-13: prior hosted work must match an owned frontend lifecycle."""

import copy

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "drift", [None, "missing", "duplicate", "action", "owner", "extra", "order"]
)
def test_hosted_prefix_requires_exact_completed_lifecycle(drift):
    """MON-13: closed provider results alone cannot prove frontend completion."""
    from aisle.harness.frontend_qualification import match_hosted_prefix_items

    action = {"type": "search", "query": "fixture"}
    start = {
        "method": "item/started",
        "params": {
            "threadId": "thread",
            "turnId": "turn",
            "item": {"type": "webSearch", "id": "search", "action": action},
        },
    }
    end = copy.deepcopy(start)
    end["method"] = "item/completed"
    rows = [start, end]
    if drift == "missing":
        rows.pop()
    elif drift == "duplicate":
        rows.append(copy.deepcopy(end))
    elif drift == "action":
        end["params"]["item"]["action"]["query"] = "other"
    elif drift == "owner":
        end["params"]["turnId"] = "other"
    elif drift == "extra":
        extra = copy.deepcopy(start)
        extra["params"]["item"]["id"] = "extra"
        rows.append(extra)
    elif drift == "order":
        rows.reverse()
    prefix = {"sequences": {"received": [(row, b"") for row in rows]}}
    expected = [{"id": "search", "action": {**action, "queries": None}}]
    if drift is None:
        assert match_hosted_prefix_items(expected, prefix) == 1
    else:
        with pytest.raises(ValueError):
            match_hosted_prefix_items(expected, prefix)
