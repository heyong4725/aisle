"""MON-8/MON-13: the frontend endpoint must resolve to the owned relay listener."""

import json

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "drift", [None, "session", "host", "boolean", "zero", "range", "extra", "missing"]
)
def test_listener_identity_is_exact_and_local(drift):
    """MON-13: malformed, external or cross-session listener identities cannot bind a launch."""
    from aisle.harness.provider_source_audit import read_provider_listener

    listener = {
        "schema_version": "aisle.provider-relay-listener.v1",
        "session_id": "session",
        "host": "127.0.0.1",
        "port": 32123,
    }
    if drift == "session":
        listener["session_id"] = "other"
    elif drift == "host":
        listener["host"] = "192.0.2.1"
    elif drift in {"boolean", "zero", "range"}:
        listener["port"] = {"boolean": True, "zero": 0, "range": 65536}[drift]
    elif drift == "extra":
        listener["fallback"] = "unbound"
    artifacts = {} if drift == "missing" else {"listener.json": json.dumps(listener).encode()}
    if drift is None:
        assert read_provider_listener(artifacts, session_id="session") == listener
    else:
        with pytest.raises(ValueError):
            read_provider_listener(artifacts, session_id="session")
