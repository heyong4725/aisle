"""Node-level tests for the reset service's reply contract (SPEC 040 RST-1/2).

Issue #192 moved every episode-state consumer off the bridge and onto this
node's `reset_done` output, on the premise that the service answers on EVERY
route: it relays the bridge on teleport, replies directly on behavioral
success, and forwards a teleport on behavioral exhaustion.

That premise had no test. `route_reset` (the pure dispatcher) was unit-tested
and the graph wiring is checked statically, but nothing exercised the node
itself — so "the service always replies" was an assertion in a commit
message. If it is ever false, every consumer in every graph now waits
forever on a boundary that never comes, which is a strictly worse failure
than the one #192 fixed.

dora is faked (CON-12 keeps the import inside `main()`), and the behavioral
runtime is stubbed so no model load or sim is needed.
"""

import sys
import types

import numpy as np
import pyarrow as pa
import pytest

pytestmark = pytest.mark.unit

TELEPORT, BEHAVIORAL = 0, 1


class FakeNode:
    def __init__(self, events):
        self._events = events
        self.sent: list[tuple[str, list, dict]] = []

    def __iter__(self):
        return iter(self._events)

    def send_output(self, topic, value, metadata=None):
        self.sent.append((topic, list(value.to_pylist()), metadata or {}))


class StubRuntime:
    """The BehavioralRuntime surface `service.main()` actually touches.

    `succeed_after` joint_state events flips the outcome, mirroring the real
    runtime settling asynchronously rather than inside `start()`.
    """

    def __init__(self, *, succeed_after: int = 1, outcome: str = "success", **_kwargs):
        self.succeed_after = succeed_after
        self.final_outcome = outcome
        self.seed = 0
        self.request_meta: dict = {}
        self.attempts = 0
        self.outcome = None
        self._started = False
        self._ticks = 0

    @property
    def active(self) -> bool:
        return self._started

    def start(self, seed: int, request_meta: dict) -> None:
        self.seed = seed
        self.request_meta = dict(request_meta)
        self.attempts = 1
        self.outcome = None
        self._started = True
        self._ticks = 0

    def on_bridge_info(self, info):  # pragma: no cover - not exercised here
        pass

    def on_rgb(self, rgb):  # pragma: no cover
        pass

    def on_depth(self, depth):  # pragma: no cover
        pass

    def on_joint_state(self, qpos):
        self._ticks += 1
        if self._ticks >= self.succeed_after:
            self.outcome = self.final_outcome
        return None, None


def _inp(topic, value, metadata=None):
    return {"type": "INPUT", "id": topic, "value": value, "metadata": metadata or {}}


def reset_request(seed: int, mode: int, request_id: str = "req-1", **extra) -> dict:
    return _inp(
        "reset",
        pa.array(np.array([seed, mode], dtype=np.uint32)),
        {"request_id": request_id, **extra},
    )


def bridge_reply(**metadata) -> dict:
    """What dora-genesis sends back after a teleport."""
    return _inp("reset_done", pa.array(np.array([1], dtype=np.uint32)), metadata)


def joint_state(sim_ns: int = 1_000_000) -> dict:
    return _inp(
        "joint_state",
        pa.array(np.zeros(9, dtype=np.float32)),
        {"sim_time_ns": sim_ns},
    )


def run_service(events, monkeypatch, **runtime_kwargs) -> FakeNode:
    node = FakeNode(events)
    fake_dora = types.ModuleType("dora")
    fake_dora.Node = lambda: node
    monkeypatch.setitem(sys.modules, "dora", fake_dora)

    import aisle.reset.runtime as runtime_mod
    import aisle.verifier.models as models_mod
    from aisle.scenes import pharmacy

    monkeypatch.setattr(
        runtime_mod, "BehavioralRuntime", lambda **kw: StubRuntime(**kw, **runtime_kwargs)
    )
    monkeypatch.setattr(models_mod, "load_pinned", lambda *_a, **_k: object())
    monkeypatch.setattr(pharmacy, "load_meds", lambda *_a, **_k: {})
    monkeypatch.setattr(pharmacy, "resolve_layout", lambda *_a, **_k: {})

    from aisle.reset.service import main

    main()
    return node


