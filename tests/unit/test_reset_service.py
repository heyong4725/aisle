"""Unit tests for the reset dispatcher (SPEC 040 RST-1, RST-2) — the pure
routing function, no dora (CON-12)."""

import numpy as np
import pytest

from aisle.reset.service import refusal_reply_metadata, route_reset, stamp

pytestmark = pytest.mark.unit


def test_teleport_routes_to_bridge():
    """RST-1: mode 0 (teleport) dispatches to the bridge, which owns state
    injection; the <2 s completion budget is measured live in acceptance A2
    through this dispatcher."""
    assert route_reset(0) == "bridge"


def test_behavioral_is_refused_loudly():
    """RST-2: behavioral mode is Phase 2 — refused loudly, never silently
    downgraded to teleport."""
    with pytest.raises(NotImplementedError, match="Phase 2"):
        route_reset(1)


def test_unknown_mode_is_rejected():
    """RST-1: the reset request schema admits only modes 0 and 1; anything
    else is an explicit error, not a default."""
    with pytest.raises(ValueError, match="reset mode"):
        route_reset(2)


def test_stamp_adds_tc2_keys_and_service_seq():
    """TC-2 (PR review): every service output carries sim_time_ns, env_id,
    and the service's OWN per-topic monotonic seq; upstream values for the
    first two are preserved when present."""
    assert stamp({}, 3) == {"sim_time_ns": 0, "env_id": 0, "seq": 3}
    stamped = stamp({"sim_time_ns": 42, "env_id": 1, "seq": 999, "request_id": "r"}, 4)
    assert stamped == {"sim_time_ns": 42, "env_id": 1, "seq": 4, "request_id": "r"}


def test_refusal_reply_metadata_is_tc6_complete():
    """TC-6 (PR review): a refusal reply echoes request_id, carries
    seed/mode when the payload was well-formed, t_reset_ms=0 (the sim was
    never touched), and the error."""
    payload = np.array([7, 1], dtype=np.uint32)
    meta = refusal_reply_metadata({"request_id": "req-9"}, payload, "behavioral is Phase 2")
    assert meta == {
        "request_id": "req-9",
        "t_reset_ms": 0,
        "error": "behavioral is Phase 2",
        "seed": 7,
        "mode": 1,
    }
    malformed = refusal_reply_metadata({"request_id": "req-10"}, np.array([], np.uint32), "bad")
    assert "seed" not in malformed and malformed["request_id"] == "req-10"
