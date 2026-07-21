"""SPEC 070 idea tree and rollout gates (HAR-2, HAR-7, HAR-8) — no dora,
no sim (CON-12)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aisle.harness.ideas import close_idea, log_idea, open_ideas
from aisle.harness.rollout import run_gates

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_log_appends_jsonl_with_monotonic_ids(tmp_path):
    """HAR-7: `report log` appends JSONL entries with branch-monotonic ids,
    injected timestamp and git sha (CON-5)."""
    first = log_idea(tmp_path, "feat/x", "try wider grasp", "t0", "sha0", expect="+10pp on T1")
    second = log_idea(tmp_path, "feat/x", "raise gains", "t1", "sha1", parent=first["id"])
    assert (first["id"], second["id"]) == ("I1", "I2")
    lines = (tmp_path / "runs" / "ideas" / "feat__x.jsonl").read_text().splitlines()
    assert [json.loads(line)["id"] for line in lines] == ["I1", "I2"]
    assert json.loads(lines[0])["expect"] == "+10pp on T1"
    assert json.loads(lines[1])["parent"] == "I1"


def test_open_iff_logged_and_not_closed(tmp_path):
    """HAR-8: an idea is OPEN if logged and not closed; closing removes it
    from the open set without rewriting history (append-only)."""
    log_idea(tmp_path, "b", "idea one", "t0", "sha")
    log_idea(tmp_path, "b", "idea two", "t1", "sha")
    assert [e["id"] for e in open_ideas(tmp_path, "b")] == ["I1", "I2"]
    close_idea(tmp_path, "b", "I1", "observed flat", "flat", "t2")
    assert [e["id"] for e in open_ideas(tmp_path, "b")] == ["I2"]
    lines = (tmp_path / "runs" / "ideas" / "b.jsonl").read_text().splitlines()
    assert len(lines) == 3  # append-only: log, log, close


def test_close_requires_an_open_idea_and_valid_verdict(tmp_path):
    log_idea(tmp_path, "b", "idea", "t0", "sha")
    with pytest.raises(ValueError, match="no open idea"):
        close_idea(tmp_path, "b", "I9", "x", "up", "t1")
    with pytest.raises(ValueError, match="verdict"):
        close_idea(tmp_path, "b", "I1", "x", "sideways", "t1")
    close_idea(tmp_path, "b", "I1", "went flat", "flat", "t1")
    # PR #11 review: a second, contradictory close must be refused
    with pytest.raises(ValueError, match="no open idea"):
        close_idea(tmp_path, "b", "I1", "actually up", "up", "t2")


def _fake_root(tmp_path: Path, hash_ok: bool = True) -> Path:
    """A minimal root that passes/fails the env-hash gate deterministically;
    the REAL registry rides along (symlink) so the validation gate can pass
    and the idea gate is what decides."""
    (tmp_path / "registry").symlink_to(REPO_ROOT / "registry")
    (tmp_path / "tools").mkdir(parents=True)
    (tmp_path / "tools" / "env_hash.py").write_text(
        'import json, sys\nprint(json.dumps({"ok": '
        + ("True" if hash_ok else "False")
        + ', "env_hash": "h"}))\nsys.exit(0 if '
        + ("True" if hash_ok else "False")
        + " else 1)\n"
    )
    return tmp_path


def test_gate_refuses_on_env_hash_mismatch(tmp_path):
    """HAR-2: rollout MUST refuse when tools/env_hash.py --check fails
    (CON-7 frozen-set drift)."""
    root = _fake_root(tmp_path, hash_ok=False)
    result = run_gates(root, REPO_ROOT / "graphs" / "expert_t0.yaml", "b", no_idea_gate=True)
    assert result["ok"] is False and result["gate"] == "env_hash"


def test_gate_refuses_without_open_idea_and_bypass_is_recorded(tmp_path):
    """HAR-2/HAR-8: no OPEN idea on the branch refuses the launch; the
    humans-only --no-idea-gate bypass is surfaced so the manifest logs it."""
    from aisle.harness.validate import validate

    root = _fake_root(tmp_path, hash_ok=True)
    graph = REPO_ROOT / "graphs" / "expert_t0.yaml"
    if not validate(graph, REPO_ROOT, "franka", allow_unproven=False)["ok"]:
        pytest.skip("expert graph does not validate in this environment")
    refused = run_gates(root, graph, "b", no_idea_gate=False)
    assert refused["ok"] is False and refused["gate"] == "idea"
    log_idea(root, "b", "the campaign idea", "t0", "sha")
    passed = run_gates(root, graph, "b", no_idea_gate=False)
    assert passed["ok"] is True
    assert passed["idea"] == "I1" and passed["no_idea_gate"] is False
    bypass = run_gates(root, graph, "b", no_idea_gate=True)
    assert bypass["ok"] is True and bypass["no_idea_gate"] is True


def test_report_cli_json_contract(tmp_path):
    """CON-8 + HAR-7: `harness report log/close` emit a single JSON object
    on stdout and exit 0 iff ok."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.cli",
            "report",
            "log",
            "--idea",
            "test idea",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    entry = json.loads(proc.stdout)
    assert entry["ok"] is True and entry["id"] == "I1" and entry["status"] == "open"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "aisle.harness.cli",
            "report",
            "close",
            "--id",
            "I9",
            "--observed",
            "x",
            "--verdict",
            "up",
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["ok"] is False
