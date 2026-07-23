"""Test fixture: drives the mobile base with a constant forward base_cmd
(SPEC 210 MOB-1). v, omega from $BASE_V, $BASE_OMEGA (default 0.3, 0.0)."""

import os

import numpy as np
import pyarrow as pa
from dora import Node


def main() -> None:
    v = float(os.environ.get("BASE_V", "0.3"))
    omega = float(os.environ.get("BASE_OMEGA", "0.0"))
    node = Node()
    for event in node:
        if event["type"] == "INPUT" and event["id"] == "tick":
            node.send_output(
                "base_cmd", pa.array(np.array([v, omega], dtype=np.float32)), metadata={}
            )


if __name__ == "__main__":
    main()
