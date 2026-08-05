"""Unit tests for harness swap/probe (SPEC 070 HAR-10, HAR-11, HAR-12;
design doc §9.1; hardened per the PR #50 adversarial review). The dora
seam is injected — no live dataflow."""

import json
from pathlib import Path

import pytest
import yaml

from aisle.harness.swap import probe, swap, swap_event, swapped_graph_doc

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_GRAPH = REPO_ROOT / "graphs" / "expert_t0.yaml"


def make_runner(calls, fail_on=None, stdout_for=None):
    def run(cmd):
        calls.append(cmd)

        class R:
            returncode = 1 if fail_on and cmd[:2] == list(fail_on) else 0
            stderr = "boom" if fail_on and cmd[:2] == list(fail_on) else ""
            stdout = stdout_for(cmd) if stdout_for else ""

        return R()

    return run


def no_sleep(calls):
    """A sleeper that records into the same call list so ORDER against
    the dora commands is assertable."""

    def sleeper(seconds):
        calls.append(("sleep", seconds))

    return sleeper


def running_list(node_id):
    def stdout_for(cmd):
        if cmd[:2] == ["node", "list"]:
            # the real CLI emits JSON LINES, one object per node
            return json.dumps({"node": node_id, "status": "Running"}) + "\n"
        return ""

    return stdout_for


@pytest.fixture
def graph(tmp_path):
    """A tmp COPY of the expert graph: swaps write the file back on
    success, and the checked-in (env-hashed) original must never mutate.
    Node paths are absolutized like the RUN graphs swap actually targets
    (instrumented copies, HAR-4) — post-#62 there is one authoritative
    path base, and a relocated copy with relative paths would rightly
    fail PATH_MANIFEST_MISMATCH."""
    copy = tmp_path / "graph.yaml"
    doc = yaml.safe_load(REAL_GRAPH.read_text())
    for node in doc["nodes"]:
        path = node.get("path")
        if isinstance(path, str) and path and not path.startswith("pip:"):
            node["path"] = str((REAL_GRAPH.parent / path).resolve())
    copy.write_text(yaml.safe_dump(doc, sort_keys=False))
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
    out = swapped_graph_doc(graph, "no-such-node", {"id": "no-such-node"}, REPO_ROOT)
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


def test_anchor_refusal_survives_crafted_graph_paths(tmp_path):
    """PR #50 re-review: the anchor check is keyed on the node ID's
    MANIFEST source (root authority), so a crafted --graph giving `reset`
    a benign path, an obfuscated path, or NO path cannot dodge it."""
    crafted = tmp_path / "crafted.yaml"
    for node in (
        {"id": "reset", "path": "totally/benign.py", "outputs": ["bridge_reset"]},
        {"id": "reset", "path": "../src/aisle//reset/service.py", "outputs": ["bridge_reset"]},
        {"id": "reset", "outputs": ["bridge_reset"]},
    ):
        crafted.write_text(yaml.safe_dump({"nodes": [node]}))
        out = swapped_graph_doc(crafted, "reset", {"id": "reset"}, REPO_ROOT)
        assert isinstance(out, str) and "trust anchor" in out, node


def test_every_refusal_logs_a_har12_event(tmp_path, graph):
    """PR #50 re-review: HAR-12 says every ATTEMPT logs — including
    refusals and failed mutations; the guard-swap attempt is the most
    security-relevant event of all."""
    log = tmp_path / "runs" / "swaps" / "b.jsonl"

    def events():
        return [json.loads(line)["action"] for line in log.read_text().splitlines()]

    bad_id = tmp_path / "bad.yaml"
    bad_id.write_text(yaml.safe_dump({"id": "other"}))
    swap(tmp_path, graph, "df", "oracle-pose", bad_id, "franka", "b", runner=make_runner([]))
    assert events()[-1] == "swap_refused"

    guard = tmp_path / "guard.yaml"
    guard.write_text(yaml.safe_dump({"id": "budget-guard"}))
    swap(tmp_path, graph, "df", "budget-guard", guard, "franka", "b", runner=make_runner([]))
    assert events()[-1] == "swap_refused"

    probe(tmp_path, "df", "x/oracle_state", 0.0, "b", runner=make_runner([]))
    assert events()[-1] == "probe_refused"
    probe(
        tmp_path,
        "df",
        "dora-genesis/poses",
        0.0,
        "b",
        runner=make_runner([], fail_on=("node", "add")),
    )
    assert events()[-1] == "probe_failed"


def test_swap_stages_absolute_paths_one_authoritative_base(tmp_path, graph, monkeypatch):
    """PR #62 review P1: staging must preserve ONE authoritative runtime
    base — every path-form node path in the staged post-swap graph AND
    the staged node doc is absolutized from the ORIGINAL graph's
    directory before validation and `dora node add`, so the validator
    and the runtime can never resolve different code."""
    import aisle.harness.swap as s

    monkeypatch.setattr(s, "swap_event", lambda root, branch, e: {"ts": 1.0, **e})
    calls = []
    staged_docs = []
    base_runner = make_runner(calls)

    def capturing_runner(cmd):
        # snapshot the staged node at `dora node add` time — the tmpdir is
        # (correctly) cleaned before swap() returns
        if cmd[:2] == ["node", "add"]:
            staged_docs.append(yaml.safe_load(Path(cmd[cmd.index("--from-yaml") + 1]).read_text()))
        return base_runner(cmd)

    out = swap(
        REPO_ROOT,
        graph,
        "df",
        "oracle-pose",
        identity_swap_file(tmp_path, graph),
        "franka",
        "b",
        runner=capturing_runner,
    )
    assert out["ok"] is True
    staged_node = staged_docs[0]
    assert Path(staged_node["path"]).is_absolute()
    assert (
        Path(staged_node["path"]).resolve()
        == (REPO_ROOT / "src" / "aisle" / "nodes" / "oracle_pose.py").resolve()
    )
    # the graph write-back keeps the runtime-truth (absolute) form too
    doc = yaml.safe_load(graph.read_text())
    for node in doc["nodes"]:
        if isinstance(node.get("path"), str) and not node["path"].startswith("pip:"):
            assert Path(node["path"]).is_absolute(), node["id"]


