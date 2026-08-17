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


def test_the_staleness_floor_is_reachable_from_the_node(tmp_path):
    """ADR-38 rule 4 (#268): the queue's staleness check is unit-tested with
    explicit stamps, but the NODE called `offer(chunk, obs_ns, obs_ns)` — so
    `now_ns - obs_ns` was identically zero and the rule could never fire.

    A rule that is correct and uninvoked is the pattern this project keeps
    rediscovering (report appendix F6). Under ADR-30 lockstep with synchronous
    inference the delta is legitimately zero, which is exactly why the defect
    was invisible: the moment observation and emission are separated — the
    reason action chunks exist at all — the protection silently would not be
    there.

    The stamps must come from different sources: `obs_ns` from the frame that
    was seen, `now_ns` from the current turn. Both are SIM time, so the
    decision stays reproducible (no wall-clock coupling, #268's other half).
    """
    import inspect

    from aisle.nodes import vla_smolvla

    source = inspect.getsource(vla_smolvla.main)
    assert "queue.offer(chunk, obs_ns, obs_ns)" not in source, (
        "the staleness floor is unreachable: now_ns must not be obs_ns"
    )
    assert "queue.offer(chunk, obs_ns, now_ns)" in source
    # and `now_ns` must be derived from the CURRENT event, not aliased
    assert "now_ns = int(metadata.get(" in source


def test_inference_is_seeded_from_a_reproducible_stamp():
    """CON-5 (#268): a policy of this class may SAMPLE during action
    selection. Unseeded, the same graph, seed and environment can produce
    different trajectories — precisely what CON-5 forbids, arriving through
    a source the determinism contract does not yet name.

    The seed must come from SIM time (reproducible under ADR-30 lockstep),
    never wall time, and never be omitted."""
    import inspect

    from aisle.nodes import vla_backend, vla_smolvla

    node_src = inspect.getsource(vla_smolvla.main)
    assert "seed=obs_ns" in node_src, "inference must be seeded from the sim stamp"
    assert "time.time" not in node_src and "perf_counter" not in node_src

    backend_src = inspect.getsource(vla_backend.select_chunk)
    assert "torch.manual_seed" in backend_src
