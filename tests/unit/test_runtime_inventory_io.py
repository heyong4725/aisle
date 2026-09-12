"""MON-13: complete runtime inventories remain fresh under bounded concurrent I/O."""

import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from aisle.harness.matched_runtime import RuntimeDrift, capture_runtime

pytestmark = pytest.mark.unit


def test_runtime_file_reads_overlap_without_unbounded_handles(tmp_path, monkeypatch):
    """MON-13: slow file opens overlap with a fixed, small handle bound."""
    root = tmp_path.resolve() / "runtime"
    root.mkdir()
    for index in range(12):
        (root / str(index)).write_bytes(b"runtime bytes")
    original = Path.open
    lock = threading.Lock()
    overlap = threading.Event()
    active = peak = 0

    @contextmanager
    def observed(path, *args, **kwargs):
        nonlocal active, peak
        with original(path, *args, **kwargs) as stream:
            with lock:
                active += 1
                peak = max(peak, active)
                if active >= 2:
                    overlap.set()
            try:
                overlap.wait(0.02)
                yield stream
            finally:
                with lock:
                    active -= 1

    monkeypatch.setattr(Path, "open", observed)
    receipt = capture_runtime([root])
    assert 2 <= peak <= 4
    assert active == 0
    assert len(receipt["trees"][str(root)]) == 13


def test_runtime_mutation_during_file_read_is_rejected(tmp_path, monkeypatch):
    """MON-13: concurrent hashing must retain each entry's before/after drift check."""
    root = tmp_path.resolve() / "runtime"
    root.mkdir()
    target = root / "changed"
    target.write_bytes(b"before")
    original = Path.open

    @contextmanager
    def changed(path, *args, **kwargs):
        with original(path, *args, **kwargs) as stream:
            yield stream
        if path == target:
            with original(target, "wb") as stream:
                stream.write(b"changed after read")

    monkeypatch.setattr(Path, "open", changed)
    with pytest.raises(RuntimeDrift, match="changed during inventory"):
        capture_runtime([root])
