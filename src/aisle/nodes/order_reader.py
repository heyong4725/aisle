"""order-reader, ORACLE rung (SPEC 200 RS-10, ADR-17): the order slip is
published directly from the episode goal. The realistic rung (OCR/VLM on
the rendered slip) swaps behind the same manifest. Pure core, thin dora
main (CON-12)."""

from __future__ import annotations


def read_order(episode_goal: dict) -> dict:
    """The structured order from an S1 episode goal — loud on a goal
    without one (an order-reader in a non-order episode is a wiring bug)."""
    if "order" not in episode_goal:
        raise ValueError(f"episode goal carries no order: {sorted(episode_goal)}")
    return {"order": episode_goal["order"], "seed": episode_goal.get("seed")}


def main() -> None:
    import json

    import pyarrow as pa
    from dora import Node

    from aisle.topics import make_sender

    node = Node()
    send = make_sender(node)
    for event in node:
        if event["type"] == "INPUT" and event["id"] == "episode_goal":
            goal = json.loads(event["value"][0].as_py())
            order = read_order(goal)
            send("order", pa.array([json.dumps(order)]), event.get("metadata") or {})


if __name__ == "__main__":
    main()
