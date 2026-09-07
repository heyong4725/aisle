"""Explicit primitive requests for the planned isolated monolithic worker.

Objects stay in this service; peers receive bounded data or session-local
handles. This handler does not launch a worker or establish OS confinement.
"""

from __future__ import annotations

import inspect

from aisle.monolith.primitive_api import CALLS, CREATES, READS
from aisle.monolith.primitives import GraspPlan, Primitives
from aisle.monolith.wire import decode, encode
from aisle.nodes.ik_trajectory import Stage, StagedPlan, StageStreamer
from aisle.nodes.segmented_pose import L1Session


class PrimitiveRequestError(ValueError):
    """A worker request exceeds the explicit primitive API authority."""


TYPES = {
    Primitives: "primitives",
    L1Session: "pose",
    GraspPlan: "grasp",
    StagedPlan: "staged",
    Stage: "stage",
    StageStreamer: "streamer",
}
REFERENCES = {
    ("primitives", "staged_plan", "plan"): "grasp",
    ("primitives", "streamer", "staged"): "staged",
}


def _copy(value):
    # Copy through the same data protocol as the eventual pipe, so configuration
    # views and ndarray replies can never alias mutable trusted state.
    return decode(encode(value))


class PrimitiveRequests:
    """Serve one worker's documented primitives with bounded object ownership."""

    def __init__(self, primitives, *, max_handles=1024, max_calls=100000):
        if type(primitives) is not Primitives:
            raise PrimitiveRequestError("expected the pinned Primitives implementation")
        if any(type(n) is not int or n <= 0 for n in (max_handles, max_calls)):
            raise PrimitiveRequestError("primitive limits must be positive integers")
        self.max_handles, self.max_calls = max_handles, max_calls
        self.calls = 0
        self._next = 1
        self._objects = {0: primitives}

    def _object(self, handle):
        if type(handle) is not int or handle not in self._objects:
            raise PrimitiveRequestError("unknown primitive handle")
        obj = self._objects[handle]
        return obj, TYPES[type(obj)]

    def _reference(self, obj):
        kind = TYPES.get(type(obj))
        if kind is None:
            raise PrimitiveRequestError("primitive returned an undeclared object type")
        for handle, retained in self._objects.items():
            if retained is obj:
                return {"handle": handle, "kind": kind}
        if len(self._objects) >= self.max_handles:
            raise PrimitiveRequestError("primitive handle budget exhausted")
        handle = self._next
        self._next += 1
        self._objects[handle] = obj
        return {"handle": handle, "kind": kind}

    def dispatch(self, request):
        """Execute only a named API operation; count rejected requests too."""
        self.calls += 1
        if self.calls > self.max_calls:
            raise PrimitiveRequestError("primitive call budget exhausted")
        if type(request) is not dict or set(request) != {"op", "handle", "name", "args", "kwargs"}:
            raise PrimitiveRequestError("invalid primitive request schema")
        operation, name = request["op"], request["name"]
        args, kwargs = request["args"], request["kwargs"]
        if (
            type(operation) is not str
            or type(name) is not str
            or type(args) is not list
            or type(kwargs) is not dict
            or any(type(k) is not str for k in kwargs)
        ):
            raise PrimitiveRequestError("invalid primitive request fields")
        obj, kind = self._object(request["handle"])
        if operation == "release":
            if request["handle"] == 0 or name or args or kwargs:
                raise PrimitiveRequestError("invalid primitive release")
            del self._objects[request["handle"]]
            return {"value": None}
        if operation == "get":
            if name not in READS[kind] or args or kwargs:
                raise PrimitiveRequestError("undeclared primitive attribute")
            value = getattr(obj, name)
            if kind == "staged" and name == "stages":
                # Check the whole allocation before changing the handle table.
                known = {id(v) for v in self._objects.values()}
                extra = {id(v) for v in value} - known
                if len(self._objects) + len(extra) > self.max_handles:
                    raise PrimitiveRequestError("primitive handle budget exhausted")
                return {"references": [self._reference(stage) for stage in value]}
            return {"value": _copy(value)}
        if operation != "call" or name not in CALLS[kind]:
            raise PrimitiveRequestError("undeclared primitive method")
        if kind == "primitives" and name in CREATES and len(self._objects) >= self.max_handles:
            raise PrimitiveRequestError("primitive handle budget exhausted")
        method = getattr(obj, name)
        try:
            bound = inspect.signature(method).bind(*args, **kwargs)
        except TypeError as exc:
            raise PrimitiveRequestError("primitive arguments differ from API") from exc
        for parameter, argument in bound.arguments.items():
            expected = REFERENCES.get((kind, name, parameter))
            if type(argument) is not dict:
                raise PrimitiveRequestError("primitive argument must be a value or handle")
            if expected is not None:
                if set(argument) != {"handle"}:
                    raise PrimitiveRequestError("primitive argument requires a typed handle")
                value, actual = self._object(argument["handle"])
                if actual != expected:
                    raise PrimitiveRequestError("primitive argument handle has the wrong type")
            else:
                if set(argument) != {"value"}:
                    raise PrimitiveRequestError("primitive argument requires a data value")
                value = _copy(argument["value"])
            bound.arguments[parameter] = value
        result = method(*bound.args, **bound.kwargs)
        if kind == "primitives" and name in CREATES:
            if TYPES.get(type(result)) != CREATES[name]:
                raise PrimitiveRequestError("primitive returned the wrong object type")
            return {"reference": self._reference(result)}
        return {"value": _copy(result)}
