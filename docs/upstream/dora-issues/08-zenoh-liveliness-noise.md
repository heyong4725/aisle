# Noise: zenoh_ext "Received malformed liveliness token key expression" warnings flood node logs

**Filed as [dora-rs/dora#2923](https://github.com/dora-rs/dora/issues/2923) (2026-07-31).**

**Versions:** dora-cli 1.0.0-rc.4; API from git rev `7eb4a5f8b`; zenoh via bundled deps.

Every node subscribing to another node's output logs warnings like:

```
WARN zenoh_ext::advanced_subscriber AdvancedSubscriber{}: Received malformed liveliness
token key expression: dora/default/<uuid>/output/<node>/<topic>/@schema/@adv/pub/<hash>/39/_
```

one per (subscriber, publisher) pair at startup — a 9-node graph produces dozens, and
with reconnects/dynamic nodes they recur. They appear harmless (dataflows work), but they
dominate log tails and hide real warnings. Ask: either emit liveliness tokens in the
format `zenoh_ext` expects, or downgrade/silence this specific mismatch if it is expected
with dora's key layout.
