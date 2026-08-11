"""The recorder fixture's RECORDER_AWAIT fail-loud contract (issue #94,
PR #159 review): an await that is never met must surface as a LOUD settle
failure, never as a truncated-but-accepted capture — and a crashed node
must fail fast with its stderr, not burn the whole outer deadline. Pure
python nodes, no genesis. Marker `graph`: launches dora dataflows."""

import shutil
from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.graph,
    pytest.mark.skipif(shutil.which("dora") is None, reason="dora CLI not installed"),
]

REPO = Path(__file__).resolve().parents[2]
RECORDER = REPO / "tests" / "fixtures" / "nodes" / "recorder.py"


def _write_graph(tmp: Path, rec_out: Path, env: dict) -> Path:
    graph = {
        "nodes": [
            {
                "id": "recorder",
                "path": str(RECORDER),
                "inputs": {"tick": "dora/timer/millis/100"},
                "env": {"RECORDER_OUT": str(rec_out), **env},
            }
        ]
    }
    path = tmp / "await.yaml"
    path.write_text(yaml.safe_dump(graph))
    return path


def test_unmet_await_fails_loudly_not_as_a_truncated_pass(tmp_path, dataflow):
    """Events flow (100 ms ticks) and the 2 s window expires, but the
    awaited topic never arrives: the recorder must keep the window open and
    write NO sentinel, so the settle helper raises at ITS deadline — a
    missing protocol event can never be read as a completed capture."""
    rec_out = tmp_path / "records.jsonl"
    graph = _write_graph(
        tmp_path, rec_out, {"RECORDER_DURATION_S": "2", "RECORDER_AWAIT": "reset_done:1"}
    )
    with pytest.raises(AssertionError, match="sentinel"):
        dataflow.run_until_settled(graph, rec_out, deadline_s=12)
    # the window stayed open past its wall duration: ticks kept recording
    rows = dataflow.read(rec_out)
    assert len(rows) > 20, f"recorder stopped recording while the await was unmet: {len(rows)}"


def test_crashed_node_fails_fast_with_stderr(tmp_path, dataflow):
    """A malformed await spec kills the recorder at startup; the settle
    helper must notice the dead dataflow within a poll cycle and surface
    the stderr, instead of sleeping its whole deadline (PR #159 review).
    (write_bridge_dataflow rejects such specs at build time; this covers
    hand-written graphs reaching the recorder directly.)"""
    import time

    rec_out = tmp_path / "records.jsonl"
    graph = _write_graph(
        tmp_path, rec_out, {"RECORDER_DURATION_S": "2", "RECORDER_AWAIT": "reset_done"}
    )
    start = time.monotonic()
    with pytest.raises(AssertionError, match="exited|sentinel"):
        dataflow.run_until_settled(graph, rec_out, deadline_s=60)
    # fail-fast: well under the 60 s deadline (one dora startup + poll cycle)
    assert time.monotonic() - start < 45, "a dead dataflow burned the settle deadline"
