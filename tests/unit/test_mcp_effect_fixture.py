"""MON-12/MON-13: bound MCP probe writes only its configured editable target."""

import importlib.util
import io
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "aisle_mcp_effect_fixture",
    Path(__file__).resolve().parents[2] / "tools/frontend_mcp_fixture.py",
)
frontend_mcp_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frontend_mcp_fixture)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("fault", [None, "target", "marker", "symlink"])
def test_mcp_append_fixture_executes_bound_effect(tmp_path, monkeypatch, fault):
    """MON-13: fixture arguments cannot redirect the target or replace the expected marker."""
    root = tmp_path / "view"
    root.mkdir()
    target = root / "task.yaml"
    target.write_bytes(b"nodes: []\n")
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside\n")
    if fault == "symlink":
        target.unlink()
        target.symlink_to(outside)
    marker = "# AISLE probe mcp"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "append",
                "arguments": {
                    "target": "other.yaml" if fault == "target" else "task.yaml",
                    "marker": "different" if fault == "marker" else marker,
                },
            },
        },
    ]
    monkeypatch.setattr(
        frontend_mcp_fixture.sys,
        "stdin",
        io.TextIOWrapper(
            io.BytesIO(b"".join(json.dumps(row).encode() + b"\n" for row in requests))
        ),
    )
    stdout = io.StringIO()
    monkeypatch.setattr(frontend_mcp_fixture.sys, "stdout", stdout)
    kwargs = dict(probe_root=root, probe_target="task.yaml", probe_marker=marker)
    if fault == "symlink":
        with pytest.raises((ValueError, OSError)):
            frontend_mcp_fixture.serve(tmp_path, **kwargs)
    else:
        frontend_mcp_fixture.serve(tmp_path, **kwargs)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert "append" in {row["name"] for row in replies[0]["result"]["tools"]}
        assert ("result" in replies[1]) is (fault is None)
        assert target.read_bytes() == b"nodes: []\n" + (
            marker.encode() + b"\n" if fault is None else b""
        )
    assert outside.read_bytes() == b"outside\n"
