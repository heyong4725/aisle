"""task-planner (design doc §11.4, ADR-17): episode goal -> sequenced
subtasks. THE agent-iteration surface — this hub stub emits a flat,
deterministic goto/pick/place sequence per goal parameter (no
replanning); agents are expected to replace it. Pure core, thin dora
main (CON-12)."""

from __future__ import annotations


def _zone_location(plano: dict, slot_id: str) -> str:
    zone = plano["slots"][slot_id]["shelf_zone"]
    return f"shelf_zone_{zone.removeprefix('aisle_').upper()}"


def plan_subtasks(episode_goal: dict, plano: dict) -> list[dict]:
    """The deterministic subtask sequence for one episode goal (CON-5):
    S1 order lines -> per unit: goto a stocked slot's zone, pick the
    product, goto the counter, place; S2 restock -> bin pickups into the
    assigned slots; S3 misplaced -> fetch each item and return it home."""
    subtasks: list[dict] = []
    for line in episode_goal.get("order", []):
        # source from the HIGHEST level first (ADR-18): upper slots have
        # open sky for the proven top-down grasp; v0 store units are
        # uniform-depth, so lower levels sit under a board (the T10 lesson)
        sources = sorted(
            (
                slot_id
                for slot_id, slot in plano["slots"].items()
                if slot["category"] == line["product"]
            ),
            key=lambda s: (-int(s.split("-L")[1].split("-")[0]), s),
        )
        if len(sources) < line["qty"]:
            raise ValueError(f"order line {line} exceeds planogram stock {len(sources)}")
        for k in range(line["qty"]):
            slot_id = sources[k]
            subtasks += [
                {"op": "goto", "location": _zone_location(plano, slot_id)},
                {"op": "pick", "category": line["product"], "slot": slot_id},
                {"op": "goto", "location": "counter"},
                {"op": "place", "where": "counter"},
            ]
    for entry in episode_goal.get("restock", []):
        subtasks += [
            {"op": "goto", "location": "bin"},
            {"op": "pick", "category": entry["category"], "slot": "bin"},
            {"op": "goto", "location": _zone_location(plano, entry["slot"])},
            {"op": "place", "where": "slot", "slot": entry["slot"]},
        ]
    for entry in episode_goal.get("misplaced", []):
        subtasks += [
            {"op": "goto", "location": _zone_location(plano, entry["found_in"])},
            {"op": "pick", "item": entry["item"], "slot": entry["found_in"]},
            {"op": "goto", "location": _zone_location(plano, entry["belongs_in"])},
            {"op": "place", "where": "slot", "slot": entry["belongs_in"]},
        ]
    if not subtasks:
        raise ValueError(f"episode goal has no plannable parameters: {sorted(episode_goal)}")
    return subtasks


def main() -> None:
    import json

    import pyarrow as pa
    from dora import Node

    from aisle.scenes.store import load_planogram
    from aisle.topics import make_sender

    node = Node()
    send = make_sender(node)
    plano = load_planogram()
    for event in node:
        if event["type"] == "INPUT" and event["id"] == "episode_goal":
            goal = json.loads(event["value"][0].as_py())
            plan = plan_subtasks(goal, plano)
            send(
                "subtask_plan",
                pa.array([json.dumps({"subtasks": plan})]),
                event.get("metadata") or {},
            )


if __name__ == "__main__":
    main()