def replies(node: FakeNode) -> list[dict]:
    return [m for topic, _, m in node.sent if topic == "reset_done"]


def forwards(node: FakeNode) -> list[list]:
    return [v for topic, v, _ in node.sent if topic == "bridge_reset"]


def test_teleport_is_forwarded_and_its_reply_relayed(monkeypatch):
    """RST-1: the teleport route reaches the bridge, and the bridge's reply
    comes back out on the SERVICE's output — which is the only edge
    episode-state consumers listen to since issue #192."""
    node = run_service(
        [reset_request(7, TELEPORT), bridge_reply(sim_time_ns=555, env_id=2, request_id="req-1")],
        monkeypatch,
    )
    assert forwards(node) == [[7, TELEPORT]]
    assert len(replies(node)) == 1
    relayed = replies(node)[0]
    # the bridge's own routing/timing keys survive the relay
    assert relayed["sim_time_ns"] == 555
    assert relayed["env_id"] == 2


def test_behavioral_success_replies_without_touching_the_bridge(monkeypatch):
    """RST-2 + issue #192, the route that motivated the whole fix: a
    successful behavioral reset never forwards to the bridge, so
    `dora-genesis/reset_done` cannot fire. The service must answer itself,
    or every consumer waits forever."""
    node = run_service(
        [reset_request(4, BEHAVIORAL, env_id=3), joint_state(sim_ns=9_000)],
        monkeypatch,
    )
    assert forwards(node) == [], "behavioral success must not teleport"
    assert len(replies(node)) == 1, "the service did not answer a behavioral reset"
    reply = replies(node)[0]
    assert reply["env_id"] == 3, "the guard slices its per-env boundary on this (BRG-5)"
    assert reply["sim_time_ns"] == 9_000, "post-motion sim time (BRG-4 parity)"
    assert reply["fallback"] is False


def test_behavioral_exhaustion_falls_back_and_still_answers_exactly_once(monkeypatch):
    """The third route. Exhaustion forwards a TELEPORT and lets the bridge's
    reply be relayed — so there must be exactly ONE reset_done, not two.
    Consumers advance an episode epoch on each reply (issue #179), and a
    double-fire would desync nav's epoch from its producer's and make nav
    refuse every goal of the new episode."""
    node = run_service(
        [
            reset_request(5, BEHAVIORAL),
            joint_state(),
            bridge_reply(sim_time_ns=42, request_id="req-1"),
        ],
        monkeypatch,
        outcome="exhausted",
    )
    assert forwards(node) == [[5, TELEPORT]], "exhaustion must fall back to a teleport"
    assert len(replies(node)) == 1, f"expected exactly one boundary, got {replies(node)}"


def test_a_refused_request_still_answers(monkeypatch):
    """ADR-8/TC-6: a malformed payload is refused loudly — but it must still
    produce a reply, because since #192 a silent refusal would hang every
    consumer rather than just the requester."""
    node = run_service(
        [_inp("reset", pa.array(np.array([1, 2, 3], dtype=np.uint32)), {"request_id": "req-2"})],
        monkeypatch,
    )
    assert forwards(node) == []
    assert len(replies(node)) == 1
    assert "error" in replies(node)[0]


def test_the_episode_epoch_is_monotonic_across_mixed_routes(monkeypatch):
    """CON-5 + issue #179: consumers use `reset_done`'s seq as the episode
    epoch, and nav refuses goals stamped with a stale one. Because every
    reply now comes from this single node, the seq must advance by one per
    boundary regardless of which route produced it — a per-route counter
    would restart and make epochs ambiguous."""
    node = run_service(
        [
            reset_request(1, TELEPORT),
            bridge_reply(sim_time_ns=1),
            reset_request(2, BEHAVIORAL),
            joint_state(sim_ns=2),
            reset_request(3, TELEPORT),
            bridge_reply(sim_time_ns=3),
        ],
        monkeypatch,
    )
    seqs = [m["seq"] for m in replies(node)]
    assert seqs == [1, 2, 3], f"episode epochs are not monotonic across routes: {seqs}"


