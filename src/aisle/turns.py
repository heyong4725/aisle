"""ADR-30 run-to-quiescence protocol primitives.

The runtime-facing dora nodes use these small state machines rather than
reimplementing trust-boundary parsing, watermark accounting, and command
ordering.  They deliberately contain no dora or Genesis imports, which keeps
the protocol exhaustively unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ProtocolError(RuntimeError):
    """A lockstep message violated the closed-turn protocol."""


@dataclass(frozen=True, order=True)
class TurnStamp:
    """One globally ordered turn inside one bridge-process epoch."""

    epoch: int
    turn_id: int
    sim_time_ns: int

    def metadata(self) -> dict[str, int]:
        return {
            "turn_epoch": self.epoch,
            "turn_id": self.turn_id,
            "sim_time_ns": self.sim_time_ns,
        }


def _plain_int(value: Any, *, minimum: int) -> int | None:
    """Return a contract integer, rejecting bools and coercions."""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return None
    return value


def parse_turn_stamp(metadata: dict | None) -> TurnStamp | None:
    """Total TC-2 lockstep stamp parser.

    Turn zero and simulation time zero are real values.  Epoch zero is not:
    epochs start at one so an absent/default integer cannot alias a process.
    """
    if not isinstance(metadata, dict):
        return None
    epoch = _plain_int(metadata.get("turn_epoch"), minimum=1)
    turn_id = _plain_int(metadata.get("turn_id"), minimum=0)
    sim_time_ns = _plain_int(metadata.get("sim_time_ns"), minimum=0)
    if epoch is None or turn_id is None or sim_time_ns is None:
        return None
    return TurnStamp(epoch, turn_id, sim_time_ns)


def require_turn_stamp(metadata: dict | None, *, context: str) -> TurnStamp:
    stamp = parse_turn_stamp(metadata)
    if stamp is None:
        raise ProtocolError(f"{context} has no valid turn_epoch/turn_id/sim_time_ns stamp")
    return stamp


def watermark_metadata(stamp: TurnStamp, outputs: dict[str, int]) -> dict:
    """Build CAP-1's complete, lexically ordered output declaration."""
    names = sorted(outputs)
    counts: list[int] = []
    for name in names:
        count = outputs[name]
        if not isinstance(name, str) or not name:
            raise ProtocolError(f"watermark output name must be non-empty, got {name!r}")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ProtocolError(f"watermark count for {name!r} must be non-negative int")
        counts.append(count)
    return {
        **stamp.metadata(),
        "closed_outputs": names,
        "emitted_counts": counts,
    }


def parse_watermark(metadata: dict | None, *, context: str) -> tuple[TurnStamp, dict[str, int]]:
    """Parse and validate a complete CAP-1 watermark declaration."""
    stamp = require_turn_stamp(metadata, context=context)
    names = metadata.get("closed_outputs")
    counts = metadata.get("emitted_counts")
    if not isinstance(names, list) or not isinstance(counts, list) or len(names) != len(counts):
        raise ProtocolError(f"{context} watermark names/counts must be equal-length lists")
    if any(not isinstance(name, str) or not name for name in names):
        raise ProtocolError(f"{context} watermark contains an invalid output name")
    if names != sorted(names) or len(set(names)) != len(names):
        raise ProtocolError(f"{context} watermark outputs must be unique and lexical")
    if any(isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in counts):
        raise ProtocolError(f"{context} watermark counts must be non-negative integers")
    return stamp, dict(zip(names, counts, strict=True))


