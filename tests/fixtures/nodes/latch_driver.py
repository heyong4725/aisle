"""Test fixture (MOB-3 watchdog): send ONE forward base_cmd then go silent,
so the guard's tick watchdog must stop the latched command with [0,0]."""

import numpy as np
import pyarrow as pa
from dora import Node


def main() -> None:
    node = Node()
    sent = False
    for event in node:
        if event["type"] == "INPUT" and event["id"] == "tick" and not sent:
            node.send_output(
                "base_cmd", pa.array(np.array([0.5, 0.0], dtype=np.float32)), metadata={}
            )
            sent = True


if __name__ == "__main__":
    main()
