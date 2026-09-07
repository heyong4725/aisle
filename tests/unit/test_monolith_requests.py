"""MON-3/MON-4/MON-8: explicit primitive authority across the worker boundary."""

import numpy as np
import pytest

pytestmark = pytest.mark.unit


def _request(handle, name, *, op="get", args=None, kwargs=None):
    return {
        "op": op,
        "handle": handle,
        "name": name,
        "args": [] if args is None else args,
        "kwargs": {} if kwargs is None else kwargs,
    }


def test_requests_use_the_actual_pose_primitive_and_keep_state():
    """MON-4: remote calls delegate to the existing L1 session and preserve its lifecycle."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.requests import PrimitiveRequests

    primitives = Primitives._load("franka")
    service = PrimitiveRequests(primitives)
    reply = service.dispatch(_request(0, "pose_session", op="call"))
    assert reply["reference"]["kind"] == "pose"
    handle = reply["reference"]["handle"]
    target = primitives.med_names[0]
    accepted = service.dispatch(
        _request(handle, "on_target_request", op="call", args=[{"value": {"target_med": target}}])
    )
    assert accepted == {"value": True}
    assert service.dispatch(_request(handle, "target")) == {"value": target}
    assert service.dispatch(_request(handle, "on_reset_done", op="call")) == {"value": None}
    assert service.dispatch(_request(handle, "target")) == {"value": None}


@pytest.mark.parametrize(
    "name", ["__class__", "_load", "backprojector", "__dict__", "not_a_method"]
)
def test_requests_refuse_undeclared_attributes_before_lookup(name):
    """MON-3/MON-8: a request cannot gain introspection or private primitive authority."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.requests import PrimitiveRequestError, PrimitiveRequests

    service = PrimitiveRequests(Primitives._load("franka"))
    with pytest.raises(PrimitiveRequestError):
        service.dispatch(_request(0, name))


def test_requests_return_detached_data_and_refuse_untyped_handles():
    """MON-8: returned configuration cannot mutate trusted state; handles have exact types."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.requests import PrimitiveRequestError, PrimitiveRequests

    primitives = Primitives._load("franka")
    service = PrimitiveRequests(primitives)
    home = service.dispatch(_request(0, "home"))["value"]
    np.testing.assert_array_equal(home, primitives.home)
    config = service.dispatch(_request(0, "physics"))["value"]
    config.clear()
    assert primitives.physics
    for handle in (False, -1, 999):
        with pytest.raises(PrimitiveRequestError):
            service.dispatch(_request(handle, "home"))
    with pytest.raises(PrimitiveRequestError):
        service.dispatch(_request(0, "staged_plan", op="call", args=[{"handle": 0}]))


def test_requests_bound_handles_and_call_count():
    """MON-8/MON-12: primitive requests cannot allocate unlimited objects or calls."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.requests import PrimitiveRequestError, PrimitiveRequests

    service = PrimitiveRequests(Primitives._load("franka"), max_handles=2, max_calls=3)
    service.dispatch(_request(0, "pose_session", op="call"))
    with pytest.raises(PrimitiveRequestError, match="handle"):
        service.dispatch(_request(0, "pose_session", op="call"))
    service.dispatch(_request(0, "home"))
    with pytest.raises(PrimitiveRequestError, match="call"):
        service.dispatch(_request(0, "home"))
    assert service.calls == 4


def test_requests_delegate_typed_plan_handles_and_streamer(monkeypatch):
    """MON-4/MON-8: plan references bind existing objects, without peer object reconstruction.

    Planner output is a deterministic fixture; this verifies delegation and
    reference typing, not IK success or simulator behavior.
    """
    from aisle.monolith.primitives import GraspPlan, Primitives
    from aisle.monolith.requests import PrimitiveRequests
    from aisle.nodes.ik_trajectory import Stage, StagedPlan

    grasp = GraspPlan((0.0,) * 7, 0.1, 0.2, False)
    staged = object.__new__(StagedPlan)
    staged.stages = [Stage("fixture", (np.zeros(7),), 0.0, 0.1)]
    staged.error = None
    observed = []

    def plan_grasp(self, pose, target_med, neighbours=None):
        observed.append((pose, target_med, neighbours))
        return grasp

    def staged_plan(self, plan):
        assert plan is grasp
        return staged

    monkeypatch.setattr(Primitives, "plan_grasp", plan_grasp)
    monkeypatch.setattr(Primitives, "staged_plan", staged_plan)
    service = PrimitiveRequests(Primitives._load("franka"))
    reference = service.dispatch(
        _request(
            0, "plan_grasp", op="call", args=[{"value": [1.0, 2.0, 3.0]}, {"value": "fixture-med"}]
        )
    )["reference"]
    assert observed == [([1.0, 2.0, 3.0], "fixture-med", None)]
    assert service.dispatch(_request(reference["handle"], "grasp")) == {"value": grasp.grasp}
    plan = service.dispatch(
        _request(0, "staged_plan", op="call", kwargs={"plan": {"handle": reference["handle"]}})
    )["reference"]
    stages = service.dispatch(_request(plan["handle"], "stages"))["references"]
    assert len(stages) == 1 and stages[0]["kind"] == "stage"
    assert service.dispatch(_request(stages[0]["handle"], "name")) == {"value": "fixture"}
    remote_path = service.dispatch(_request(stages[0]["handle"], "path"))["value"]
    remote_path[0][:] = 10
    assert np.all(staged.stages[0].path[0] == 0)
    streamer = service.dispatch(
        _request(0, "streamer", op="call", args=[{"handle": plan["handle"]}])
    )["reference"]
    assert streamer["kind"] == "streamer"
    assert service.dispatch(_request(streamer["handle"], "done")) == {"value": False}


def test_released_handles_are_not_reused_and_unknown_arguments_are_refused():
    """MON-8: stale references cannot address later objects or invoke extra parameters."""
    from aisle.monolith.primitives import Primitives
    from aisle.monolith.requests import PrimitiveRequestError, PrimitiveRequests

    service = PrimitiveRequests(Primitives._load("franka"), max_handles=2)
    old = service.dispatch(_request(0, "pose_session", op="call"))["reference"]["handle"]
    assert service.dispatch(_request(old, "", op="release")) == {"value": None}
    new = service.dispatch(_request(0, "pose_session", op="call"))["reference"]["handle"]
    assert new != old
    with pytest.raises(PrimitiveRequestError):
        service.dispatch(_request(old, "target"))
    with pytest.raises(PrimitiveRequestError):
        service.dispatch(
            _request(new, "on_reset_done", op="call", kwargs={"hidden_override": {"value": True}})
        )
    with pytest.raises(PrimitiveRequestError):
        service.dispatch(_request(0, "", op="release"))


def test_request_surface_matches_public_primitive_api():
    """MON-3/MON-4: the remote root surface tracks every declared primitive member."""
    from aisle.monolith.primitives import PUBLIC_API
    from aisle.monolith.requests import CALLS, READS

    assert READS["primitives"] | CALLS["primitives"] == set(PUBLIC_API)
    assert not READS["primitives"] & CALLS["primitives"]
