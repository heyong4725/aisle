"""Pinned Codex 0.153.4 Code Mode wire schema; transport code is AISLE-owned."""

from pathlib import Path

from google.protobuf import descriptor_pool, message_factory

_POOL = descriptor_pool.DescriptorPool()
DESCRIPTOR = _POOL.AddSerializedFile(Path(__file__).with_name("descriptor.pb").read_bytes())


def message(name, data=None, **fields):
    """Construct or decode one named message from the pinned schema."""
    descriptor = _POOL.FindMessageTypeByName("codex.code_mode.v1." + name)
    cls = message_factory.GetMessageClass(descriptor)
    return cls.FromString(data) if data is not None else cls(**fields)
