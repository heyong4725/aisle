"""Unit tests for the reset dispatcher (SPEC 040 RST-1, RST-2) — the pure
routing function, no dora (CON-12)."""

import pytest

from aisle.reset.service import route_reset

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
