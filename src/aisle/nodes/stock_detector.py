"""stock-detector, ORACLE rung (SPEC 200 RS-10, ADR-17): empty-slot
detection + category from the planogram, diffing oracle_state against the
slot table with the SAME occupancy geometry as the retail verifier — the
detector and the referee cannot drift. Realistic rung (shelf-label vision)
swaps behind the same manifest. Pure core, thin dora main (CON-12)."""

from __future__ import annotations

import numpy as np

from aisle.verifier.retail import RetailCfg, _item_pose, _occupies_slot


def detect_stock(oracle_state: np.ndarray, plano: dict, cfg: RetailCfg) -> dict:
    """{"empty_slots": [{"slot", "category"}...]} — slots with NO occupant,
    in planogram order (deterministic, CON-5)."""
    state = np.asarray(oracle_state, dtype=np.float32).reshape(-1)
    poses = [_item_pose(state, i) for i in range(len(cfg.item_ids))]
    empty = []
    for slot_id, slot in plano["slots"].items():
        occupied = any(
            _occupies_slot(pos, cfg.half_extents[idx], slot_id, plano, cfg)
            for idx, (pos, _) in enumerate(poses)
        )
        if not occupied:
            empty.append({"slot": slot_id, "category": slot["category"]})
    return {"empty_slots": empty}


def main() -> None:
    import json

    import pyarrow as pa
    from dora import Node

    from aisle.scenes.store import load_planogram
    from aisle.topics import make_sender
    from aisle.verifier.retail import build_retail_cfg

    node = Node()
    send = make_sender(node)
    plano = load_planogram()
    cfg = None
    for event in node:
        if event["type"] != "INPUT":
            continue
        if event["id"] == "episode_goal":
            # the goal fixes the item roster (stock order) for this episode
            cfg = build_retail_cfg(plano, json.loads(event["value"][0].as_py()))
        elif event["id"] == "oracle_state" and cfg is not None:
            state = event["value"].to_numpy(zero_copy_only=False)
            report = detect_stock(state, plano, cfg)
            send("stock_report", pa.array([json.dumps(report)]), event.get("metadata") or {})


if __name__ == "__main__":
    main()