def test_swap_default_is_settle_free(tmp_path, graph, monkeypatch):
    """HAR-10: the default swap does NOT sleep between remove and add —
    the dora-rs/dora#2916 race the 2 s settle worked around (H4 shakeout,
    rev 7eb4a5f) is fixed at our pin (eec31a40b in cd597e705, live
    retest in PR #86). An explicit settle_s is still honored as the
    escape hatch for older daemons."""
    monkeypatch.chdir(tmp_path)
    calls = []
    out = swap(
        REPO_ROOT,
        graph,
        "df",
        "oracle-pose",
        identity_swap_file(tmp_path, graph),
        "franka",
        "b",
        runner=make_runner(calls, stdout_for=running_list("oracle-pose")),
        sleeper=no_sleep(calls),
    )
    assert out["ok"] is True and out["replacement_health"] == "running"
    assert not any(c[0] == "sleep" for c in calls if isinstance(c, tuple))

    calls_legacy = []
    out = swap(
        REPO_ROOT,
        graph,
        "df",
        "oracle-pose",
        identity_swap_file(tmp_path, graph),
        "franka",
        "b",
        runner=make_runner(calls_legacy, stdout_for=running_list("oracle-pose")),
        settle_s=2.0,
        sleeper=no_sleep(calls_legacy),
    )
    assert out["ok"] is True
    remove_i = calls_legacy.index(["node", "remove", "-d", "df", "oracle-pose"])
    add_i = next(i for i, c in enumerate(calls_legacy) if c[:2] == ["node", "add"])
    assert ("sleep", 2.0) in calls_legacy[remove_i + 1 : add_i]  # explicit settle sits between


def test_swap_unhealthy_replacement_rolls_back(tmp_path, graph, monkeypatch):
    """The post-add health belt: a replacement reported Failed by
    `dora node list` refuses the swap and restores the ORIGINAL node
    (never a dead planner on a live stream), logging a HAR-12
    swap_failed event."""
    monkeypatch.chdir(tmp_path)
    calls = []

    def failed_list(cmd):
        if cmd[:2] == ["node", "list"]:
            return json.dumps({"node": "oracle-pose", "status": "Failed"}) + "\n"
        return ""

    out = swap(
        REPO_ROOT,
        graph,
        "df",
        "oracle-pose",
        identity_swap_file(tmp_path, graph),
        "franka",
        "b",
        runner=make_runner(calls, stdout_for=failed_list),
        sleeper=no_sleep(calls),
    )
    assert out["ok"] is False and "unhealthy" in out["error"]
    assert out["restored"] is True
    adds = [c for c in calls if c[:2] == ["node", "add"]]
    assert len(adds) == 2  # the replacement, then the original restored
    events = (REPO_ROOT / "runs" / "swaps" / "b.jsonl").read_text().splitlines()
    assert json.loads(events[-1])["action"] == "swap_failed"


def test_swap_unknown_health_format_does_not_refuse(tmp_path, graph, monkeypatch):
    """CLI format drift must not brick swaps: unparseable `node list`
    output reports health 'unknown' and the swap stands (the race is
    prevented by the settle, not the belt)."""
    monkeypatch.chdir(tmp_path)
    calls = []
    out = swap(
        REPO_ROOT,
        graph,
        "df",
        "oracle-pose",
        identity_swap_file(tmp_path, graph),
        "franka",
        "b",
        runner=make_runner(calls, stdout_for=lambda cmd: "not json"),
        sleeper=no_sleep(calls),
    )
    assert out["ok"] is True and out["replacement_health"] == "unknown"


def test_probe_spawns_via_current_interpreter(tmp_path):
    """H4 shakeout: dynamically added nodes spawn WITHOUT the dataflow's
    --uv wrapping, so a bare recorder path died at import under the
    daemon's python (ExitCode(1) before register). The probe node MUST
    spawn via the harness's own interpreter."""
    import sys

    calls = []
    out = probe(tmp_path, "df", "dora-genesis/poses", 0.0, "b", runner=make_runner(calls))
    assert out["ok"] is True
    staged = yaml.safe_load((tmp_path / "runs" / "probes" / f"{out['probe']}.yaml").read_text())
    assert staged["path"] == sys.executable
    assert staged["args"].endswith("trace_recorder.py")


def test_probe_env_has_no_pythonpath_pin(tmp_path):
    """The PYTHONPATH pin existed because the daemon resolved the
    interpreter symlink before exec, losing pyvenv.cfg discovery (H4
    shakeout: ModuleNotFoundError: numpy under the venv python). Fixed
    upstream (dora-rs/dora#2942, in our pin cd597e705; live probe retest
    in PR #86) — the probe env must stay minimal so the recorder runs
    under the venv's own discovery, not a hand-pinned path that would
    mask a future venv drift."""
    calls = []
    out = probe(tmp_path, "df", "dora-genesis/poses", 0.0, "b", runner=make_runner(calls))
    staged = yaml.safe_load((tmp_path / "runs" / "probes" / f"{out['probe']}.yaml").read_text())
    assert "PYTHONPATH" not in staged["env"]
    assert set(staged["env"]) == {"AISLE_TRACE_DIR"}
