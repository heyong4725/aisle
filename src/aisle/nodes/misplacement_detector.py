"""misplacement-detector, ORACLE rung (SPEC 200 RS-10, ADR-17): detected
identity vs planogram — an item occupying a slot whose assigned category
differs from the item's is misplaced. Same occupancy geometry as the
verifier (cannot drift). Pure core, thin dora main (CON-12)."""

from __future__ import annotations

import numpy as np

from aisle.verifier.retail import RetailCfg, _item_pose, _occupies_slot


def detect_misplacements(oracle_state: np.ndarray, plano: dict, cfg: RetailCfg) -> dict:
    """{"misplaced": [{"item", "found_in", "belongs_in"}...]} — shelf items
    occupying a slot of the WRONG category, in stock order (CON-5).
    belongs_in is the item's home slot (its id prefix); bin stock has no
    home and is never reported."""
    state = np.asarray(oracle_state, dtype=np.float32).reshape(-1)
    misplaced = []
    for idx, item_id in enumerate(cfg.item_ids):
        if item_id.startswith("bin#"):
            continue
        pos, _ = _item_pose(state, idx)
        for slot_id, slot in plano["slots"].items():
            if not _occupies_slot(pos, cfg.half_extents[idx], slot_id, plano, cfg):
                continue
            if slot["category"] != cfg.item_categories[idx]:
                misplaced.append(
                    {
                        "item": item_id,
                        "found_in": slot_id,
                        "belongs_in": item_id.split("#")[0],
                    }
                )
            break  # occupancy radii don't overlap: one slot per item
    return {"misplaced": misplaced}


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
            cfg = build_retail_cfg(plano, json.loads(event["value"][0].as_py()))
        elif event["id"] == "oracle_state" and cfg is not None:
            state = event["value"].to_numpy(zero_copy_only=False)
            report = detect_misplacements(state, plano, cfg)
            send("misplacement_report", pa.array([json.dumps(report)]), event.get("metadata") or {})


if __name__ == "__main__":
    main()