def test_a_request_without_request_id_is_dropped_with_no_reply(monkeypatch):
    """TC-6: with no request_id there is nothing to correlate a reply TO, so
    the service drops it. Pinned deliberately — it is the ONE path that
    answers nothing, and after #192 that means every consumer misses this
    boundary. Only `rollout-client` issues resets and `make_sender` always
    stamps a request_id, so it is unreachable in the real graphs; this test
    exists so the assumption is written down rather than assumed."""
    node = run_service(
        [_inp("reset", pa.array(np.array([1, TELEPORT], dtype=np.uint32)), {})], monkeypatch
    )
    assert replies(node) == []
    assert forwards(node) == []


def _run_guard(events, monkeypatch) -> FakeNode:
    """Drive budget_guard.main() over `events` on the mobile profile."""
    node = FakeNode(events)
    fake_dora = types.ModuleType("dora")
    fake_dora.Node = lambda: node
    monkeypatch.setitem(sys.modules, "dora", fake_dora)
    monkeypatch.setenv("AISLE_EMBODIMENT", "mobile")
    monkeypatch.setenv("AISLE_SCENE", "store")

    from aisle.nodes.budget_guard import main as guard_main

    guard_main(clock=lambda: 100.0)
    return node


def _base_cmd(v: float) -> dict:
    return _inp("base_cmd", pa.array(np.array([v, 0.0], dtype=np.float32)), {"env_id": 0})


def _boundary(**metadata) -> dict:
    return _inp("reset_done", pa.array(np.array([1], dtype=np.uint32)), {"env_id": 0, **metadata})


def test_a_real_boundary_retracts_a_latched_base_command(monkeypatch):
    """The control for the test below: on a genuine boundary the guard
    emits an explicit zero so a pre-reset command still in flight to the
    bridge cannot re-latch unwatched (MOB-3, PR #156 review). This is the
    observable that tells the two branches apart."""
    node = _run_guard([_base_cmd(0.4), _boundary()], monkeypatch)
    safes = [v for topic, v, _ in node.sent if topic == "base_cmd_safe"]
    assert safes and safes[0][0] > 0.0, f"the base command never latched: {safes}"
    assert safes[-1] == [0.0, 0.0], f"a real boundary did not retract the latch: {safes}"


def test_a_refusal_is_not_an_episode_boundary_for_the_guard(monkeypatch):
    """BG-2/ADR-8 (issue #192 review): the guard re-references velocity and
    hold state to the HOME qpos on a boundary, because after a teleport the
    robot IS at home. A refused reset never touched the sim, so that claim
    is false — clamping the next command against a false origin permits a
    larger real jump than the limit allows, and re-anchoring the BG-2 timer
    restarts a budget for an episode that never began.

    Refusal replies only reach the guard because #192 moved the boundary
    onto the reset service's output, so this is a hole that change opened.
    Compared against the control above: a refusal must NOT retract the
    latch, because no reset happened."""
    node = _run_guard([_base_cmd(0.4), _boundary(error="unsupported mode 2")], monkeypatch)
    safes = [v for topic, v, _ in node.sent if topic == "base_cmd_safe"]
    assert safes and safes[0][0] > 0.0, f"the base command never latched: {safes}"
    assert safes[-1] != [0.0, 0.0], (
        f"the guard treated a REFUSED reset as an episode boundary: {safes}"
    )


def test_a_non_numeric_payload_is_refused_not_fatal(monkeypatch):
    """BG-3/TC-6 (issue #192 review): the service is now the single boundary
    authority for every consumer in every graph, and dora does not restart
    nodes — so an unparseable request must produce a refusal reply, never an
    exception out of the event loop. `except ValueError` alone let a
    non-numeric payload through, because that raises TypeError."""
    node = run_service(
        [_inp("reset", pa.array(["not-a-number"]), {"request_id": "req-3"})],
        monkeypatch,
    )
    assert len(replies(node)) == 1, "a malformed request killed the boundary authority"
    assert "error" in replies(node)[0]
    assert forwards(node) == []