class TurnBarrier:
    """Topology-aware participant closure for one open turn at a time.

    ``plan`` contains the bridge id and each participant's input mapping.
    Forward inputs depend on the producer's current-turn watermark;
    episodic inputs depend on its preceding-turn watermark.  The class
    returns newly-ready nodes with the exact expected count per input.
    """

    def __init__(self, plan: dict):
        self.bridge = str(plan.get("bridge", ""))
        participants = plan.get("participants")
        if not self.bridge or not isinstance(participants, dict) or not participants:
            raise ProtocolError("turn plan needs a bridge and at least one participant")
        self.participants: dict[str, dict] = participants
        bridge_outputs = plan.get("bridge_outputs")
        self.bridge_outputs = set(bridge_outputs) if isinstance(bridge_outputs, list) else None
        self.current: TurnStamp | None = None
        self.previous_stamp: TurnStamp | None = None
        self._closed: dict[str, dict[str, int]] = {}
        self._previous: dict[str, dict[str, int]] = {}
        self._scheduled: set[str] = set()
        self.shutdown_requested = False

    @property
    def complete(self) -> bool:
        return self.current is not None and set(self._closed) >= set(self.participants)

    def open_bridge(self, metadata: dict) -> dict[str, dict[str, int]]:
        stamp, outputs = parse_watermark(metadata, context=f"{self.bridge}/sim_turn")
        self._check_output_set(self.bridge, outputs, self.bridge_outputs)
        if self.current is not None and not self.complete:
            raise ProtocolError(f"turn {self.current.turn_id} is still open")
        if self.current is not None:
            expected = TurnStamp(self.current.epoch, self.current.turn_id + 1, stamp.sim_time_ns)
            if stamp.epoch != expected.epoch or stamp.turn_id != expected.turn_id:
                raise ProtocolError(
                    f"bridge opened non-contiguous turn {stamp.epoch}:{stamp.turn_id}; "
                    f"expected {expected.epoch}:{expected.turn_id}"
                )
            self._previous = {
                node: counts for node, counts in self._closed.items() if node != self.bridge
            }
            self.previous_stamp = self.current
        self.current = stamp
        self._closed = {self.bridge: outputs}
        self._scheduled = set()
        self.shutdown_requested = False
        return self._newly_ready()

    def close(self, node: str, metadata: dict) -> dict[str, dict[str, int]]:
        if self.current is None:
            raise ProtocolError("participant closed before the bridge opened a turn")
        if node not in self.participants:
            raise ProtocolError(f"unknown turn participant {node!r}")
        stamp, outputs = parse_watermark(metadata, context=f"{node}/turn_done")
        declared = self.participants[node].get("outputs")
        self._check_output_set(node, outputs, set(declared) if isinstance(declared, list) else None)
        if stamp != self.current:
            raise ProtocolError(
                f"{node} closed {stamp.epoch}:{stamp.turn_id}@{stamp.sim_time_ns}; "
                f"open turn is {self.current.epoch}:{self.current.turn_id}"
                f"@{self.current.sim_time_ns}"
            )
        if node in self._closed:
            raise ProtocolError(f"duplicate turn_done from {node!r}")
        if node not in self._scheduled:
            raise ProtocolError(f"{node!r} closed before its upstreams made it ready")
        self._closed[node] = outputs
        if metadata.get("shutdown") is True:
            self.shutdown_requested = True
        return self._newly_ready()

    @staticmethod
    def _check_output_set(node: str, outputs: dict[str, int], expected: set[str] | None) -> None:
        if expected is not None and set(outputs) != expected:
            raise ProtocolError(
                f"{node} watermark output set {sorted(outputs)} does not match "
                f"declared graph outputs {sorted(expected)}"
            )

    def _expected(self, node: str) -> dict[str, int] | None:
        inputs = self.participants[node].get("inputs", {})
        if not isinstance(inputs, dict):
            raise ProtocolError(f"participant {node!r} inputs must be a mapping")
        expected: dict[str, int] = {}
        for input_name, edge in sorted(inputs.items()):
            if not isinstance(edge, dict):
                raise ProtocolError(f"participant {node!r} input {input_name!r} is malformed")
            source = edge.get("source")
            output = edge.get("output")
            kind = edge.get("edge", "forward")
            if kind == "forward":
                source_counts = self._closed.get(source)
                if source_counts is None:
                    return None
            elif kind == "episodic":
                # There is no turn -1.  Every episodic input is therefore
                # closed with zero at process startup.
                source_counts = self._previous.get(source, {})
            else:
                raise ProtocolError(f"unknown turn edge kind {kind!r}")
            if not isinstance(output, str) or output not in source_counts:
                if kind == "episodic" and not self._previous:
                    expected[input_name] = 0
                    continue
                raise ProtocolError(
                    f"{source!r} watermark omitted output {output!r} needed by {node}/{input_name}"
                )
            expected[input_name] = source_counts[output]
        return expected

    def _newly_ready(self) -> dict[str, dict[str, int]]:
        ready: dict[str, dict[str, int]] = {}
        # Iterate to a fixed point only over nodes whose upstreams were
        # already closed before this call.  Merely scheduling a node does not
        # close it, so descendants cannot become ready prematurely.
        for node in sorted(self.participants):
            if node in self._scheduled or node in self._closed:
                continue
            expected = self._expected(node)
            if expected is not None:
                self._scheduled.add(node)
                ready[node] = expected
        return ready

    def is_verdict_bearing(self, node: str) -> bool:
        return self.participants.get(node, {}).get("verdict_bearing") is True

    def input_stamps(self, node: str) -> dict[str, TurnStamp]:
        """Expected producer stamp per ready input (forward k, episodic k-1)."""
        if self.current is None or node not in self.participants:
            raise ProtocolError(f"cannot resolve input stamps for {node!r} without an open turn")
        resolved = {}
        for input_name, edge in self.participants[node].get("inputs", {}).items():
            if edge.get("edge", "forward") == "episodic" and self.previous_stamp is not None:
                resolved[input_name] = self.previous_stamp
            else:
                # Turn zero has no predecessor and every episodic count is
                # zero, so its placeholder stamp is never matched to data.
                resolved[input_name] = self.current
        return resolved


