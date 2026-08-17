"""vla-policy-smolvla node (Phase 5.1, next-phases.md; ADR-38): SmolVLA
behind the typed contract.

Consumes rgb_overhead + rgb_wrist + joint_state + the task instruction
(derived from target_request), emits joint_cmd/gripper_cmd CHUNKS under
the ADR-38 preemption rule: one in-flight chunk, a new inference
replaces the queued remainder at the next action boundary, reset
flushes, and a chunk computed from observations older than
VLA_STALE_NS at emission is dropped. Every element still traverses the
budget guard (safety_class: motion).

The model (lerobot smolvla_base, `vla` extra) is LAZY-loaded at the
first inference so import and unit tests never touch torch (CON-12);
weights identity is pinned by HF revision in the manifest.
"""

from __future__ import annotations

VLA_STALE_NS = 500_000_000  # ADR-38 rule 4
CHUNK_HZ = 10  # actions consumed per second of sim time (one per tick)


class ChunkQueue:
    """Pure ADR-38 core: preemption, reset flush, staleness floor."""

    def __init__(self, stale_ns: int = VLA_STALE_NS) -> None:
        self.stale_ns = stale_ns
        self.chunk: list = []  # queued [q..] joint targets (+gripper)
        self.obs_ns: int | None = None  # sim stamp the chunk was computed from

    def offer(self, chunk: list, obs_ns: int, now_ns: int) -> bool:
        """A new inference result arrives. Stale chunks are dropped
        (rule 4); a fresh one REPLACES the queued remainder (rule 1)."""
        if now_ns - obs_ns > self.stale_ns:
            return False
        self.chunk = list(chunk)
        self.obs_ns = obs_ns
        return True

    def pop(self) -> list | None:
        """The next action at an action boundary; None when drained."""
        if not self.chunk:
            return None
        return self.chunk.pop(0)

    def flush(self) -> None:
        """Episode boundary (rule 3)."""
        self.chunk = []
        self.obs_ns = None


def instruction_from_request(payload: dict) -> str:
    """The task text the policy conditions on — derived from the SAME
    target_request every classical pipeline consumes (no extra
    channel, no blinding change)."""
    med = payload.get("target_med", "the requested item")
    return f"pick the {med} box from the shelf and place it in the tray"


def main() -> None:  # pragma: no cover — exercised by graph tests
    import json
    import os

    import numpy as np
    import pyarrow as pa

    from aisle.topics import env_accepts, env_pin_from_env, make_sender
    from aisle.turn_node import Node

    node = Node()
    send = make_sender(node)
    env_pin = env_pin_from_env(os.environ)
    queue = ChunkQueue()
    policy = None  # lazy (ADR-38 bring-up scope)
    frames: dict = {}
    joint_state = None
    instruction = None
    obs_ns = 0

    def infer():
        nonlocal policy
        if instruction is None or joint_state is None or "wrist" not in frames:
            return None
        if policy is None:
            from aisle.nodes.vla_backend import load_smolvla

            policy = load_smolvla()
        from aisle.nodes.vla_backend import select_chunk

        # CON-5 (#268): the sampler is seeded from the observation's SIM
        # stamp — reproducible under ADR-30 lockstep, unlike wall time and
        # unlike leaving it unseeded.
        return select_chunk(policy, frames, joint_state, instruction, seed=obs_ns)

    for event in node:
        if event["type"] != "INPUT":
            continue
        metadata = event.get("metadata") or {}
        if not env_accepts(metadata, env_pin):
            continue
        eid = event["id"]
        if eid == "reset_done":
            queue.flush()
            instruction = None
        elif eid == "target_request":
            instruction = instruction_from_request(json.loads(event["value"][0].as_py()))
        elif eid in ("rgb_overhead", "rgb_wrist"):
            frames[eid.removeprefix("rgb_")] = (
                np.asarray(event["value"].to_numpy(zero_copy_only=False), dtype=np.uint8),
                metadata,
            )
            obs_ns = int(metadata.get("sim_time_ns", obs_ns))
        elif eid == "joint_state":
            joint_state = np.asarray(
                event["value"].to_numpy(zero_copy_only=False), dtype=np.float32
            )
            # ADR-38 rule 4 (#268): the staleness delta is (current turn -
            # observed frame). Both stamps are SIM time, so the drop decision
            # is reproducible; deriving `now` from wall time would make a
            # loaded host execute a different trajectory with every seed
            # identical. Passing obs_ns for both made the delta identically
            # zero and the rule unreachable — correct under synchronous
            # lockstep inference, and silently unprotected the moment
            # observation and emission are separated, which is what chunks
            # are for.
            now_ns = int(metadata.get("sim_time_ns", obs_ns))
            # action boundary: one queued action per joint_state tick
            step = queue.pop()
            if step is None and instruction is not None:
                chunk = infer()
                if chunk is not None and queue.offer(chunk, obs_ns, now_ns):
                    step = queue.pop()
            if step is not None:
                q, grip = step[:-1], step[-1]
                send("joint_cmd", pa.array(np.asarray(q, dtype=np.float32)), metadata)
                send("gripper_cmd", pa.array([np.float32(grip)]), metadata)


if __name__ == "__main__":
    main()
