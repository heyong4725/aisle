"""MON-2/MON-5/MON-6: authored graph wiring survives trusted-host replacement."""

import copy

import pytest
import yaml
from test_typed_validation_snapshot import ROOT

pytestmark = pytest.mark.unit


MODULES = {
    "segmented-pose": "segmented_pose",
    "grasp-planner-topdown": "grasp_topdown",
    "ik-trajectory": "ik_trajectory",
    "task-state-machine": "task_state_machine",
}


def _inputs():
    baseline = yaml.safe_load((ROOT / "graphs/expert_t1.yaml").read_text())
    bindings = {}
    for node in baseline["nodes"]:
        if node["id"] in MODULES:
            bindings[node["id"]] = {
                "config_path": "/private/controller/" + node["id"] + ".json",
                "config_sha256": "1" * 64,
                "module": "aisle.nodes." + MODULES[node["id"]],
                "outputs": [name for name in node["outputs"] if name != "turn_done"],
                "wall_outputs": [],
                "configuration": {"environment": dict(node.get("env", {})), "arguments": []},
            }
    return baseline, bindings


def test_graph_replacement_preserves_routes_and_keeps_authored_env_off_host():
    """MON-2/MON-6: the transport graph retains policy edges without authored startup env."""
    from aisle.harness.typed_graph_hosts import replace_authored_nodes

    baseline, bindings = _inputs()
    authored = copy.deepcopy(baseline)
    node = next(n for n in authored["nodes"] if n["id"] == "segmented-pose")
    node["env"]["PYTHONPATH"] = "/authored/path"
    bindings[node["id"]]["configuration"]["environment"] = dict(node["env"])
    original = copy.deepcopy(authored)
    result = replace_authored_nodes(authored, baseline, bindings, ROOT)
    assert authored == original
    for old, new in zip(authored["nodes"], result["nodes"], strict=True):
        if old["id"] not in bindings:
            assert new == old
            continue
        assert new["inputs"] == old["inputs"]
        assert new["outputs"] == old["outputs"]
        assert new["path"] == str(ROOT / "src/aisle/harness/typed_node_host.py")
        assert "env" not in new
        assert "--config-sha256" in new["args"]


@pytest.mark.parametrize(
    "mutation", ["trusted", "build", "module", "binding", "global_env", "missing"]
)
def test_graph_refuses_unbound_or_privileged_changes(mutation):
    """MON-5/MON-6/MON-13: graph replacement never hides changed authority or missing bindings."""
    from aisle.harness.typed_graph_hosts import GraphHostError, replace_authored_nodes

    baseline, bindings = _inputs()
    authored = copy.deepcopy(baseline)
    node = next(n for n in authored["nodes"] if n["id"] == "segmented-pose")
    if mutation == "trusted":
        authored["nodes"][0]["env"]["AISLE_STEP_WITHOUT_RESET"] = "1"
    elif mutation == "build":
        node["build"] = "touch /private/controller/marker"
    elif mutation == "module":
        node["path"] = "../src/aisle/turn_node.py"
    elif mutation == "binding":
        bindings[node["id"]]["outputs"] = []
    elif mutation == "global_env":
        authored["env"] = {"PYTHONPATH": "/authored"}
    else:
        bindings.pop(node["id"])
    with pytest.raises(GraphHostError):
        replace_authored_nodes(authored, baseline, bindings, ROOT)


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ("one # comment\ntwo", ["one", "two"]),
        ('one#two ""', ["one#two", ""]),
        ('"\\$value" a\\\nb', ["$value", "ab"]),
        ('"two words" literal$(value)', ["two words", "literal$(value)"]),
    ],
)
def test_node_argument_decoding_matches_dora(arguments, expected):
    """MON-2: pinned Dora shell tokenization does not expand or alter authored arguments."""
    from aisle.harness.typed_graph_hosts import node_configuration

    assert node_configuration({"args": arguments})["arguments"] == expected