class ParticipantTurn:
    """Exact input/output accounting for one synchronous participant.

    Dora may deliver data before the barrier's ready signal and may reorder
    ports.  Data is therefore buffered by its contract stamp, checked against
    the ready declaration, and released in input-name/sequence order.
    """

    def __init__(self, node_id: str, outputs: list[str]):
        if not node_id or not outputs or len(set(outputs)) != len(outputs):
            raise ProtocolError("participant needs an id and unique output ports")
        if "turn_done" not in outputs:
            raise ProtocolError("participant outputs must include turn_done")
        self.node_id = node_id
        self.outputs = sorted(outputs)
        self.stamp: TurnStamp | None = None
        self.expected: dict[str, int] | None = None
        self.expected_stamps: dict[str, TurnStamp] | None = None
        self._buffer: dict[str, list[tuple[int, int, TurnStamp, Any]]] = {}
        self._arrival = 0
        self._output_counts = {name: 0 for name in self.outputs}
        self._taken = False
        self._closed = False

    @property
    def ready(self) -> bool:
        if self.stamp is None or self.expected is None or self._closed:
            return False
        return all(
            len(self._buffer.get(name, [])) == count for name, count in self.expected.items()
        )

    def buffer(self, input_name: str, event: Any, metadata: dict) -> None:
        stamp = require_turn_stamp(metadata, context=f"{self.node_id}/{input_name}")
        if self.expected is not None:
            if input_name not in self.expected:
                raise ProtocolError(f"undeclared input {self.node_id}/{input_name} in open turn")
            if stamp != self.expected_stamps[input_name]:
                raise ProtocolError(
                    f"{self.node_id}/{input_name} stamp {stamp} does not match expected "
                    f"{self.expected_stamps[input_name]}"
                )
            if len(self._buffer.get(input_name, [])) >= self.expected[input_name]:
                raise ProtocolError(f"too many {self.node_id}/{input_name} messages in one turn")
        seq = metadata.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise ProtocolError(f"{self.node_id}/{input_name} has malformed seq {seq!r}")
        self._arrival += 1
        self._buffer.setdefault(input_name, []).append((seq, self._arrival, stamp, event))

    def open(self, metadata: dict) -> None:
        if self.stamp is not None and not self._closed:
            raise ProtocolError(f"{self.node_id} received a second ready signal")
        stamp = require_turn_stamp(metadata, context=f"{self.node_id}/turn")
        names = metadata.get("expected_inputs")
        counts = metadata.get("expected_counts")
        if not isinstance(names, list) or not isinstance(counts, list) or len(names) != len(counts):
            raise ProtocolError("ready inputs/counts must be equal-length lists")
        if (
            any(not isinstance(name, str) or not name for name in names)
            or names != sorted(names)
            or len(set(names)) != len(names)
        ):
            raise ProtocolError("ready inputs must be unique non-empty names in lexical order")
        if any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0 for count in counts
        ):
            raise ProtocolError("ready counts must be non-negative integers")
        epochs = metadata.get("expected_turn_epochs")
        turn_ids = metadata.get("expected_turn_ids")
        sim_times = metadata.get("expected_sim_time_ns")
        if epochs is None and turn_ids is None and sim_times is None:
            expected_stamps = [stamp] * len(names)
        elif not all(
            isinstance(values, list) and len(values) == len(names)
            for values in (epochs, turn_ids, sim_times)
        ):
            raise ProtocolError("ready input stamp arrays must match expected_inputs")
        else:
            expected_stamps = []
            for epoch, turn_id, sim_time_ns in zip(epochs, turn_ids, sim_times, strict=True):
                expected = parse_turn_stamp(
                    {
                        "turn_epoch": epoch,
                        "turn_id": turn_id,
                        "sim_time_ns": sim_time_ns,
                    }
                )
                if expected is None:
                    raise ProtocolError("ready contains a malformed expected input stamp")
                expected_stamps.append(expected)
        self.stamp = stamp
        self.expected = dict(zip(names, counts, strict=True))
        self.expected_stamps = dict(zip(names, expected_stamps, strict=True))
        unknown = set(self._buffer) - set(self.expected)
        excess = {
            name: len(events)
            for name, events in self._buffer.items()
            if name in self.expected and len(events) > self.expected[name]
        }
        wrong_stamps = {
            name: sorted({event_stamp for _, _, event_stamp, _ in events})
            for name, events in self._buffer.items()
            if name in self.expected
            and any(event_stamp != self.expected_stamps[name] for _, _, event_stamp, _ in events)
        }
        if unknown or excess or wrong_stamps:
            raise ProtocolError(
                f"{self.node_id} buffered undeclared/excess inputs: unknown={sorted(unknown)}, "
                f"excess={excess}, wrong_stamps={wrong_stamps}"
            )

    def take(self) -> list[Any]:
        if not self.ready:
            raise ProtocolError(f"{self.node_id} cannot run before exact input closure")
        if self._taken:
            raise ProtocolError(f"{self.node_id} input batch already consumed")
        self._taken = True
        ordered: list[Any] = []
        for name in sorted(self.expected or {}):
            ordered.extend(event for _, _, _, event in sorted(self._buffer.get(name, [])))
        return ordered

    def record_output(self, output: str) -> None:
        if self.stamp is None or self._closed:
            raise ProtocolError(f"{self.node_id}/{output} emitted outside an open turn")
        if output == "turn_done" or output not in self._output_counts:
            raise ProtocolError(f"unknown or reserved output {self.node_id}/{output}")
        self._output_counts[output] += 1

    def close(self) -> dict:
        if self.stamp is None or not self._taken or self._closed:
            raise ProtocolError(f"{self.node_id} cannot close before consuming its turn")
        self._closed = True
        self._output_counts["turn_done"] = 1
        return {
            **watermark_metadata(self.stamp, self._output_counts),
            "source_node": self.node_id,
        }


