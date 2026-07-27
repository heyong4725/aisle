"""Unit tests for harness swap/probe (SPEC 070 HAR-10, HAR-11, HAR-12;
design doc §9.1; hardened per the PR #50 adversarial review). The dora
seam is injected — no live dataflow."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from aisle.harness.swap import probe, swap, swap_event, swapped_graph_doc

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_GRAPH = REPO_ROOT / "graphs" / "expert_t0.yaml"


def make_runner(calls, fail_on=None):
    def run(cmd):
        calls.append(cmd)

        class R:
            returncode = 1 if fail_on and cmd[:2] == list(fail_on) else 0
            stderr = "boom" if fail_on and cmd[:2] == list(fail_on) else ""

        return R()

    return run


@pytest.fixture
def graph(tmp_path):
    """A tmp COPY of the expert graph: swaps write the file back on
    success, and the checked-in (env-hashed) original must never mutate."""
    copy = tmp_path / "graph.yaml"
    shutil.copy(REAL_GRAPH, copy)
    return copy


def original_node(graph, node_id):
    return next(n for n in yaml.safe_load(graph.read_text())["nodes"] if n["id"] == node_id)


def identity_swap_file(tmp_path, graph, node_id="oracle-pose"):
    f = tmp_path / "same.yaml"
    f.write_text(yaml.safe_dump(original_node(graph, node_id)))
    return f


def test_swap_validates_post_swap_graph_before_mutation(tmp_path, graph):
    """HAR-10: an invalid post-swap graph (wrong id -> refusal; phantom
    port -> validator refusal) never touches the runtime."""
    calls = []
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"id": "not-oracle-pose", "outputs": ["target_pose"]}))
    out = swap(REPO_ROOT, graph, "df", "oracle-pose", bad, "franka", "b", runner=make_runner(calls))
    assert out["ok"] is False and calls == []

    ungated = tmp_path / "ungated.yaml"
    node = dict(original_node(graph, "oracle-pose"))
    node["outputs"] = ["target_pose", "nonexistent_port"]
    ungated.write_text(yaml.safe_dump(node))
    out = swap(
        REPO_ROOT, graph, "df", "oracle-pose", ungated, "franka", "b", runner=make_runner(calls)
    )
    assert out["ok"] is False and "refused" in out and calls == []


def test_swap_refuses_trust_anchors(tmp_path, graph):
    """PR #50 adversarial review (most exploitable): a same-id guard
    replacement passes every topology check while neutering the clamp —
    the guard and frozen-set nodes are refused outright."""
    calls = []
    fake_guard = tmp_path / "guard.yaml"
    fake_guard.write_text(yaml.safe_dump({**original_node(graph, "budget-guard")}))
    out = swap(
        REPO_ROOT, graph, "df", "budget-guard", fake_guard, "franka", "b", runner=make_runner(calls)
    )
    assert out["ok"] is False and "trust anchor" in out["error"] and calls == []

    fake_reset = tmp_path / "reset.yaml"
    fake_reset.write_text(yaml.safe_dump({**original_node(graph, "reset")}))
    out = swap(
        REPO_ROOT, graph, "df", "reset", fake_reset, "franka", "b", runner=make_runner(calls)
    )
    assert out["ok"] is False and "trust anchor" in out["error"] and calls == []


def test_swap_happy_path_stages_single_node_and_writes_back(tmp_path, graph, monkeypatch):
    """HAR-10 + HAR-12 + PR #50: the runtime receives the SINGLE node doc
    (not the whole graph), staged in a tmpdir OUTSIDE graphs/; a
    successful swap persists the post-swap doc to the graph file so the
    next validation sees live reality; the event is logged."""
    import aisle.harness.swap as s

    events = []
    monkeypatch.setattr(
        s, "swap_event", lambda root, branch, e: events.append(e) or {"ts": 123.0, **e}
    )
    calls = []
    before = graph.read_text()
    out = swap(
        REPO_ROOT,
        graph,
        "df",
        "oracle-pose",
        identity_swap_file(tmp_path, graph),
        "franka",
        "b",
        runner=make_runner(calls),
    )
    assert out["ok"] is True and out["ts"] == 123.0
    assert calls[0][:2] == ["node", "remove"] and calls[1][:2] == ["node", "add"]
    staged_path = Path(calls[1][calls[1].index("--from-yaml") + 1])
    assert "graphs" not in staged_path.parts  # unpredictable tmpdir, not graphs/
    assert not staged_path.exists()  # staging cleaned up
    assert graph.read_text() != before  # write-back happened (reserialized)
    assert original_node(graph, "oracle-pose")["id"] == "oracle-pose"
    assert events[-1]["action"] == "swap"


def test_swap_add_failure_restores_original(tmp_path, graph, monkeypatch):
    """PR #50: remove-then-add with a failed add restores the ORIGINAL
    node (never a guard-less runtime), reports degraded honestly, logs a
    swap_failed event, and does NOT write the graph file back."""
    import aisle.harness.swap as s

    events = []
    monkeypatch.setattr(
        s, "swap_event", lambda root, branch, e: events.append(e) or {"ts": 1.0, **e}
    )
    calls = []
    before = graph.read_text()
    out = swap(
        REPO_ROOT,
        graph,
        "df",
        "oracle-pose",
        identity_swap_file(tmp_path, graph),
        "franka",
        "b",
        runner=make_runner(calls, fail_on=("node", "add")),
    )
    assert out["ok"] is False and "add failed" in out["error"]
    assert out["degraded"] is True  # restore also rode the failing add
    assert len([c for c in calls if c[:2] == ["node", "add"]]) == 2  # add + restore
    assert graph.read_text() == before  # no write-back on failure
    assert events[-1]["action"] == "swap_failed"


def test_swapped_graph_doc_errors_are_values_not_exits(graph):
    """PR #50 (CON-8): refusals are returned values for JSON-on-stdout,
    never SystemExit-to-stderr."""
    out = swapped_graph_doc(graph, "no-such-node", {"id": "no-such-node"})
    assert isinstance(out, str) and "not in" in out


def test_probe_refuses_oracle_state_and_negative_window(tmp_path):
    """HAR-11: no probe exemption from VAL-6; a negative window is a
    refusal, not a post-attach ValueError leak."""
    out = probe(tmp_path, "df", "dora-genesis/oracle_state", 0.0, "b", runner=make_runner([]))
    assert out["ok"] is False and "VAL-6" in out["error"]
    out = probe(tmp_path, "df", "dora-genesis/poses", -1.0, "b", runner=make_runner([]))
    assert out["ok"] is False and ">= 0" in out["error"]


def test_probe_attach_detach_event_and_unique_ids(tmp_path):
    """HAR-11 + HAR-12 + PR #50: attach/detach with a no-outputs node,
    event logged, and uuid ids that cannot collide within a second."""
    calls = []
    (tmp_path / "runs" / "ideas").mkdir(parents=True)
    out1 = probe(tmp_path, "df", "dora-genesis/poses", 0.0, "b", runner=make_runner(calls))
    out2 = probe(tmp_path, "df", "dora-genesis/poses", 0.0, "b", runner=make_runner(calls))
    assert out1["ok"] and out2["ok"] and out1["probe"] != out2["probe"]
    staged = yaml.safe_load(Path(calls[0][calls[0].index("--from-yaml") + 1]).read_text())
    assert "outputs" not in staged
    entry = json.loads((tmp_path / "runs" / "swaps" / "b.jsonl").read_text().splitlines()[-1])
    assert entry["action"] == "probe"


def test_probe_detach_runs_even_when_window_raises(tmp_path, monkeypatch):
    """PR #50: Ctrl-C (or any exception) during the window must still
    detach — the finally guarantees no silently leaked probe."""
    import aisle.harness.swap as s

    calls = []

    def boom(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(s.time, "sleep", boom)
    with pytest.raises(KeyboardInterrupt):
        probe(tmp_path, "df", "dora-genesis/poses", 5.0, "b", runner=make_runner(calls))
    assert ["node", "remove"] in [c[:2] for c in calls]  # detached anyway


def test_probe_detach_failure_names_the_leaked_probe(tmp_path):
    """PR #50: a failed detach reports WHICH probe leaked and the manual
    removal command, never a bare ok:false."""
    out = probe(
        tmp_path,
        "df",
        "dora-genesis/poses",
        0.0,
        "b",
        runner=make_runner([], fail_on=("node", "remove")),
    )
    assert out["ok"] is False
    assert out["probe"] in out["error"] and "remove" in out["error"]


def test_swap_event_records_open_idea(tmp_path):
    """HAR-12: the event carries the open idea id so H4 latency is
    measurable idea-open -> first episode under the change."""
    ideas = tmp_path / "runs" / "ideas"
    ideas.mkdir(parents=True)
    (ideas / "b.jsonl").write_text(json.dumps({"id": "I7", "ts": 1, "status": "open"}) + "\n")
    entry = swap_event(tmp_path, "b", {"action": "swap", "node": "x"})
    assert entry["open_idea"] == "I7"
