"""Unit tests for harness swap/probe (SPEC 070 HAR-10, HAR-11, HAR-12;
design doc §9.1). The dora seam is injected — no live dataflow."""

import json
from pathlib import Path

import pytest
import yaml

from aisle.harness.swap import probe, swap, swap_event, swapped_graph_doc

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
GRAPH = REPO_ROOT / "graphs" / "expert_t0.yaml"


def ok_runner(calls):
    def run(cmd):
        calls.append(cmd)

        class R:
            returncode = 0
            stderr = ""

        return R()

    return run


def test_swap_validates_post_swap_graph_before_mutation(tmp_path):
    """HAR-10: an invalid post-swap graph (id changed -> dangling edges /
    manifest mismatch) is REFUSED with the validator report and the
    runtime is never touched."""
    calls = []
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"id": "not-oracle-pose", "outputs": ["target_pose"]}))
    out = swap(REPO_ROOT, GRAPH, "df", "oracle-pose", bad, "franka", "b", runner=ok_runner(calls))
    assert out["ok"] is False and calls == []

    ungated = tmp_path / "ungated.yaml"
    node = {
        "id": "oracle-pose",
        "path": "x.py",
        "inputs": {"poses": {"source": "dora-genesis/poses", "queue_size": 100}},
        "outputs": ["target_pose", "nonexistent_port"],
    }
    ungated.write_text(yaml.safe_dump(node))
    out = swap(
        REPO_ROOT, GRAPH, "df", "oracle-pose", ungated, "franka", "b", runner=ok_runner(calls)
    )
    assert out["ok"] is False and "refused" in out and calls == []


def test_swap_happy_path_drives_dora_and_logs_event(tmp_path, monkeypatch):
    """HAR-10 + HAR-12: a valid identity-preserving swap validates, then
    remove+add on the live dataflow, and appends the latency event."""
    import aisle.harness.swap as s

    monkeypatch.setattr(s, "swap_event", lambda root, branch, e: {"ts": 123.0, **e})
    calls = []
    doc = yaml.safe_load(GRAPH.read_text())
    original = next(n for n in doc["nodes"] if n["id"] == "oracle-pose")
    same = tmp_path / "same.yaml"
    same.write_text(yaml.safe_dump(original))
    out = swap(REPO_ROOT, GRAPH, "df", "oracle-pose", same, "franka", "b", runner=ok_runner(calls))
    assert out == {"ok": True, "swapped": "oracle-pose", "dataflow": "df", "ts": 123.0}
    assert calls[0][:2] == ["node", "remove"] and calls[1][:2] == ["node", "add"]
    assert not list(GRAPH.parent.glob(".swap-*.yaml"))  # staged file cleaned


def test_swapped_graph_doc_refuses_unknown_node():
    with pytest.raises(SystemExit):
        swapped_graph_doc(GRAPH, "no-such-node", {"id": "no-such-node"})


def test_probe_refuses_oracle_state(tmp_path):
    """HAR-11: no probe exemption from VAL-6 — ground truth stays
    unroutable even for temporary inspectors."""
    out = probe(tmp_path, "df", "dora-genesis/oracle_state", 0.0, "b", runner=ok_runner([]))
    assert out["ok"] is False and "VAL-6" in out["error"]


def test_probe_attach_detach_and_event(tmp_path):
    """HAR-11 + HAR-12: attach, wait the window, detach; the probe node
    has NO outputs (cannot publish) and the event is logged."""
    calls = []
    (tmp_path / "runs" / "ideas").mkdir(parents=True)
    out = probe(tmp_path, "df", "dora-genesis/poses", 0.0, "b", runner=ok_runner(calls))
    assert out["ok"] is True
    assert calls[0][:2] == ["node", "add"] and calls[1][:2] == ["node", "remove"]
    staged = yaml.safe_load(Path(calls[0][calls[0].index("--from-yaml") + 1]).read_text())
    assert "outputs" not in staged
    log = tmp_path / "runs" / "swaps" / "b.jsonl"
    entry = json.loads(log.read_text().splitlines()[-1])
    assert entry["action"] == "probe" and entry["topic"] == "dora-genesis/poses"


def test_swap_event_records_open_idea(tmp_path):
    """HAR-12: the event carries the currently open idea id so H4 latency
    is measurable idea-open -> first episode under the change."""
    ideas = tmp_path / "runs" / "ideas"
    ideas.mkdir(parents=True)
    (ideas / "b.jsonl").write_text(json.dumps({"id": "I7", "ts": 1, "status": "open"}) + "\n")
    entry = swap_event(tmp_path, "b", {"action": "swap", "node": "x"})
    assert entry["open_idea"] == "I7"
    assert (tmp_path / "runs" / "swaps" / "b.jsonl").exists()
