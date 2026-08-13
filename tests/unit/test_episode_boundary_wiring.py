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
    assert len(found) >= 10, [p.name for p, _ in found]


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