class BridgeTurn:
    """Buffer and deterministically close one bridge control turn."""

    ORDER = ("joint_cmd", "gripper_cmd", "base_cmd")

    def __init__(self, stamp: TurnStamp):
        self.stamp = stamp
        self._commands: dict[str, tuple[int, Any, int]] = {}
        self._reset: tuple[int, Any] | None = None
        self._committed = False
        self.advances_physics: bool | None = None

    @property
    def commands(self) -> list[tuple[str, Any]]:
        return [(kind, self._commands[kind][1]) for kind in self.ORDER if kind in self._commands]

    @staticmethod
    def _record_dropped(payload: Any, dropped: int) -> None:
        if isinstance(payload, dict):
            metadata = payload.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata["dropped"] = dropped

    def _check_stamp(self, metadata: dict, *, context: str) -> None:
        stamp = require_turn_stamp(metadata, context=context)
        if stamp != self.stamp:
            raise ProtocolError(
                f"{context} belongs to {stamp.epoch}:{stamp.turn_id}@{stamp.sim_time_ns}; "
                f"open turn is {self.stamp.epoch}:{self.stamp.turn_id}@{self.stamp.sim_time_ns}"
            )

    def accept(self, kind: str, payload: Any, metadata: dict) -> None:
        if self._committed:
            raise ProtocolError("command arrived after turn commit")
        self._check_stamp(metadata, context=kind)
        seq = _plain_int(metadata.get("seq"), minimum=1)
        if seq is None:
            raise ProtocolError(f"{kind} has no positive integer seq")
        if kind == "reset":
            if self._reset is not None:
                raise ProtocolError("duplicate reset in one turn")
            self._reset = (seq, payload)
            return
        if kind not in self.ORDER:
            raise ProtocolError(f"unknown bridge command {kind!r}")
        prior = self._commands.get(kind)
        if prior is None or seq > prior[0]:
            dropped = 0 if prior is None else prior[2] + 1
            self._record_dropped(payload, dropped)
            self._commands[kind] = (seq, payload, dropped)
        elif seq == prior[0]:
            raise ProtocolError(f"duplicate {kind} seq {seq} in one turn")
        else:
            dropped = prior[2] + 1
            self._record_dropped(prior[1], dropped)
            self._commands[kind] = (prior[0], prior[1], dropped)

    def commit(self, metadata: dict) -> list[tuple[str, Any]]:
        self._check_stamp(metadata, context="turn_commit")
        if self._committed:
            raise ProtocolError("duplicate turn_commit")
        self._committed = True
        if self._reset is not None:
            self.advances_physics = False
            return [("reset", self._reset[1])]
        self.advances_physics = True
        return self.commands


class TurnWatchdog:
    """Wall-clock liveness backstop with per-turn-type budgets.

    This class reports expiry only.  The coordinator responds by aborting;
    it has no operation that can commit a turn or advance simulation.
    """

    def __init__(self, ordinary_s: float, verdict_s: float, clock):
        if ordinary_s <= 0 or verdict_s < ordinary_s:
            raise ValueError("watchdog budgets must be positive and verdict >= ordinary")
        self.ordinary_s = float(ordinary_s)
        self.verdict_s = float(verdict_s)
        self.clock = clock
        self.started: float | None = None
        self.turn_type = "ordinary"

    def open(self) -> None:
        self.started = float(self.clock())
        self.turn_type = "ordinary"

    def mark_verdict_bearing(self) -> None:
        if self.started is None:
            raise ProtocolError("cannot classify a watchdog before a turn opens")
        self.turn_type = "verdict"

    @property
    def expired(self) -> bool:
        if self.started is None:
            return False
        budget = self.verdict_s if self.turn_type == "verdict" else self.ordinary_s
        return float(self.clock()) - self.started > budget
