"""Consumer-side tests for the nav_result episode correlation (issue #179).

`nav_result_is_current` is a pure predicate tested in test_mobility.py. This
file tests the CALLERS that feed it — the half that decides whether it ever
returns True in production.

Why (issue #179 review): deleting the two stamping lines
(`pending = {**pending, "goal_id": goal_id}`) from all three consumers left
the entire unit suite green. With that mutation shipped, `pending` never
carries a goal_id, every nav_result is rejected as stale, and every episode
in all four mobile graphs hangs on its first nav leg forever. The predicate
is fail-closed by design, which is exactly why the untested half is the half
where a mistake is silent and total.

The three consumers are near-identical by construction, so all three are
driven through the same table.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]

# (label, module loader) for every node that consumes nav_result
CONSUMERS = [
    ("s1-expert", "src/aisle/nodes/s1_expert.py"),
    ("s1-driver-v2", "skills/s1-driver-v2/s1_driver_v2.py"),
    ("s3-driver-v1", "skills/s3-driver-v1/s3_driver_v1.py"),
]


class FakeNode:
    def __init__(self, events):
        self._events = events
        self.sent: list[tuple[str, list, dict]] = []

    def __iter__(self):
        return iter(self._events)

    def send_output(self, topic, value, metadata=None):
        self.sent.append((topic, list(value.to_pylist()), metadata or {}))


def _inp(topic, value, metadata=None):
    return {"type": "INPUT", "id": topic, "value": value, "metadata": metadata or {}}


def _load_main(relpath: str):
    """Import a consumer by PATH — the two driver skills are not packages."""
    path = ROOT / relpath
    name = "aisle_test_" + path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.main


def nav_goals(node: FakeNode) -> list[dict]:
    """(payload, metadata) of every nav_goal the consumer emitted."""
    return [{"goal": json.loads(v[0]), "meta": m} for t, v, m in node.sent if t == "nav_goal"]


def drive(relpath: str, events, monkeypatch) -> FakeNode:
    node = FakeNode(events)
    fake_dora = types.ModuleType("dora")
    fake_dora.Node = lambda: node
    monkeypatch.setitem(sys.modules, "dora", fake_dora)
    monkeypatch.setenv("AISLE_EMBODIMENT", "mobile")
    monkeypatch.setenv("AISLE_SCENE", "store")
    _load_main(relpath)()
    return node


#: The shortest path from a cold consumer to its first `send_nav`. The S1
#: pair plan from `subtask_plan`; s3-driver-v1 plans from `episode_goal` via
#: `swap_plan`, so it needs a feasible 2-entry swap (both slots above L0,
#: which v1 refuses).
_S1_PLAN = {"subtasks": [{"op": "goto", "location": "counter"}]}
_S3_GOAL = {
    "misplaced": [
        {"item": "A1-L1-S0#0", "found_in": "A2-L1-S0", "belongs_in": "A1-L1-S0"},
        {"item": "A2-L1-S0#0", "found_in": "A1-L1-S0", "belongs_in": "A2-L1-S0"},
    ]
}


def plan_one_goto(relpath: str) -> dict:
    if "s3_driver" in relpath:
        return _inp("episode_goal", pa.array([json.dumps(_S3_GOAL)]))
    return _inp("subtask_plan", pa.array([json.dumps(_S1_PLAN)]))


def nav_result(status: str, goal_id, failure=None) -> dict:
    payload = {"status": status, "failure": failure, "t_end": 0.0}
    meta = {} if goal_id is None else {"goal_id": goal_id}
    return _inp("nav_result", pa.array([json.dumps(payload)]), meta)


def reset_done(seq: int) -> dict:
    return _inp("reset_done", pa.array(np.zeros(1, dtype=np.uint32)), {"seq": seq})


@pytest.mark.parametrize(("label", "relpath"), CONSUMERS, ids=[c[0] for c in CONSUMERS])
def test_send_nav_stamps_the_issued_goal_id_onto_pending(label, relpath, monkeypatch):
    """TC-7 (issue #179): the outbound goal_id must reach `pending`, or the
    inbound match can never succeed.

    Observed through outputs only: a FAILED nav_result carrying the id the
    consumer just issued triggers a retry, so a second nav_goal appears.
    Delete the stamping and `pending` has no goal_id, the reply is judged
    stale, no retry is emitted, and the episode hangs — which is what this
    catches."""
    node = drive(relpath, [plan_one_goto(relpath)], monkeypatch)
    issued = nav_goals(node)
    assert issued, f"{label}: no nav_goal was emitted; the setup proves nothing"
    goal_id = issued[0]["meta"].get("goal_id")
    assert goal_id, f"{label}: nav_goal carried no goal_id: {issued[0]['meta']}"

    replied = drive(
        relpath,
        [plan_one_goto(relpath), nav_result("fail", goal_id, failure="blocked")],
        monkeypatch,
    )
    assert len(nav_goals(replied)) == 2, (
        f"{label}: the consumer ignored a reply carrying its OWN goal_id "
        f"({goal_id!r}) — pending was never stamped, so every episode hangs"
    )


@pytest.mark.parametrize(("label", "relpath"), CONSUMERS, ids=[c[0] for c in CONSUMERS])
def test_a_foreign_goal_id_does_not_advance_the_subtask(label, relpath, monkeypatch):
    """The #179 core, consumer side: a result from a leg the consumer is NOT
    waiting on — above all one carried over from a previous episode — must
    not complete or retry the live subtask."""
    node = drive(
        relpath,
        [plan_one_goto(relpath), nav_result("fail", "nav-999", failure="blocked")],
        monkeypatch,
    )
    assert len(nav_goals(node)) == 1, (
        f"{label}: a foreign goal_id advanced the live subtask: {nav_goals(node)}"
    )


@pytest.mark.parametrize(("label", "relpath"), CONSUMERS, ids=[c[0] for c in CONSUMERS])
def test_a_result_with_no_goal_id_is_not_trusted(label, relpath, monkeypatch):
    """BG-3-style trust boundary: an unstamped reply means "cannot tell",
    and cannot-tell must not complete a subtask."""
    node = drive(
        relpath, [plan_one_goto(relpath), nav_result("fail", None, failure="blocked")], monkeypatch
    )
    assert len(nav_goals(node)) == 1, nav_goals(node)


@pytest.mark.parametrize(("label", "relpath"), CONSUMERS, ids=[c[0] for c in CONSUMERS])
def test_every_nav_goal_carries_the_episode_epoch(label, relpath, monkeypatch):
    """MOB-2/TC-7 (issue #179 review): the OUTBOUND half of the episode
    correlation. waypoint-nav refuses a goal stamped with a stale epoch, so
    an unstamped goal silently loses that protection — nav would accept a
    goal that crossed the boundary in flight and drive the previous
    episode's target."""
    node = drive(relpath, [reset_done(seq=11), plan_one_goto(relpath)], monkeypatch)
    issued = nav_goals(node)
    assert issued, f"{label}: no nav_goal emitted"
    assert issued[0]["meta"].get("episode_epoch") == 11, (
        f"{label}: nav_goal did not carry the episode it was planned in: {issued[0]['meta']}"
    )
