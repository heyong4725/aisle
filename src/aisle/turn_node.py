"""Drop-in dora Node wrapper for ADR-30 participants."""

from __future__ import annotations

import json
import os

from aisle.turns import ParticipantTurn, ProtocolError, parse_turn_stamp


class Node:
    """A dora Node that becomes turn-accounted when graph env requests it.

    Participant policy code still sees ordinary INPUT events and calls the
    ordinary ``send_output`` API.  The wrapper owns all scheduling semantics;
    no policy node can accidentally turn transport arrival order into causal
    order or emit a stamped command from a wall handler.
    """

    def __init__(self, raw_node=None, environ=None):
        if raw_node is None:
            from dora import Node as DoraNode

            raw_node = DoraNode()
        self.raw = raw_node
        env = os.environ if environ is None else environ
        self.lockstep = str(env.get("AISLE_LOCKSTEP", "")).strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self.node_id = str(env.get("AISLE_TURN_NODE", "")).strip()
        self.outputs = [v for v in str(env.get("AISLE_TURN_OUTPUTS", "")).split(",") if v]
        self.wall_outputs = {v for v in str(env.get("AISLE_TURN_WALL_OUTPUTS", "")).split(",") if v}
        if self.lockstep and (not self.node_id or "turn_done" not in self.outputs):
            raise SystemExit(
                "lockstep node config refused: AISLE_TURN_NODE and "
                "AISLE_TURN_OUTPUTS including turn_done are required"
            )
        self._active: ParticipantTurn | None = None
        self._wall_handler = False
        self._stop_after_turn = False

    def stop_after_turn(self) -> None:
        """Finish the active watermark before ending iteration cleanly."""
        if self.lockstep and self._active is None:
            raise ProtocolError(f"{self.node_id} cannot stop outside an active turn")
        self._stop_after_turn = True

    def send_output(self, topic, value, metadata=None):
        metadata = dict(metadata or {})
        if not self.lockstep:
            return self.raw.send_output(topic, value, metadata)
        if self._active is not None:
            # Legacy TopicSender always supplies sim_time_ns (default zero).
            # Derived outputs may preserve an episodic input's previous-turn
            # stamp; that identifies the data they consumed, not the turn in
            # which they are emitted. The wrapper is the authoritative sender
            # and overwrites it with the current turn. Reject only partial
            # claims, which cannot identify either meaning safely.
            if "turn_epoch" in metadata or "turn_id" in metadata:
                if not all(key in metadata for key in ("turn_epoch", "turn_id", "sim_time_ns")):
                    raise ProtocolError(f"{self.node_id}/{topic} supplied a partial turn stamp")
                claimed = parse_turn_stamp(metadata)
                allowed = {self._active.stamp, *(self._active.expected_stamps or {}).values()}
                if claimed not in allowed:
                    raise ProtocolError(
                        f"{self.node_id}/{topic} supplied an unrelated turn stamp {claimed}"
                    )
            self._active.record_output(topic)
            metadata = {**metadata, **self._active.stamp.metadata()}
        elif topic not in self.wall_outputs:
            raise ProtocolError(
                f"{self.node_id}/{topic} emitted outside a turn; wall handlers may emit "
                f"only {sorted(self.wall_outputs)}"
            )
        return self.raw.send_output(topic, value, metadata)

    def __iter__(self):
        if not self.lockstep:
            yield from self.raw
            return

        import pyarrow as pa

        participant = ParticipantTurn(self.node_id, self.outputs)
        raw = iter(self.raw)
        while True:
            if participant.ready:
                events = participant.take()
                assert participant.stamp is not None
                events.append(
                    {
                        "type": "INPUT",
                        "id": "turn",
                        "value": pa.array([participant.stamp.turn_id], type=pa.uint64()),
                        "metadata": participant.stamp.metadata(),
                    }
                )
                self._active = participant
                yield from events
                done = participant.close()
                if self._stop_after_turn:
                    done["shutdown"] = True
                self._active = None
                self.raw.send_output(
                    "turn_done",
                    pa.array([participant.stamp.turn_id], type=pa.uint64()),
                    done,
                )
                if self._stop_after_turn:
                    return
                participant = ParticipantTurn(self.node_id, self.outputs)
                continue
            try:
                event = next(raw)
            except StopIteration:
                return
            if event.get("type") != "INPUT":
                yield event
                continue
            metadata = event.get("metadata") or {}
            if event.get("id") == "turn":
                ready_plan = metadata.get("ready_plan")
                if isinstance(ready_plan, str):
                    try:
                        declaration = json.loads(ready_plan)
                    except json.JSONDecodeError as exc:
                        raise ProtocolError("turn ready_plan is malformed JSON") from exc
                    if not isinstance(declaration, dict):
                        raise ProtocolError("turn ready_plan must be an object")
                    own = declaration.get(self.node_id)
                    if own is not None:
                        if not isinstance(own, dict):
                            raise ProtocolError(
                                f"turn ready_plan entry for {self.node_id} is malformed"
                            )
                        participant.open({**metadata, **own})
                elif metadata.get("target_node") == self.node_id:
                    # Compatibility for a directly-authored coordinator.
                    participant.open(metadata)
                continue
            if all(key in metadata for key in ("turn_epoch", "turn_id", "sim_time_ns")):
                participant.buffer(str(event.get("id", "")), event, metadata)
                continue
            if event.get("id") != "tick":
                raise ProtocolError(
                    f"{self.node_id}/{event.get('id')} is an unstamped input in lockstep mode"
                )
            # The wall timer is delivered immediately. send_output enforces
            # that only declared non-turn diagnostics can result.
            self._wall_handler = True
            try:
                yield event
            finally:
                self._wall_handler = False
