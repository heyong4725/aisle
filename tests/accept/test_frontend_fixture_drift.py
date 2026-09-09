"""MON-13: actual frontend hook execution must expose retained-input drift."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
pytestmark = pytest.mark.accept


@pytest.mark.parametrize("frontend", ["codex", "claude"])
def test_actual_hook_mutating_invocation_invalidates_probe(tmp_path, monkeypatch, frontend):
    """MON-13: successful denial cannot hide a changed fixture invocation."""
    if sys.platform != "darwin":
        pytest.skip("requires the macOS fixture sandbox")
    binary = os.environ.get(f"AISLE_{frontend.upper()}_PROBE_BINARY")
    if not binary:
        pytest.skip("requires an explicitly selected actual frontend binary")
    import frontend_claude_probe as claude
    import frontend_codex_probe as codex

    original = codex._hook

    def mutating_hook(output, mode):
        command = original(output, mode)
        with (output / "hook.py").open("a") as stream:
            stream.write(f"Path({str(output / 'invocation.json')!r}).write_text('{{}}')\n")
        return command

    monkeypatch.setattr(codex, "_hook", mutating_hook)
    report = (codex if frontend == "codex" else claude).run_probe(
        binary, tmp_path / frontend, "deny"
    )
    assert report["tool_blocked"] is True, report
    assert report["ok"] is False, report
    assert "fixture changed: invocation.json" in report["errors"]
    assert report["complete_coverage"] is False
    assert report["confinement_verified"] is False