def test_scalar_environment_matches_dora_display():
    """MON-2: typed scalar environment values retain Dora's process string semantics."""
    from aisle.harness.typed_graph_hosts import node_configuration

    assert node_configuration({"env": {"A": True, "B": 2, "C": 1e-7, "D": -0.0}})[
        "environment"
    ] == {
        "A": "true",
        "B": "2",
        "C": "0.0000001",
        "D": "-0",
    }


def test_environment_expansion_cannot_read_controller_ambient(monkeypatch):
    """MON-6: unresolved graph variables must not be expanded from controller state."""
    from aisle.harness.typed_graph_hosts import GraphHostError, node_configuration

    monkeypatch.setenv("CONTROLLER_ONLY", "private-sentinel")
    with pytest.raises(GraphHostError, match="admitted value binding"):
        node_configuration({"env": {"SETTING": "$CONTROLLER_ONLY"}})


@pytest.mark.parametrize("arguments", ['"unterminated', "trailing\\"])
def test_incomplete_argument_quoting_is_not_repaired(arguments):
    """MON-2: malformed argument syntax is refused rather than silently completed."""
    from aisle.harness.typed_graph_hosts import GraphHostError, node_configuration

    with pytest.raises(GraphHostError):
        node_configuration({"args": arguments})


@pytest.mark.parametrize(
    "value, expected",
    [
        ("$HOME/file", "/worker/home/file"),
        ("${MISSING:-fallback}", "fallback"),
        ("${EMPTY:-fallback}", ""),
        ("$$HOME", "$HOME"),
        ("${MISSING:-$HOME}", "$HOME"),
        ("${HOME", "${HOME"),
        ("$NUMBER", "1"),
        ("1.0", "1"),
        ("001", "1"),
    ],
)
def test_environment_expands_only_explicit_values(value, expected, monkeypatch):
    """MON-2/MON-6: Dora expansion and scalar parsing use only admitted worker values."""
    from aisle.harness.typed_graph_hosts import node_configuration

    monkeypatch.setenv("HOME", "/controller/home")
    result = node_configuration(
        {"env": {"SETTING": value}},
        expansion_environment={"HOME": "/worker/home", "EMPTY": "", "NUMBER": "001"},
    )
    assert result["environment"]["SETTING"] == expected


def test_graph_binding_checks_expanded_configuration():
    """MON-13: graph replacement compares the resolved worker settings with the binding."""
    from aisle.harness.typed_graph_hosts import replace_authored_nodes

    baseline, bindings = _inputs()
    authored = copy.deepcopy(baseline)
    node = next(n for n in authored["nodes"] if n["id"] == "segmented-pose")
    node["env"]["CACHE"] = "$HOME/cache"
    bindings[node["id"]]["configuration"]["environment"]["CACHE"] = "/worker/home/cache"
    result = replace_authored_nodes(
        authored,
        baseline,
        bindings,
        ROOT,
        expansion_environments={"segmented-pose": {"HOME": "/worker/home"}},
    )
    assert all("$HOME" not in str(n.get("env")) for n in result["nodes"])


@pytest.mark.parametrize("scope", ["global", "trusted"])
def test_graph_refuses_trusted_setting_type_drift(scope):
    """MON-6/MON-13: equal-valued numeric substitutions cannot alter trusted settings."""
    from aisle.harness.typed_graph_hosts import GraphHostError, replace_authored_nodes

    baseline, bindings = _inputs()
    settings = baseline if scope == "global" else baseline["nodes"][0]
    settings.setdefault("env", {})["AISLE_TEST_SETTING"] = 1
    authored = copy.deepcopy(baseline)
    changed = authored if scope == "global" else authored["nodes"][0]
    changed["env"]["AISLE_TEST_SETTING"] = True
    with pytest.raises(GraphHostError):
        replace_authored_nodes(authored, baseline, bindings, ROOT)
