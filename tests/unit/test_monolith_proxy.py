"""MON-3/MON-4/MON-12: controller-facing primitive proxies preserve the public API."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_current_expert_constructs_and_handles_events_through_proxy():
    """MON-4/MON-12: the unchanged expert uses remote pose state and returns normal feedback."""
    from aisle.monolith.confinement import load_module
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.proxy import PrimitiveClient
    from aisle.monolith.requests import PrimitiveRequests
    from aisle.monolith.wire import decode, encode

    service = PrimitiveRequests(Primitives._load("franka"))
    seen = []

    def exchange(request):
        seen.append(request)
        return decode(encode(service.dispatch(decode(encode(request)))))

    client = PrimitiveClient(exchange)
    module = Path(__file__).resolve().parents[2] / "experts/monolithic/expert_t1.py"
    namespace = load_module(str(module), module.read_text())
    controller = namespace["Controller"](client.root, lambda message: None)
    target = client.root.med_names[0]
    event = {
        "name": "episode_goal",
        "payload": {"target_med": target},
        "sim_time_ns": 0,
        "goal_id": "fixture",
    }
    assert controller.on_event(event) == []
    assert controller.session.target == target
    event.update(name="tick", payload=1, sim_time_ns=1_000_000_000)
    assert controller.on_event(event) == [
        {"feedback": {"t": 1, "phase": "executing", "retries": 0}}
    ]
    event.update(name="reset_done", payload=None)
    assert controller.on_event(event) == []
    assert controller.session.target is None
    assert any(request["name"] == "on_target_request" for request in seen)


def test_proxy_rejects_cross_client_handles_before_exchange():
    """MON-8: handles from separate worker sessions cannot be mixed by the client."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.proxy import PrimitiveClient, PrimitiveProtocolError
    from aisle.monolith.requests import PrimitiveRequests

    first_service = PrimitiveRequests(Primitives._load("franka"))
    second_service = PrimitiveRequests(Primitives._load("franka"))
    first = PrimitiveClient(first_service.dispatch)
    second = PrimitiveClient(second_service.dispatch)
    foreign = first.root.pose_session()
    with pytest.raises(PrimitiveProtocolError):
        second.root.staged_plan(foreign)
    assert second_service.calls == 0


@pytest.mark.parametrize(
    "reply",
    [
        {},
        {"value": 1, "reference": {}},
        {"reference": {"handle": True, "kind": "pose"}},
        {"reference": {"handle": 1, "kind": "private"}},
    ],
)
def test_proxy_refuses_malformed_replies(reply):
    """MON-8/MON-12: malformed replies cannot create undeclared remote object types."""
    from aisle.monolith.proxy import PrimitiveClient, PrimitiveProtocolError

    client = PrimitiveClient(lambda request: reply)
    with pytest.raises(PrimitiveProtocolError):
        client.root.pose_session()


def test_proxy_import_does_not_load_trusted_implementations():
    """MON-3/MON-8: the worker facade has no import dependency on privileged primitive code."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import aisle.monolith.proxy; "
            "forbidden = ('aisle.nodes', 'aisle.scenes', 'aisle.verifier', 'aisle.harness'); "
            "assert not any(name == prefix or name.startswith(prefix + '.') "
            "for name in sys.modules for prefix in forbidden)",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_proxy_distinguishes_primitive_failures_from_protocol_refusals():
    """MON-8/MON-12: peer error categories remain distinct without reconstructing exceptions."""
    from aisle.monolith.proxy import PrimitiveClient, PrimitiveProtocolError, PrimitiveRemoteError

    replies = iter(
        [
            {"error": {"category": "primitive", "kind": "ValueError", "message": "bad target"}},
            {
                "error": {
                    "category": "request",
                    "kind": "PrimitiveRequestError",
                    "message": "refused",
                }
            },
        ]
    )
    client = PrimitiveClient(lambda request: next(replies))
    with pytest.raises(PrimitiveRemoteError) as failure:
        client.root.pose_session()
    assert failure.value.kind == "ValueError"
    with pytest.raises(PrimitiveProtocolError, match="refused"):
        client.root.pose_session()
