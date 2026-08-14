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


def run_service(events, monkeypatch, *, runtime_load_ticks: int = 0, **runtime_kwargs) -> FakeNode:
    node = FakeNode(events)
    fake_dora = types.ModuleType("dora")
    fake_dora.Node = lambda: node
    monkeypatch.setitem(sys.modules, "dora", fake_dora)

    import aisle.reset.runtime as runtime_mod
    import aisle.verifier.models as models_mod
    from aisle.scenes import pharmacy

    # CON-5: injected so t_reset_ms is reproducible. Each call advances 0.25 s,
    # so an elapsed measurement is a deterministic multiple rather than a race.
    ticks = iter(range(10_000))

    def clock():
        return next(ticks) * 0.25

    def build_runtime(**kw):
        # `runtime_load_ticks` stands in for the ~2 s Owlv2 load real
        # construction pays. Without it the fake builds instantly and NO test
        # can tell whether the reset clock starts above or below get_runtime()
        # — which is exactly how that bug shipped (round-2 review).
        for _ in range(runtime_load_ticks):
            next(ticks)
        return StubRuntime(**kw, **runtime_kwargs)

    monkeypatch.setattr(runtime_mod, "BehavioralRuntime", build_runtime)
    monkeypatch.setattr(models_mod, "load_pinned", lambda *_a, **_k: object())
    monkeypatch.setattr(pharmacy, "load_meds", lambda *_a, **_k: {})
    monkeypatch.setattr(pharmacy, "resolve_layout", lambda *_a, **_k: {})

    from aisle.reset.service import main

    main(clock=clock)
    return node


def replies(node: FakeNode) -> list[dict]:
    return [m for topic, _, m in node.sent if topic == "reset_done"]


def refusals(node: FakeNode) -> list[dict]:
    """ADR-34: refusals answer on their own topic, never on the boundary."""
    return [m for topic, _, m in node.sent if topic == "reset_refused"]


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


def test_a_refused_request_still_answers_on_its_own_topic(monkeypatch):
    """ADR-8/TC-6/ADR-34: a malformed payload is refused loudly — it must
    still produce a reply (a silent refusal hangs the requester), and since
    issue #195 that reply leaves on `reset_refused`, NOT on the boundary."""
    node = run_service(
        [_inp("reset", pa.array(np.array([1, 2, 3], dtype=np.uint32)), {"request_id": "req-2"})],
        monkeypatch,
    )
    assert forwards(node) == []
    assert len(refusals(node)) == 1
    assert "error" in refusals(node)[0]
    assert refusals(node)[0]["request_id"] == "req-2"  # TC-6 correlation survives the move
    assert replies(node) == [], "a refusal reached the episode boundary topic (ADR-34)"


def test_the_boundary_topic_never_carries_a_refusal(monkeypatch):
    """The single guarantee ADR-34 buys, pinned on its own.

    `budget_guard` and `label_reader` each used to filter `metadata["error"]`
    off `reset_done`; ADR-34 DELETED both filters, so from here on the
    guard re-references velocity/hold state to home on every reply it hears,
    and the OCR session clears its read barrier on every one. That is only
    safe while this holds. Every refusable shape in one test, because the
    cost of a leak is silent: a clamp against a false origin permits a
    larger real jump than the limit allows."""
    for payload, meta in (
        (pa.array(np.array([1, 2, 3], dtype=np.uint32)), {"request_id": "wrong-shape"}),
        (pa.array(["not-a-number"]), {"request_id": "non-numeric"}),
        (pa.array(np.array([4, 9], dtype=np.uint32)), {"request_id": "unknown-mode"}),
    ):
        node = run_service([_inp("reset", payload, meta)], monkeypatch)
        assert len(refusals(node)) == 1, (payload, refusals(node))
        assert replies(node) == [], f"{meta['request_id']} refusal rode the boundary topic"
        assert forwards(node) == [], f"{meta['request_id']} was forwarded to the bridge"


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


def test_a_refusal_does_not_advance_the_episode_epoch(monkeypatch):
    """CON-5 + issue #179, the case the sibling test cannot see: a refusal
    is not a boundary, so it must not consume a boundary sequence number.

    While refusals shared `reset_done` they also shared its counter, so a
    refused request between two real resets made the epoch jump 1 -> 3.
    Consumers read that seq as the episode epoch and nav REFUSES goals
    stamped with a stale one, so the gap is not cosmetic. ADR-34 gives
    `reset_refused` its own sequence; this pins that it did."""
    node = run_service(
        [
            reset_request(1, TELEPORT),
            bridge_reply(sim_time_ns=1),
            _inp("reset", pa.array(np.array([9, 9, 9], dtype=np.uint32)), {"request_id": "bad1"}),
            reset_request(2, TELEPORT),
            bridge_reply(sim_time_ns=2),
            _inp("reset", pa.array(np.array([8, 8, 8], dtype=np.uint32)), {"request_id": "bad2"}),
        ],
        monkeypatch,
    )
    assert [m["seq"] for m in replies(node)] == [1, 2], (
        f"a refusal consumed an episode epoch: {[m['seq'] for m in replies(node)]}"
    )
    # TWO refusals: with one, `seq_refused = 1` (a constant) is
    # indistinguishable from a counter, and TC-2 wants per-topic MONOTONIC,
    # not per-topic constant (round-2 review).
    assert [m["seq"] for m in refusals(node)] == [1, 2], (
        f"reset_refused's sequence does not advance: {[m['seq'] for m in refusals(node)]}"
    )


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


