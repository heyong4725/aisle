"""Test fixture: records base_pose and base_scan payloads (+ metadata) to a
JSONL file for mobile-bridge graph tests (SPEC 210 MOB-1)."""

import json
import os

from dora import Node


def main() -> None:
    out = open(os.environ["REC_OUT"], "w", buffering=1)
    node = Node()
    for event in node:
        if event["type"] != "INPUT":
            continue
        arrow = event["value"]
        value = arrow.to_numpy(zero_copy_only=False).tolist()
        out.write(
            json.dumps(
                {
                    "id": event["id"],
                    "value": value,
                    "dtype": str(arrow.type),  # observed Arrow dtype (schema conformance)
                    "meta": dict(event.get("metadata") or {}),
                },
                default=str,  # dora stamps a datetime in metadata (see recorder.py)
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
