"""Unit tests for the ADR-38 chunk-preemption core and instruction
derivation — no torch, no lerobot, no dora (CON-12)."""

import pytest

from aisle.nodes.vla_smolvla import VLA_STALE_NS, ChunkQueue, instruction_from_request

pytestmark = pytest.mark.unit


def test_new_inference_replaces_queued_remainder():
    """ADR-38 rule 1: one in-flight chunk — a fresh result replaces the
    remainder, never interleaves."""
    q = ChunkQueue()
    assert q.offer([[1], [2], [3]], obs_ns=0, now_ns=0)
    assert q.pop() == [1]
    assert q.offer([[9], [8]], obs_ns=100, now_ns=100)
    assert q.pop() == [9]
    assert q.pop() == [8]
    assert q.pop() is None  # old [2],[3] never ran


def test_stale_inference_is_dropped_not_executed():
    """ADR-38 rule 4: a chunk computed from observations older than the
    staleness floor at emission is refused."""
    q = ChunkQueue()
    assert not q.offer([[1]], obs_ns=0, now_ns=VLA_STALE_NS + 1)
    assert q.pop() is None
    assert q.offer([[1]], obs_ns=0, now_ns=VLA_STALE_NS)  # at the floor: accepted


def test_reset_flushes_the_queue():
    """ADR-38 rule 3: the episode boundary drops any queued chunk."""
    q = ChunkQueue()
    q.offer([[1], [2]], obs_ns=0, now_ns=0)
    q.flush()
    assert q.pop() is None and q.obs_ns is None


def test_instruction_derives_from_the_standard_target_request():
    """The policy conditions on the SAME channel every classical
    pipeline consumes — no extra goal visibility."""
    text = instruction_from_request({"target_med": "cetirizine"})
    assert "cetirizine" in text and "tray" in text
    assert "item" in instruction_from_request({})