def test_the_guard_now_trusts_every_reply_on_the_boundary_topic(monkeypatch):
    """ADR-34 (issue #195) replaced the guard's `metadata["error"]` filter
    with a structural guarantee, and this pins the consequence honestly: the
    guard no longer inspects reply metadata at all, so a reply carrying an
    `error` key IS treated as a boundary here.

    That is safe ONLY because refusals no longer ride this topic — the
    service publishes them on `reset_refused`, which
    `test_the_boundary_topic_never_carries_a_refusal` pins at the node and
    `test_refusals_ride_their_own_topic_and_reach_only_the_requester` pins
    in every graph. If either of those goes red, the hazard BG-2/ADR-8
    described is live again: velocity and hold state re-referenced to a home
    the robot is not at, clamping the next command against a false origin.
    Read this test with those two, never alone."""
    node = _run_guard([_base_cmd(0.4), _boundary(error="stale key from somewhere")], monkeypatch)
    safes = [v for topic, v, _ in node.sent if topic == "base_cmd_safe"]
    assert safes and safes[0][0] > 0.0, f"the base command never latched: {safes}"
    assert safes[-1] == [0.0, 0.0], (
        f"the guard still branches on reply metadata; ADR-34 deleted that filter: {safes}"
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
    assert len(refusals(node)) == 1, "a malformed request killed the boundary authority"
    assert "error" in refusals(node)[0]
    assert forwards(node) == []


def test_the_behavioral_reply_is_tc6_complete(monkeypatch):
    """TC-6 (issue #194): the reply carries `seed`, `mode`, `t_reset_ms`. The
    behavioral-success route carried NONE of the three — and it is the route
    that can take three real motion attempts, so it is exactly where RST-1's
    <2 s budget most needs to be auditable. `t_reset_ms` measured from the
    request, not from a teleport that never happened."""
    node = run_service(
        [reset_request(9, BEHAVIORAL), joint_state(sim_ns=7_000)],
        monkeypatch,
    )
    assert len(replies(node)) == 1
    reply = replies(node)[0]
    assert reply["seed"] == 9
    assert reply["mode"] == BEHAVIORAL
    # EXACT, not >=: the injected clock advances 0.25 s per read, so one read
    # separates this request from its reply. `>= 250` was satisfied by any two
    # distinct reads and so pinned only "a clock was read twice" — it survived
    # moving the anchor out of the request branch entirely (round-2 review).
    assert reply["t_reset_ms"] == 250, reply["t_reset_ms"]


def test_t_reset_ms_measures_THIS_request_not_the_node_lifetime(monkeypatch):
    """CON-5/RST-1: the anchor is per-request. A node-lifetime anchor passes
    a single-request test and then reports cumulative time since startup on
    every later reset — an unbounded OVERCOUNT of exactly the <2 s budget the
    key exists to audit. Two requests in one stream is the observable that
    tells the two apart: both must read one tick, not 250 then 750."""
    node = run_service(
        [
            reset_request(1, BEHAVIORAL, request_id="a"),
            joint_state(sim_ns=1_000),
            reset_request(2, BEHAVIORAL, request_id="b"),
            joint_state(sim_ns=2_000),
        ],
        monkeypatch,
    )
    assert [r["request_id"] for r in replies(node)] == ["a", "b"]
    assert [r["t_reset_ms"] for r in replies(node)] == [250, 250], replies(node)


def test_t_reset_ms_includes_the_model_load_the_first_request_pays(monkeypatch):
    """RST-1 (round-2 review): the first behavioral request builds the
    runtime, and building it loads Owlv2 — ~2 s against a <2 s budget. The
    `bridge_info` pre-warm moves that cost out of the request only when
    bridge_info arrives first, which nothing enforces. Starting the clock
    below `get_runtime()` would hide the single largest component of the
    number, so the reset that breaches RST-1 would report as compliant.

    Here the load costs one clock tick, so an honest measurement reads TWO:
    the load plus the attempt."""
    node = run_service(
        [reset_request(3, BEHAVIORAL), joint_state()],
        monkeypatch,
        runtime_load_ticks=1,
    )
    assert replies(node)[0]["t_reset_ms"] == 500, replies(node)[0]


def test_the_two_pre_existing_routes_still_report_the_timing_key(monkeypatch):
    """A regression control for the sibling behavioral assertions, NOT
    coverage of the fix: both routes here are untouched by issue #194 — the
    teleport relay copies the bridge's metadata verbatim and the refusal's 0
    comes from `refusal_reply_metadata`. Named for what it pins, because an
    earlier revision claimed "every route" while omitting both behavioral
    routes, and the exhaustion route still reports only the fallback
    teleport's duration (open on #194)."""
    relay = run_service(
        [reset_request(1, TELEPORT), bridge_reply(sim_time_ns=1, t_reset_ms=1234, seed=1, mode=0)],
        monkeypatch,
    )
    assert replies(relay)[0]["t_reset_ms"] == 1234

    refused = run_service(
        [_inp("reset", pa.array(np.array([1, 2, 3], dtype=np.uint32)), {"request_id": "r"})],
        monkeypatch,
    )
    assert len(refusals(refused)) == 1
    assert refusals(refused)[0]["t_reset_ms"] == 0
