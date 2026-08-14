"""Every graph's episode boundary comes from one authority (issue #192).

A successful BEHAVIORAL reset (RST-2) is replied to by the reset SERVICE and
never reaches the bridge, so `dora-genesis/reset_done` does not fire. Any
consumer wired to the bridge therefore misses the boundary entirely in that
mode: the guard's BG-2 episode timer never re-anchors, the drivers never
`clear()`, and issue #179 comes back — silently, one env var away, in a
config that already exists (`expert_t1_behavioral.yaml` was wired to the
bridge, in the graph named for behavioral resets).

The service replies on EVERY route — it relays the bridge on teleport,
answers directly on behavioral success, and falls back to teleport on
exhaustion — so it is the single authority. These tests pin that, because
the failure has no symptom at validation time: a bridge-wired graph
validates, runs, and quietly leaks episode state.
"""

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
GRAPHS = sorted((ROOT / "graphs").glob("*.yaml"))

#: The node that owns the boundary. It is the ONLY legitimate consumer of
#: the bridge's reset_done: it relays that reply onto its own output.
RELAY = "reset"


def _reset_done_source(node: dict):
    """The node's reset_done source, or None if it has no such input."""
    port = (node.get("inputs") or {}).get("reset_done")
    if isinstance(port, dict):
        return port.get("source")
    return port


def _consumers(path: Path) -> dict:
    """{node_id: source} for every reset_done consumer in the graph."""
    doc = yaml.safe_load(path.read_text()) or {}
    return {
        n["id"]: src for n in doc.get("nodes", []) if (src := _reset_done_source(n)) is not None
    }


def graphs_with_reset_done():
    for path in GRAPHS:
        consumers = _consumers(path)
        if consumers:
            yield path, consumers


def test_the_corpus_is_not_empty():
    """Guards the two tests below from silently passing on zero graphs."""
    found = list(graphs_with_reset_done())
    # every graph, not "at least ten": a deleted graph must change this
    # count rather than slip under a floor
    assert len(found) == len(GRAPHS), sorted({p.name for p in GRAPHS} - {p.name for p, _ in found})


@pytest.mark.parametrize("path", GRAPHS, ids=lambda p: p.name)
def test_only_the_relay_consumes_the_bridges_reset_done(path):
    """RST-2 + issue #192: everyone except the relay takes the boundary from
    the service. Wiring a consumer to `dora-genesis/reset_done` makes it
    blind to behavioral resets."""
    offenders = [
        node_id
        for node_id, source in _consumers(path).items()
        if node_id != RELAY and "dora-genesis" in source
    ]
    assert not offenders, (
        f"{path.name}: {offenders} take reset_done straight from the bridge — they will "
        "miss the boundary entirely under AISLE_RESET_MODE=behavioral (issue #192)"
    )


@pytest.mark.parametrize("path", GRAPHS, ids=lambda p: p.name)
def test_all_episode_state_consumers_agree_on_the_boundary(path):
    """CON-5: consumers that cut the episode at different instants leave the
    run in mixed state — one node already in episode N+1 while another is
    still finishing N. Whatever the source is, they must share it."""
    consumers = {nid: src for nid, src in _consumers(path).items() if nid != RELAY}
    if not consumers:
        pytest.skip(f"{path.name} has no episode-state consumers")
    assert len(set(consumers.values())) == 1, (
        f"{path.name}: episode-state consumers disagree on the boundary — {consumers}"
    )


@pytest.mark.parametrize("path", GRAPHS, ids=lambda p: p.name)
def test_the_relay_still_hears_the_bridge(path):
    """The other half: the service can only relay the teleport reply if it
    subscribes to it. Rewiring everyone to the service is worthless if the
    service itself was moved off the bridge."""
    doc = yaml.safe_load(path.read_text()) or {}
    relay = [n for n in doc.get("nodes", []) if n["id"] == RELAY]
    if not relay:
        pytest.skip(f"{path.name} has no reset service")
    source = _reset_done_source(relay[0])
    assert source and "dora-genesis" in source, (
        f"{path.name}: the reset service does not consume the bridge's reset_done "
        f"(got {source!r}) — the teleport route would never be relayed"
    )
    outputs = relay[0].get("outputs") or []
    assert "reset_done" in outputs, f"{path.name}: the reset service publishes no reset_done"


def _node_sources(path: Path):
    """{node_id: source .py path} for every python node in the graph."""
    doc = yaml.safe_load(path.read_text()) or {}
    out = {}
    for node in doc.get("nodes", []):
        rel = str(node.get("path", ""))
        if rel.endswith(".py"):
            out[node["id"]] = (path.parent / rel).resolve()
    return out


