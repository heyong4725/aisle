"""MON-8/MON-12/MON-13: provider items require admission before delivery."""

import json

import pytest

from aisle.harness.frontend_dispatch import DispatchBudget, DispatchRefused
from aisle.harness.provider_response_authority import ProviderResponseAuthority

pytestmark = pytest.mark.unit


def item(index, *, kind="function_call", namespace=None, name="exec_command"):
    value = {
        "id": f"item-{index}",
        "type": kind,
        "call_id": f"call-{index}",
        "name": name,
        "arguments" if kind == "function_call" else "input": "{}",
    }
    if namespace is not None:
        value["namespace"] = namespace
    return value


def frames(items, *, response_id="response-1", mutate=None):
    events = [{"type": "response.created", "response": {"id": response_id}}]
    events.extend(
        {"type": "response.output_item.done", "output_index": i, "item": value}
        for i, value in enumerate(items)
    )
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": response_id,
                "status": "completed",
                "output": items,
            },
        }
    )
    if mutate:
        mutate(events)
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events
    )


def test_second_item_is_not_delivered_at_ceiling(tmp_path):
    """MON-12/MON-13: one response with two calls consumes two distinct reservations."""
    delivered = []
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=1) as budget:
        authority = ProviderResponseAuthority(budget)
        with pytest.raises(DispatchRefused, match="exhausted"):
            authority.forward(frames([item(1), item(2)]), delivered.append)
    assert b'"call-1"' in b"".join(delivered)
    assert b'"call-2"' not in b"".join(delivered)
    assert b"response.completed" not in b"".join(delivered)
    assert budget.reference()["reserved"] == 1
    assert budget.reference()["attempts"] == 2


@pytest.mark.parametrize("kind", ["function_call", "custom_tool_call"])
def test_reservation_exists_before_executable_frame(tmp_path, kind):
    """MON-13: the frontend byte writer observes a durable reservation first."""
    delivered = []
    root = tmp_path / "budget"
    with DispatchBudget(root, session_id="session", ceiling=2) as budget:
        authority = ProviderResponseAuthority(budget)

        def deliver(frame):
            if b"response.output_item.done" in frame:
                record = json.loads((root / "00000001-reservation.json").read_bytes())
                assert record["decision"] == "authorized"
                assert (root / "00000001.frame").read_bytes() == frame
            delivered.append(frame)

        raw = frames([item(1, kind=kind)])
        authority.forward(raw, deliver)
    assert b"".join(delivered) == raw


@pytest.mark.parametrize("fault", ["identity", "output", "index", "replay", "trailing"])
def test_malformed_response_never_releases_prefix(tmp_path, fault):
    """MON-13: reject inconsistent complete streams before any executable item is released."""

    def mutate(events):
        if fault == "identity":
            events[-1]["response"]["id"] = "other"
        elif fault == "output":
            events[-1]["response"]["output"] = []
        elif fault == "index":
            events[1]["output_index"] = True
        elif fault == "replay":
            events.insert(2, events[1])
        else:
            events.append(events[1])

    delivered = []
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=2) as budget:
        with pytest.raises(ValueError):
            ProviderResponseAuthority(budget).forward(
                frames([item(1)], mutate=mutate), delivered.append
            )
    assert delivered == []
    assert budget.reference()["reserved"] == 0


def test_same_response_cannot_be_replayed(tmp_path):
    """MON-13: provider response retries cannot execute the same calls again."""
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=3) as budget:
        authority = ProviderResponseAuthority(budget)
        raw = frames([item(1)])
        authority.forward(raw, lambda _: None)
        delivered = []
        with pytest.raises(ValueError, match="replay"):
            authority.forward(raw, delivered.append)
    assert not delivered
    assert budget.reference()["reserved"] == 1


def test_delegation_is_exact_namespace_and_name(tmp_path):
    """MON-8/MON-13: dotted lookalikes do not bypass the shared budget."""
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=2) as budget:
        authority = ProviderResponseAuthority(
            budget, delegated_tools={("harness", "check", "function_call")}
        )
        authority.forward(
            frames(
                [
                    item(1, namespace="harness", name="check"),
                    item(2, name="harness.check"),
                ]
            ),
            lambda _: None,
        )
    assert budget.reference()["reserved"] == 1


def test_non_tool_write_failure_retires_provider_authority(tmp_path):
    """MON-13: uncertain stream delivery cannot be followed by a fresh response."""
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=2) as budget:
        authority = ProviderResponseAuthority(budget)
        with pytest.raises(OSError):
            authority.forward(frames([item(1)]), lambda _: (_ for _ in ()).throw(OSError()))
        with pytest.raises(ValueError, match="closed"):
            authority.forward(frames([item(2)], response_id="response-2"), lambda _: None)


def test_client_tool_search_is_reserved(tmp_path):
    """MON-12/MON-13: the pinned router also executes client tool-search output items."""
    search = {
        "type": "tool_search_call",
        "id": "search-1",
        "call_id": "search-call-1",
        "execution": "client",
        "arguments": {"query": "available tools"},
    }
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=1) as budget:
        ProviderResponseAuthority(budget).forward(frames([search]), lambda _: None)
    assert budget.reference()["reserved"] == 1


def test_duplicate_call_in_new_response_is_refused(tmp_path):
    """MON-13: a changed provider response ID cannot disguise a replayed call."""
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=3) as budget:
        authority = ProviderResponseAuthority(budget)
        authority.forward(frames([item(1)]), lambda _: None)
        delivered = []
        with pytest.raises(ValueError, match="call.*replay"):
            authority.forward(frames([item(1)], response_id="response-2"), delivered.append)
    assert not delivered
    assert budget.reference()["reserved"] == 1


def test_delegation_does_not_cover_another_payload_kind(tmp_path):
    """MON-13: a delegated function name cannot exempt an unrelated custom-tool payload."""
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=2) as budget:
        authority = ProviderResponseAuthority(
            budget, delegated_tools={("harness", "check", "function_call")}
        )
        authority.forward(
            frames(
                [
                    item(1, namespace="harness", name="check"),
                    item(2, namespace="harness", name="check", kind="custom_tool_call"),
                ]
            ),
            lambda _: None,
        )
    assert budget.reference()["reserved"] == 1


def test_concurrent_responses_cannot_share_one_remaining_reservation(tmp_path):
    """MON-13: simultaneous provider responses cannot both cross the last budget slot."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    start = threading.Barrier(2)
    delivered = {}
    with DispatchBudget(tmp_path / "budget", session_id="session", ceiling=1) as budget:
        authority = ProviderResponseAuthority(budget)

        def send(index):
            delivered[index] = []
            start.wait(timeout=2)
            try:
                authority.forward(
                    frames([item(index)], response_id=f"response-{index}"), delivered[index].append
                )
                return True
            except DispatchRefused:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send, [1, 2]))
    assert sorted(results) == [False, True]
    assert budget.reference()["reserved"] == 1
    assert budget.reference()["attempts"] == 2
    assert sum(b"response.output_item.done" in b"".join(value) for value in delivered.values()) == 1
