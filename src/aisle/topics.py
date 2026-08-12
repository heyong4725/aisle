"""Topic-contract helpers shared by every AISLE node (SPEC 010 TC-2)."""

from __future__ import annotations


def stamp(metadata: dict, seq: int) -> dict:
    """TC-2 mandatory output keys on every node output: defaults for
    sim_time_ns/env_id when the upstream message carries none, upstream
    values preserved when it does, and the sender's OWN per-topic
    monotonic seq."""
    return {"sim_time_ns": 0, "env_id": 0, **metadata, "seq": seq}


def make_sender(node, env_pin: int | None = None):
    """TC-2 sender: per-topic monotonic seq + stamp around node.send_output.
    Every AISLE node's send path in one place (six copies before this).
    A PINNED sender (fleet mode, BRG-5) stamps its env_id on every output
    so downstream per-env consumers and the bridge route correctly."""
    seq: dict[str, int] = {}

    def send(topic: str, value, metadata: dict) -> None:
        seq[topic] = seq.get(topic, 0) + 1
        if env_pin is not None:
            metadata = {**metadata, "env_id": env_pin}
        node.send_output(topic, value, stamp(metadata, seq[topic]))

    return send


def env_pin_from_env(environ) -> int | None:
    """Fleet mode (BRG-5): the node's pinned env slot from AISLE_ENV_PIN.
    None (unset) = single-env behavior, accept everything. Junk refuses
    loudly — a typo'd pin must not silently accept every env's traffic."""
    raw = environ.get("AISLE_ENV_PIN", "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError(f"AISLE_ENV_PIN must be a non-negative int, got {raw!r}")
    return int(raw)


def env_accepts(metadata: dict, env_pin: int | None) -> bool:
    """Whether a PINNED node should process this INPUT event. The bridge
    fans every env's messages out on shared topics; a pinned node owns
    exactly one env's stream and must drop the rest. Unpinned nodes
    (single-env graphs) accept everything — byte-identical behavior.
    Events WITHOUT an env_id (dora timer ticks) are env-agnostic and
    pass every pin: a pinned client still needs its tick to fire."""
    if env_pin is None or "env_id" not in metadata:
        return True
    return int(metadata["env_id"]) == env_pin