@pytest.mark.parametrize("path", GRAPHS, ids=lambda p: p.name)
def test_every_node_that_handles_the_boundary_is_wired_to_it(path):
    """RST-1 + issue #179: the bug that started all of this was a node whose
    code handles `reset_done` while the graph never delivered it —
    waypoint-nav ran for months that way.

    The sibling tests here check the SOURCE of edges that exist; none of
    them notices an edge that is simply absent, so deleting a consumer's
    input entirely passed all of them. This is the one that catches it, and
    it is derived from the node's own source rather than a hand-kept list,
    so a new stateful node is covered the day it is written."""
    consumers = _consumers(path)
    missing = []
    for node_id, src in _node_sources(path).items():
        if not src.is_file() or node_id in consumers:
            continue
        text = src.read_text()
        if '== "reset_done"' in text or "'reset_done'" in text:
            missing.append((node_id, src.name))
    assert not missing, (
        f"{path.name}: {missing} handle reset_done in code but the graph never delivers it — "
        "the node silently misses every episode boundary (issue #179)"
    )


def _edges_from(node: dict, topic: str) -> dict:
    """{local port name: source} for every input of `node` sourced from a
    `*/topic` edge — keyed on the SOURCE, never on the local port name.

    dora delivers an event under the LOCAL PORT NAME, so
    `reset_done: {source: reset/reset_refused}` makes a boundary consumer
    dispatch a refusal down its `reset_done` branch. Keying on the port name
    is blind to exactly that, which is the one wiring that matters
    (round-2 review of #208: rewriting every consumer's source that way left
    all 118 tests green)."""
    found = {}
    for port, raw in (node.get("inputs") or {}).items():
        source = raw.get("source") if isinstance(raw, dict) else raw
        if isinstance(source, str) and source.endswith(f"/{topic}"):
            found[port] = source
    return found


@pytest.mark.parametrize("path", GRAPHS, ids=lambda p: p.name)
def test_refusals_ride_their_own_topic_and_reach_only_the_requester(path):
    """TC-6/ADR-34 (issue #195): a refused reset is a REPLY, not a boundary.

    It goes to the one requester; the boundary is broadcast state. Feeding a
    refusal to a boundary consumer puts back exactly what this change
    removes — the guard re-referencing hold state to a home the robot is not
    at, and the OCR session clearing a live read request — and both nodes'
    `error` filters are gone, so nothing downstream would catch it.

    Checked on EDGE SOURCES and on the producer, both learned the hard way:
    keying on the local port name missed the only dangerous shape, and not
    pinning the producer let `dora-genesis/reset_refused` (a topic with no
    producer) pass while the requester silently never heard a refusal."""
    doc = yaml.safe_load(path.read_text()) or {}
    nodes = doc.get("nodes", [])
    service = [n for n in nodes if n["id"] == RELAY]
    if not service:
        pytest.skip(f"{path.name} has no reset service")
    assert "reset_refused" in (service[0].get("outputs") or []), (
        f"{path.name}: the reset service declares no reset_refused output — dora DROPS a "
        "send_output to an undeclared output, so every refusal would vanish into a stderr "
        "warning and the requester would wait forever (ADR-34)"
    )
    consumers = {n["id"]: _edges_from(n, "reset_refused") for n in nodes}
    consumers = {nid: edges for nid, edges in consumers.items() if edges}
    assert set(consumers) == {"rollout-client"}, (
        f"{path.name}: reset_refused reaches {sorted(consumers)}; only the requester may "
        "consume it — a boundary consumer that hears a refusal is the ADR-34 bug returning"
    )
    assert list(consumers["rollout-client"].values()) == [f"{RELAY}/reset_refused"], (
        f"{path.name}: the requester's refusal edge is {consumers['rollout-client']}, not "
        f"{RELAY}/reset_refused — a refusal from any other producer never arrives"
    )


@pytest.mark.parametrize("path", GRAPHS, ids=lambda p: p.name)
def test_every_node_that_handles_a_refusal_is_wired_to_it(path):
    """The issue #179 shape, for the new topic: a node whose code handles
    `reset_refused` while the graph never delivers it waits forever on a
    reply published to nobody. Derived from node SOURCE, not a hand-kept
    list, so it covers a node the day it is written. BOTH quote forms, like
    the reset_done sibling above — a single-quoted handler slipped past the
    first revision of this test (round-2 review)."""
    doc = yaml.safe_load(path.read_text()) or {}
    wired = {n["id"] for n in doc.get("nodes", []) if _edges_from(n, "reset_refused")}
    missing = []
    for node_id, src in _node_sources(path).items():
        if not src.is_file() or node_id in wired:
            continue
        text = src.read_text()
        if '== "reset_refused"' in text or "== 'reset_refused'" in text:
            missing.append((node_id, src.name))
    assert not missing, (
        f"{path.name}: {missing} handle reset_refused in code but the graph never delivers it"
    )
