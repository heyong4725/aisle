"""SPEC 040 RST-2 behavioral reset — Phase 2 acceptance slot (per TASKS.md,
behavioral is not in M0 scope). The sim marker anticipates the Phase 2
routine, which will need genesis; today's refusal behavior is unit-tested
in tests/unit/test_reset_service.py."""

import pytest

pytestmark = pytest.mark.sim


def test_behavioral_reset_is_phase_two():
    """RST-2 (Phase 2 gate): placeholder for the behavioral reset routine."""
    pytest.skip("behavioral reset routine is Phase 2 (TASKS.md)")
