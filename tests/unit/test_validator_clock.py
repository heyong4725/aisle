"""ADR-30 validator topology acceptance (issue #175)."""

from __future__ import annotations

import copy

import pytest

from aisle.harness.validate import _clock_errors, compile_turn_plan

pytestmark = pytest.mark.unit


def _manifest(*, inputs=None, outputs=None, provides=None):
    return {
        "inputs": inputs or {},
        "outputs": outputs or {},
        "provides": provides or [],
    }


def _clock_input(*, episodic=False):
    spec = {"schema": "sim_turn_u64", "rate_hz": 100, "is_clock": True}
    if episodic:
        spec["turn_edge"] = "episodic"
    return spec


def _complete_graph():
    """Client/verifier and guard/state cycles, both broken episodically."""
    nodes = [
        {
            "id": "bridge",
            "inputs": {
                "joint_cmd": {"source": "guard/joint_safe", "queue_size": 10},
                "reset": {"source": "client/reset", "queue_size": 10},
                "turn_commit": {
                    "source": "turn-barrier/turn_commit",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
            },
            "outputs": ["state", "sim_turn"],
        },
        {
            "id": "turn-barrier",
            "inputs": {
                "sim_turn": {
                    "source": "bridge/sim_turn",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "done_0": {
                    "source": "client/turn_done",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "done_1": {
                    "source": "guard/turn_done",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "done_2": {
                    "source": "state/turn_done",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "done_3": {
                    "source": "verifier/turn_done",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
            },
            "outputs": ["turn", "turn_commit"],
        },
        {
            "id": "client",
            "inputs": {
                "turn": {
                    "source": "turn-barrier/turn",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "result": {"source": "verifier/result", "queue_size": 10},
            },
            "outputs": ["goal", "reset", "turn_done"],
        },
        {
            "id": "state",
            "inputs": {
                "turn": {
                    "source": "turn-barrier/turn",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "state": {"source": "bridge/state", "queue_size": 1},
                "goal": {"source": "client/goal", "queue_size": 10},
                "violation": {"source": "guard/violation", "queue_size": 10},
            },
            "outputs": ["command", "turn_done"],
        },
        {
            "id": "guard",
            "inputs": {
                "turn": {
                    "source": "turn-barrier/turn",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "command": {"source": "state/command", "queue_size": 10},
            },
            "outputs": ["joint_safe", "violation", "turn_done"],
        },
        {
            "id": "verifier",
            "inputs": {
                "turn": {
                    "source": "turn-barrier/turn",
                    "queue_size": 4,
                    "queue_policy": "backpressure",
                },
                "state": {"source": "bridge/state", "queue_size": 1},
                "goal": {"source": "client/goal", "queue_size": 10},
            },
            "outputs": ["result", "turn_done"],
        },
    ]
    for node in nodes:
        if node["id"] == "bridge":
            node["env"] = {
                "AISLE_LOCKSTEP": "1",
                "AISLE_TURN_OUTPUTS": ",".join(node["outputs"]),
            }
        elif node["id"] != "turn-barrier":
            node["env"] = {
                "AISLE_LOCKSTEP": "1",
                "AISLE_TURN_NODE": node["id"],
                "AISLE_TURN_OUTPUTS": ",".join(node["outputs"]),
            }
    manifests = {
        "bridge": _manifest(
            provides=["sim_bridge"],
            inputs={"joint_cmd": {}, "reset": {}, "turn_commit": _clock_input()},
            outputs={"state": {}, "sim_turn": {}},
        ),
        "turn-barrier": _manifest(
            provides=["turn_barrier"],
            inputs={"sim_turn": _clock_input(), "done": _clock_input()},
            outputs={"turn": {}, "turn_commit": {}},
        ),
        "client": _manifest(
            inputs={"turn": _clock_input(), "result": {"turn_edge": "episodic"}},
            outputs={"goal": {}, "reset": {}, "turn_done": {}},
        ),
        "state": _manifest(
            inputs={
                "turn": _clock_input(),
                "state": {},
                "goal": {},
                "violation": {"turn_edge": "episodic"},
            },
            outputs={"command": {}, "turn_done": {}},
        ),
        "guard": _manifest(
            inputs={"turn": _clock_input(), "command": {}},
            outputs={"joint_safe": {}, "violation": {}, "turn_done": {}},
        ),
        "verifier": _manifest(
            inputs={"turn": _clock_input(), "state": {}, "goal": {}},
            outputs={"result": {}, "turn_done": {}},
        ),
    }
    return nodes, manifests


def _codes(nodes, manifests):
    return {entry["code"] for entry in _clock_errors(nodes, manifests)}


def test_good_graph_with_service_and_guard_cycles_passes_clock_validation():
    """VAL-2/BRG-1: cycles containing episodic back-edges are valid."""
    nodes, manifests = _complete_graph()
    assert _clock_errors(nodes, manifests) == []


def test_simulation_cannot_opt_out_of_clock_validation_by_omitting_lockstep_env():
    """VAL-2/BRG-1: every validated simulator is lockstep; bring-up bypasses validate."""
    nodes, manifests = _complete_graph()
    nodes = [node for node in nodes if node["id"] != "turn-barrier"]
    for node in nodes:
        node["env"] = {}
        for port in ("turn", "turn_commit"):
            node.get("inputs", {}).pop(port, None)
        if "outputs" in node:
            node["outputs"] = [output for output in node["outputs"] if output != "turn_done"]
    assert "CLOCK_PATH_INCOMPLETE" in _codes(nodes, manifests)
    assert "CLOCK_COMMIT_COUNT" in _codes(nodes, manifests)


def test_compiled_runtime_plan_matches_validated_topology():
    """VAL-2/BRG-1: runtime scheduling consumes the topology the validator proved."""
    nodes, manifests = _complete_graph()
    plan = compile_turn_plan(nodes, manifests)
    assert plan["bridge"] == "bridge"
    assert set(plan["participants"]) == {"client", "state", "guard", "verifier"}
    assert plan["participants"]["client"]["inputs"]["result"] == {
        "source": "verifier",
        "output": "result",
        "edge": "episodic",
    }
    assert set(plan["done_ports"].values()) == set(plan["participants"])
    assert plan["bridge_outputs"] == ["sim_turn", "state"]
    assert plan["bridge_inputs"] == {
        "joint_cmd": {"source": "guard", "output": "joint_safe"},
        "reset": {"source": "client", "output": "reset"},
    }
    assert plan["participants"]["client"]["outputs"] == ["goal", "reset", "turn_done"]


def test_participant_output_config_must_match_the_graph_exactly():
    """CAP-1: runtime watermarks cannot omit a graph output by configuration."""
    nodes, manifests = _complete_graph()
    client = next(node for node in nodes if node["id"] == "client")
    client["env"] = {
        "AISLE_LOCKSTEP": "1",
        "AISLE_TURN_NODE": "client",
        "AISLE_TURN_OUTPUTS": "goal,turn_done",
    }
    assert "CLOCK_PATH_INCOMPLETE" in _codes(nodes, manifests)


def test_latest_wins_clock_is_rejected():
    """VAL-2/CAP-1: structural clocks require explicit positive backpressure queues."""
    nodes, manifests = _complete_graph()
    client = next(node for node in nodes if node["id"] == "client")
    client["inputs"]["turn"] = {"source": "turn-barrier/turn", "queue_size": 1}
    assert "CLOCK_DROPPED" in _codes(nodes, manifests)


def test_clock_from_non_barrier_source_is_rejected():
    """VAL-2: a participant clock must come from the validated terminal barrier."""
    nodes, manifests = _complete_graph()
    client = next(node for node in nodes if node["id"] == "client")
    client["inputs"]["turn"]["source"] = "bridge/state"
    assert "CLOCK_SOURCE_INVALID" in _codes(nodes, manifests)


def test_forward_path_node_without_clock_participation_is_rejected():
    """VAL-2: every node on a path to reset or motion participates."""
    nodes, manifests = _complete_graph()
    state = next(node for node in nodes if node["id"] == "state")
    state["inputs"].pop("turn")
    assert "CLOCK_PATH_INCOMPLETE" in _codes(nodes, manifests)


def test_forward_cycle_without_episodic_edge_is_rejected():
    """VAL-2: every causal cycle must contain an episodic edge."""
    nodes, manifests = _complete_graph()
    manifests = copy.deepcopy(manifests)
    manifests["state"]["inputs"]["violation"].pop("turn_edge")
    assert "CLOCK_CYCLE" in _codes(nodes, manifests)


@pytest.mark.parametrize("mutation", ["missing", "second", "wrong_source"])
def test_bridge_requires_exactly_one_terminal_commit(mutation):
    """VAL-2/BRG-1: exactly one validated terminal commit returns to the bridge."""
    nodes, manifests = _complete_graph()
    bridge = next(node for node in nodes if node["id"] == "bridge")
    if mutation == "missing":
        bridge["inputs"].pop("turn_commit")
    elif mutation == "second":
        bridge["inputs"]["turn_commit_1"] = {
            "source": "turn-barrier/turn_commit",
            "queue_size": 4,
            "queue_policy": "backpressure",
        }
    else:
        bridge["inputs"]["turn_commit"]["source"] = "client/reset"
    assert "CLOCK_COMMIT_COUNT" in _codes(nodes, manifests)


def test_committed_turn_plans_match_the_graphs_they_compile_from():
    """ADR-30: `graphs/turn_plans/*.json` are RUNTIME inputs, not artifacts.

    The barrier loads the committed file (`AISLE_TURN_PLAN:
    turn_plans/<stem>.json` in each graph) and refuses any participant whose
    watermark output set disagrees with the plan's. So a graph edit that
    does not regenerate its plan does not fail loudly at validate time — it
    kills the barrier on the first watermark, which surfaces as every
    episode hitting the ADR-23 wall clamp, relaunching, and the run
    reporting pass1 0.0 about twenty minutes later.

    That is exactly what an unregenerated plan cost in the round-2 review of
    #208, and nothing caught it: unit tests, `harness validate`, and
    `env_hash` were all green, because the plans are frozen INPUTS to the
    hash rather than derived from the graphs. One second here beats a
    nineteen-minute live run."""
    import json
    from pathlib import Path

    import yaml

    from aisle.harness.registry import load_manifests

    root = Path(__file__).resolve().parents[2]
    manifest_list, errors = load_manifests(root)
    assert not errors, errors
    manifests = {m["id"]: m for _, m in manifest_list}

    plans = sorted((root / "graphs" / "turn_plans").glob("expert_*.json"))
    assert plans, "no committed turn plans — the corpus moved and this test went blind"
    stale = []
    for plan_path in plans:
        graph = root / "graphs" / f"{plan_path.stem}.yaml"
        assert graph.is_file(), f"{plan_path.name} has no graph — an orphan plan is dead weight"
        expected = compile_turn_plan(yaml.safe_load(graph.read_text())["nodes"], manifests)
        if json.loads(plan_path.read_text()) != expected:
            stale.append(plan_path.name)
    assert not stale, (
        f"turn plans stale against their graphs: {stale} — regenerate with "
        "compile_turn_plan(graph_nodes, manifests); the barrier loads these at runtime"
    )


def _real_graph_and_manifests(stem="expert_t0"):
    from pathlib import Path

    import yaml

    from aisle.harness.registry import load_manifests, load_vocabulary

    root = Path(__file__).resolve().parents[2]
    manifest_list, errors = load_manifests(root)
    assert not errors, errors
    nodes = yaml.safe_load((root / "graphs" / f"{stem}.yaml").read_text())["nodes"]
    vocab = set(load_vocabulary(root))
    return root, nodes, {m["id"]: m for _, m in manifest_list}, vocab


def _mirror_graph_dir(tmp_path, root):
    """A stand-in `graphs/` whose relative node paths still resolve.

    The graph's nodes use `../src/aisle/...`, so pointing `graph_dir` at a
    bare tmp dir trips VAL-2 PATH_MANIFEST_MISMATCH first — and since the
    turn-plan rule runs behind the caller's `if not errors` gate, it would
    then be suppressed and the test would pass for the wrong reason."""
    (tmp_path / "src").symlink_to(root / "src")
    graph_dir = tmp_path / "graphs"
    graph_dir.mkdir()
    (graph_dir / "turn_plans").mkdir()
    return graph_dir


def test_validate_refuses_a_graph_whose_committed_turn_plan_is_stale(tmp_path):
    """ADR-30 / issue #213: the barrier loads the COMMITTED plan, and refuses
    any participant whose watermark output set disagrees with it. A graph
    edit that skips regenerating the plan therefore does not fail at the
    gate — it kills the barrier on the first watermark, and the operator
    sees only every episode wall-clamping and `pass1: 0.0` twenty minutes
    later (measured, round-2 review of #208).

    Driven through `validate_nodes` rather than the pure helper, because the
    lesson of #204 is that a rule nobody CALLS passes every test of the rule
    itself. `graph_dir` is redirected at a tmp copy of the plan so the real
    committed one is never touched."""
    import json

    from aisle.harness.validate import validate_nodes

    root, nodes, manifests, _VOCAB = _real_graph_and_manifests()
    graph_dir = _mirror_graph_dir(tmp_path, root)
    plans = graph_dir / "turn_plans"
    good = json.loads((root / "graphs" / "turn_plans" / "expert_t0.json").read_text())

    def codes(plan):
        (plans / "expert_t0.json").write_text(json.dumps(plan))
        errors, _ = validate_nodes(
            nodes, manifests, _VOCAB, "franka", False, graph_dir=graph_dir, root=root
        )
        return {e["code"] for e in errors}

    assert "TURN_PLAN_STALE" not in codes(good), "the committed plan is already stale"

    stale = json.loads(json.dumps(good))
    stale["participants"]["reset"]["outputs"] = [
        o for o in stale["participants"]["reset"]["outputs"] if o != "reset_refused"
    ]
    assert "TURN_PLAN_STALE" in codes(stale), "the exact #208 mismatch validated clean"

    dropped = json.loads(json.dumps(good))
    dropped["participants"].pop("reset")
    assert "TURN_PLAN_STALE" in codes(dropped)


def test_an_unreadable_plan_is_refused_but_a_relocated_graph_is_not(tmp_path):
    """The rule is scoped to graphs that sit BESIDE their plan.

    A relocated copy — an instrumented run graph, or the staged copy
    `harness swap` validates — legitimately differs from the committed plan
    (instrumentation adds the recorder; A7 rewrites the topology and
    compiles its own). Comparing those would refuse correct graphs, which is
    exactly what the first revision of this rule did: it broke all six
    `harness swap` tests. A genuinely absent plan stays the barrier's own
    refusal, which is loud rather than silent — silence was the defect.

    A plan that IS beside the graph and cannot be parsed is refused here."""
    from aisle.harness.validate import validate_nodes

    root, nodes, manifests, _VOCAB = _real_graph_and_manifests()
    graph_dir = _mirror_graph_dir(tmp_path, root)

    errors, _ = validate_nodes(
        nodes, manifests, _VOCAB, "franka", False, graph_dir=graph_dir, root=root
    )
    assert "TURN_PLAN_STALE" not in {e["code"] for e in errors}, (
        "a relocated graph was refused for a plan that is not its own"
    )

    (graph_dir / "turn_plans" / "expert_t0.json").write_text("{not json")
    errors, _ = validate_nodes(
        nodes, manifests, _VOCAB, "franka", False, graph_dir=graph_dir, root=root
    )
    assert "TURN_PLAN_STALE" in {e["code"] for e in errors}, "an unreadable plan validated clean"


def test_an_episodic_edge_whose_producer_is_upstream_is_refused():
    """ADR-30 / issue #220: `turn_edge: episodic` is only sound on a genuine
    BACK edge — one whose producer the barrier schedules AFTER the consumer.

    On a forward edge it is not a harmless mislabel. The consumer resolves an
    episodic input from the PREVIOUS turn's watermark, while dora delivers
    the current turn's message, so the very first time the producer emits,
    `ParticipantTurn` sees an excess input with a wrong stamp and raises. The
    barrier's watchdog then kills the dataflow about a second in. Measured on
    `expert_s1`: every episode wall-clamps and the run reports pass1 0.0
    twenty minutes later.

    `_clock_errors` used `turn_edge` only to DELETE edges from the cycle
    check, so nothing ever asked whether the producer was actually
    downstream. Two graphs shipped broken before this rule existed."""
    nodes, manifests = _complete_graph()
    assert _clock_errors(nodes, manifests) == []

    # client -> state -> guard is a forward chain, so `client` sits two
    # layers above `guard` and emits well before it opens. Feeding guard from
    # client and calling THAT episodic is the #220 defect exactly: the same
    # shape as reset(L2) -> waypoint-nav(L4) on expert_s1.
    guard = next(n for n in nodes if n["id"] == "guard")
    guard["inputs"]["goal"] = {"source": "client/goal", "queue_size": 10}
    manifests["guard"]["inputs"]["goal"] = {"turn_edge": "episodic"}
    assert "EPISODIC_NOT_A_BACK_EDGE" in _codes(nodes, manifests)

    # and the same edge declared FORWARD is fine — it is only the episodic
    # label that is wrong, so the rule must not just reject the extra edge
    manifests["guard"]["inputs"]["goal"] = {}
    assert "EPISODIC_NOT_A_BACK_EDGE" not in _codes(nodes, manifests)


def test_a_genuine_back_edge_stays_valid():
    """The control: `result` (verifier -> client) and `violation`
    (guard -> state) ARE back edges — they close cycles the forward DAG
    cannot. A rule that flagged those would refuse every correct lockstep
    graph, so this pins that the check discriminates rather than just
    rejecting the word `episodic`."""
    nodes, manifests = _complete_graph()
    assert manifests["client"]["inputs"]["result"]["turn_edge"] == "episodic"
    assert manifests["state"]["inputs"]["violation"]["turn_edge"] == "episodic"
    assert "EPISODIC_NOT_A_BACK_EDGE" not in _codes(nodes, manifests)


def test_no_shipped_graph_declares_a_forward_edge_episodic():
    """The corpus regression for #220. Two graphs shipped with this defect
    (`expert_s1` via waypoint-nav/nav-action `reset_done`, `expert_t4` via
    task-state-machine `episode_result`) and every gate stayed green, because
    validation never modelled what episodic MEANS."""
    from pathlib import Path

    import yaml

    from aisle.harness.registry import load_manifests

    root = Path(__file__).resolve().parents[2]
    manifest_list, errors = load_manifests(root)
    assert not errors, errors
    manifests = {m["id"]: m for _, m in manifest_list}

    broken = {}
    for graph in sorted((root / "graphs").glob("*.yaml")):
        nodes = yaml.safe_load(graph.read_text())["nodes"]
        codes = [
            e for e in _clock_errors(nodes, manifests) if e["code"] == "EPISODIC_NOT_A_BACK_EDGE"
        ]
        if codes:
            broken[graph.name] = [e["detail"] for e in codes]
    assert not broken, broken


def test_write_turn_plan_unblocks_a_graph_whose_plan_went_stale(tmp_path):
    """Issue #227: the barrier loads the COMMITTED plan, so #214's
    TURN_PLAN_STALE refuses a graph whose topology moved — correctly, since
    a stale plan kills the barrier at the first watermark instead of failing
    at the gate. But `compile_turn_plan` was reachable only from Python and
    the research contract never mentioned turn plans, so an agent editing
    its own `graphs/agent_campaign.yaml` had no way out and burned metered
    budget (ADR-21) rediscovering the rule.

    Drives the whole loop through the same entry point the agent uses:
    stale -> refused -> `--write-turn-plan` -> validates."""
    import json
    import shutil
    from pathlib import Path

    from aisle.harness.validate import validate, write_turn_plan

    root = Path(__file__).resolve().parents[2]
    graph = root / "graphs" / "expert_t0.yaml"
    plan_path = root / "graphs" / "turn_plans" / "expert_t0.json"
    backup = tmp_path / "expert_t0.json"
    shutil.copy(plan_path, backup)
    try:
        stale = json.loads(plan_path.read_text())
        victim = sorted(stale["participants"])[0]
        stale["participants"][victim]["outputs"] = stale["participants"][victim]["outputs"][:-1]
        plan_path.write_text(json.dumps(stale, sort_keys=True, separators=(",", ":")) + "\n")

        refused = validate(graph, root, "franka", False)
        assert "TURN_PLAN_STALE" in {e["code"] for e in refused["errors"]}, refused["errors"]

        written = write_turn_plan(graph, root)
        assert written["ok"] is True, written
        assert str(plan_path) in written["written"], written

        assert validate(graph, root, "franka", False)["ok"] is True
        # idempotent: the recovery reproduces the committed bytes exactly
        assert plan_path.read_text() == backup.read_text()
    finally:
        shutil.copy(backup, plan_path)


def test_write_turn_plan_refuses_a_graph_that_has_no_plan(tmp_path):
    """CON-8: a free-run graph declares no AISLE_TURN_PLAN, so there is
    nothing to rewrite. Refuse loudly rather than write an artifact nobody
    reads, or return ok having done nothing — both of which read as success
    to the agent that just ran it."""
    from pathlib import Path

    import yaml

    from aisle.harness.validate import write_turn_plan

    root = Path(__file__).resolve().parents[2]
    doc = yaml.safe_load((root / "graphs" / "expert_t0.yaml").read_text())
    for node in doc["nodes"]:
        env = node.get("env")
        if isinstance(env, dict):
            env.pop("AISLE_TURN_PLAN", None)
    graph = tmp_path / "free_run.yaml"
    graph.write_text(yaml.safe_dump(doc, sort_keys=False))

    report = write_turn_plan(graph, root)
    assert report["ok"] is False
    assert {e["code"] for e in report["errors"]} == {"TURN_PLAN_ABSENT"}, report["errors"]
