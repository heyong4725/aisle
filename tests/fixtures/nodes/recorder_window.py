"""Pure capture-window state machine shared by the recorder fixtures
(issue #160, from the PR #159 review): the RECORDER_AWAIT semantics were
inlined in recorder.py and therefore covered only by full graph runs;
base_recorder.py had no await at all, leaving the ADR-29 wall-net tests
on the same wall-only-window truncation class issue #94 fixed. One pure
class, imported by both fixtures (dora launches node scripts with the
script's directory first on sys.path) and unit-tested directly.

Semantics (issue #94, PR #159 review):
- the window OPENS at the first observed event, so a genesis/taichi build
  never eats the capture;
- with an await of "topic:count", the window cannot close before the Nth
  row of that topic, however late load makes it;
- the Nth row re-anchors the deadline to now + tail, guaranteeing the
  post-event tail;
- an optional sim horizon additionally holds the window until recorded
  sim stamps advance await_sim_ns past the Nth row's stamp (a wall tail
  under-covers SIM time when rtf collapses; CON-5 layer (c) windows are
  sim-denominated). An unstamped Nth row cannot anchor a sim horizon;
  the wall tail then governs alone;
- if the awaited row never arrives the window never closes, no sentinel
  is written, and the settle helper fails loudly at its outer deadline —
  a protocol defect stays a failure, never a truncated pass.
"""

from __future__ import annotations


def parse_await_spec(spec: str) -> tuple[str, int]:
    """'topic:count' -> (topic, count); '' -> ('', 0). Malformed specs
    raise LOUDLY: a silent recorder death would burn the settle helper's
    whole outer deadline with an opaque empty-capture error."""
    if not spec:
        return "", 0
    topic, _, raw = spec.partition(":")
    if not topic or not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"RECORDER_AWAIT must be 'topic:count', got {spec!r}")
    return topic, int(raw)


class CaptureWindow:
    """The recorder fixtures' window: `observe(now)` before handling each
    event (True = the window is closed — write the sentinel and stop);
    `on_recorded(topic, sim_time_ns, now)` after writing a row.

    duration_s None = unbounded: record until the dataflow is torn down
    (base_recorder's historical default); the window never closes."""

    def __init__(
        self,
        duration_s: float | None,
        await_topic: str = "",
        await_count: int = 0,
        await_tail_s: float | None = None,
        await_sim_ns: int = 0,
    ) -> None:
        if await_count and duration_s is None:
            # an await only ever DELAYS a deadline; with no duration there is
            # no deadline, so the await would be silently ignored. Refuse
            # rather than pretend (tests/conftest.py guards the analogous
            # tail-without-await no-op the same way; PR #177 review).
            raise ValueError("RECORDER_AWAIT requires RECORDER_DURATION_S; it would be ignored")
        self.duration_s = duration_s
        self.await_topic = await_topic
        self.await_count = await_count
        # default tail = the window duration, matching the pre-extraction
        # recorder.py behavior (issue #94)
        self.await_tail_s = (
            await_tail_s if await_tail_s is not None else (duration_s if duration_s else 0.0)
        )
        self.await_sim_ns = await_sim_ns
        self.deadline: float | None = None
        self.awaited_seen = 0
        self.sim_target: int | None = None
        self.max_sim_ns = 0

    def observe(self, now: float) -> bool:
        """First contact opens the window; afterwards, closed iff the wall
        deadline passed AND the awaited rows landed AND any sim horizon
        was reached."""
        if self.duration_s is None:
            return False
        if self.deadline is None:
            self.deadline = now + self.duration_s
            return False
        return (
            now > self.deadline
            and self.awaited_seen >= self.await_count
            and (self.sim_target is None or self.max_sim_ns >= self.sim_target)
        )

    def on_recorded(self, topic: str, sim_time_ns: object, now: float) -> None:
        if isinstance(sim_time_ns, int) and sim_time_ns > self.max_sim_ns:
            self.max_sim_ns = sim_time_ns
        if self.await_topic and topic == self.await_topic:
            self.awaited_seen += 1
            if self.awaited_seen == self.await_count:
                # the awaited protocol completed, however late load made it:
                # guarantee the post-event tail from HERE
                if self.deadline is not None:
                    self.deadline = max(self.deadline, now + self.await_tail_s)
                if self.await_sim_ns and isinstance(sim_time_ns, int) and sim_time_ns > 0:
                    self.sim_target = sim_time_ns + self.await_sim_ns
