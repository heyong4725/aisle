"""MON-2/CON-4: primitive payload coverage beyond the observed T1 rollout."""

import numpy as np
import pyarrow as pa
import pytest

from aisle.harness.typed_node_requests import pack_array, unpack_array
from aisle.monolith.wire import decode, encode

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "dtype",
    [
        "int8",
        "int16",
        "int32",
        "int64",
        "uint8",
        "uint16",
        "uint32",
        "uint64",
        "float16",
        "float32",
        "float64",
        "bool",
    ],
)
@pytest.mark.parametrize("shape", ["sliced", "empty", "all_null"])
def test_primitive_payload_preserves_limits_validity_and_slices(dtype, shape):
    """MON-2/CON-4: each declared primitive retains type, nulls and boundary values."""
    numpy_type = np.dtype(dtype)
    if numpy_type.kind in "iu":
        limits = np.iinfo(numpy_type)
        values = [limits.min, 0, limits.max]
    elif numpy_type.kind == "f":
        limits = np.finfo(numpy_type)
        values = [-limits.max, -0.0, limits.max]
    else:
        values = [False, True, False]
    arrow_type = pa.from_numpy_dtype(numpy_type)
    source = pa.array(
        np.array([0, *values, 0], dtype=numpy_type),
        mask=np.array([False, False, True, False, False]),
        type=arrow_type,
    )
    if shape == "sliced":
        source = source.slice(1, 3)
    elif shape == "empty":
        source = source.slice(2, 0)
    else:
        source = pa.nulls(3, type=arrow_type)
    restored = unpack_array(decode(encode(pack_array(source))))
    assert restored.type == source.type
    assert restored.equals(source)
