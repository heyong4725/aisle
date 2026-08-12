"""Topic-contract helpers shared by every AISLE node (SPEC 010 TC-2)."""

from __future__ import annotations


def stamp(metadata: dict, seq: int) -> dict:
    """TC-2 mandatory output keys on every node output: defaults for
    sim_time_ns/env_id when the upstream message carries none, upstream
    values preserved when it does, and the sender's OWN per-topic
    monotonic seq."""
    return {"sim_time_ns": 0, "env_id": 0, **metadata, "seq": seq}


def parse_sim_stamp(metadata: dict) -> int | None:
    """TOTAL sim_time_ns read (TC-2 trust boundary): None when the stamp is
    absent, zero, or malformed — all three mean 'no usable sim clock on
    this message'. Zero maps to None because `stamp()` above defaults a
    missing stamp to 0, so a genuine 0 is indistinguishable from an
    unstamped source; a consumer that treated 0 as a real time would
    anchor a comparison at the start of the run.

    Total by construction: a malformed stamp from any upstream node must
    degrade the consumer's decision, never raise out of its event loop
    (BG-3; issue #160 item 1, generalized here once three nodes needed it)."""
    try:
        stamp_ns = int(metadata.get("sim_time_ns", 0))
    except (TypeError, ValueError, OverflowError):
        return None
    return stamp_ns if stamp_ns > 0 else None


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
