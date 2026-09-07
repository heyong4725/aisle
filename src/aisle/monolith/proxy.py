"""Worker-side primitive proxies without imports of trusted implementations."""

from __future__ import annotations

from aisle.monolith.primitive_api import CALLS, CREATES, READS


class PrimitiveProtocolError(RuntimeError):
    """A primitive peer violated the request/reply protocol."""


class PrimitiveRemoteError(RuntimeError):
    """A declared primitive raised in the trusted process."""

    def __init__(self, kind, message):
        super().__init__(f"{kind}: {message}")
        self.kind = kind


class PrimitiveClient:
    """Adapt a synchronous data-only exchange to the existing primitive API."""

    def __init__(self, exchange):
        self._exchange = exchange
        self._objects = {}
        self.root = self._reference({"handle": 0, "kind": "primitives"})

    def _reference(self, reference):
        if type(reference) is not dict or set(reference) != {"handle", "kind"}:
            raise PrimitiveProtocolError("invalid primitive reference")
        handle, kind = reference["handle"], reference["kind"]
        if (
            type(handle) is not int
            or handle < 0
            or type(kind) is not str
            or kind not in READS
            or (handle == 0) != (kind == "primitives")
        ):
            raise PrimitiveProtocolError("invalid primitive reference identity")
        if handle in self._objects:
            obj = self._objects[handle]
            if obj._kind != kind:
                raise PrimitiveProtocolError("primitive reference changed type")
            return obj
        obj = RemotePrimitive(self, handle, kind)
        self._objects[handle] = obj
        return obj

    def _argument(self, argument):
        if type(argument) is RemotePrimitive:
            if argument._client is not self:
                raise PrimitiveProtocolError("primitive handle belongs to another client")
            return {"handle": argument._handle}
        return {"value": argument}

    def _invoke(self, obj, operation, name, args=(), kwargs=None):
        reply = self._exchange(
            {
                "op": operation,
                "handle": obj._handle,
                "name": name,
                "args": [self._argument(value) for value in args],
                "kwargs": {key: self._argument(value) for key, value in (kwargs or {}).items()},
            }
        )
        if type(reply) is not dict or len(reply) != 1:
            raise PrimitiveProtocolError("invalid primitive reply")
        if "error" in reply:
            error = reply["error"]
            if (
                type(error) is not dict
                or set(error) != {"category", "kind", "message"}
                or any(type(value) is not str for value in error.values())
                or error["category"] not in {"request", "primitive"}
            ):
                raise PrimitiveProtocolError("invalid primitive error reply")
            if error["category"] == "request":
                raise PrimitiveProtocolError(error["message"])
            raise PrimitiveRemoteError(error["kind"], error["message"])
        expected = CREATES.get(name) if obj._kind == "primitives" and operation == "call" else None
        if expected is not None:
            if "reference" not in reply:
                raise PrimitiveProtocolError("primitive call omitted its reference")
            result = self._reference(reply["reference"])
            if result._kind != expected:
                raise PrimitiveProtocolError("primitive call returned the wrong reference type")
            return result
        if obj._kind == "staged" and operation == "get" and name == "stages":
            if "references" not in reply or type(reply["references"]) is not list:
                raise PrimitiveProtocolError("primitive stages reply is invalid")
            result = [self._reference(ref) for ref in reply["references"]]
            if any(stage._kind != "stage" for stage in result):
                raise PrimitiveProtocolError("primitive stages reply has a different object type")
            return result
        if "value" not in reply:
            raise PrimitiveProtocolError("primitive operation omitted its value")
        return reply["value"]


class RemotePrimitive:
    """A handle proxy; all authority checks remain in the trusted dispatcher."""

    __slots__ = ("_client", "_handle", "_kind")

    def __init__(self, client, handle, kind):
        self._client, self._handle, self._kind = client, handle, kind

    def __getattr__(self, name):
        if name in READS[self._kind]:
            return self._client._invoke(self, "get", name)
        if name in CALLS[self._kind]:

            def call(*args, **kwargs):
                return self._client._invoke(self, "call", name, args, kwargs)

            return call
        raise AttributeError(f"undeclared primitive member: {name}")
