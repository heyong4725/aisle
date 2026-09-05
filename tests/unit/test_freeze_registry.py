"""Content-addressed campaign freeze registry (CSE-15, FEL-18, SFE-9, SEM-9,
BND-12, FLT-9, MON-10, HWP-14, RPR-9; CON-5, CON-8).

Every campaign spec in the 440-540 range asks for the same pre-registration
object: hypotheses, endpoints, margins, exclusions, task/fault set, seed
commitment, budgets, integrity gates, analysis code, and exact commands bound
to content hashes, refusing later drift, and never self-attesting a review a
human owns. These tests pin that contract on synthetic trees.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from cli_helpers import REPO_ROOT, run_module

from aisle.harness.freeze import FreezeError, build_manifest, check_manifest, hash_path

pytestmark = pytest.mark.unit


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "graphs").mkdir(parents=True)
    (root / "graphs" / "expert.yaml").write_text("nodes: []\n")
    (root / "tools").mkdir()
    (root / "tools" / "analyze.py").write_text("print('x')\n")
    (root / "private").mkdir()
    (root / "private" / "seeds.json").write_text("[101, 102, 103]\n")
    (root / "private" / "salt.bin").write_bytes(b"salt-bytes")
    (root / "gates").mkdir()
    (root / "gates" / "parity.json").write_text('{"ok": true}\n')
    return root


def _declaration() -> dict:
    return {
        "schema_version": "aisle.freeze.declaration.v1",
        "campaign_id": "demo-campaign-v1",
        "spec": "500",
        "issue": 347,
        "purpose": "confirmatory",
        "hypotheses": [
            {
                "id": "H-1",
                "statement": "typed beats monolithic",
                "direction": "typed_dataflow > monolithic",
            }
        ],
        "endpoints": {
            "primary": [
                {
                    "id": "E-1",
                    "outcome": "session_success",
                    "unit": "session",
                    "aggregation": "risk_difference",
                    "direction": "higher",
                    "status": "inferential",
                }
            ],
            "secondary": [],
        },
        "decision_rules": {
            "smallest_effect": {"measure": "risk_difference", "value": 0.2},
            "alpha": 0.05,
            "power": 0.8,
            "equivalence_margin": {"not_applicable": "superiority design"},
            "stopping_rule": "fixed n; no optional stopping",
        },
        "exclusions": {
            "infrastructure": ["host outage before launch"],
            "treatment_integrity": ["contamination sentinel tripped"],
            "rerun_policy": "new randomized id; original retained",
            "deviation_policy": "documented deviation record; new campaign version",
        },
        "instrument_set": {"kind": "task", "items": ["t1-l2-composition"]},
        "seed_commitment": {
            "seeds_source": "private/seeds.json",
            "salt_source": "private/salt.bin",
        },
        "budgets": {"tokens": 200000, "wall_s": 3600},
        "integrity_checks": [
            {
                "gate": "MON-parity",
                "kind": "machine_check",
                "status": "passed",
                "record": "gates/parity.json",
                "owner_role": "controller",
            },
            {
                "gate": "STA-12",
                "kind": "external_review",
                "status": "pending",
                "record": None,
                "owner_role": "independent statistician",
            },
        ],
        "artifacts": {"graph": "graphs/expert.yaml", "tools": "tools"},
        "analysis": {"scripts": ["tools/analyze.py"], "seed": 7},
        "commands": ["uv run harness stats analyze --protocol P --records R"],
    }


def test_manifest_binds_every_artifact_and_withholds_seed_values(tmp_path):
    """CSE-15 / BND-12 / SFE-9 / SEM-9: every declared artifact, script, and
    passed-gate record is content-addressed; BND-13-style seed values never
    appear, only their salted commitment."""
    root = _tree(tmp_path)
    manifest = build_manifest(root, _declaration(), git_head="abc123")
    assert set(manifest["artifact_hashes"]) == {"graph", "tools"}
    assert manifest["artifact_hashes"]["graph"] == hash_path(root, "graphs/expert.yaml")
    assert manifest["analysis_script_hashes"] == {
        "tools/analyze.py": hash_path(root, "tools/analyze.py")
    }
    assert manifest["gate_record_hashes"] == {"MON-parity": hash_path(root, "gates/parity.json")}
    assert manifest["seed_commitment"].startswith("sha256:")
    assert "101" not in json.dumps(manifest)


def test_pending_external_review_can_never_be_frozen(tmp_path):
    """CSE-15 / FEL-18 / MON-10: a pending human gate (STA-12, CON-14) keeps
    the manifest at registered_pending_review even with a timestamp; only
    all-passed gates plus an explicit timestamp freeze it."""
    root = _tree(tmp_path)
    pending = build_manifest(
        root,
        _declaration(),
        git_head=None,
        timestamp="2026-09-05T00:00:00+00:00",
        timestamp_source="git",
    )
    assert pending["frozen"] is False
    assert pending["status"] == "registered_pending_review"
    assert pending["pending_gates"] == ["STA-12"]

    declaration = _declaration()
    (root / "gates" / "sta12.json").write_text('{"signed": true}\n')
    declaration["integrity_checks"][1].update({"status": "passed", "record": "gates/sta12.json"})
    frozen = build_manifest(
        root,
        declaration,
        git_head=None,
        timestamp="2026-09-05T00:00:00+00:00",
        timestamp_source="git",
    )
    assert frozen["frozen"] is True and frozen["pending_gates"] == []
    untimed = build_manifest(root, declaration, git_head=None)
    assert untimed["frozen"] is False


def test_passed_gate_without_record_and_missing_artifact_refuse(tmp_path):
    """CSE-10-shaped honesty: a gate cannot be marked passed without a retained
    record, and a missing artifact is a refusal, never an empty hash."""
    root = _tree(tmp_path)
    declaration = _declaration()
    declaration["integrity_checks"][0]["record"] = None
    with pytest.raises(FreezeError, match="declaration is invalid") as refused:
        build_manifest(root, declaration, git_head=None)
    assert any("must name its retained record" in d for d in refused.value.details)

    declaration = _declaration()
    declaration["artifacts"]["missing"] = "graphs/nope.yaml"
    with pytest.raises(FreezeError, match="artifact is missing"):
        build_manifest(root, declaration, git_head=None)


def test_check_detects_byte_drift_in_any_bound_input(tmp_path):
    """CSE-15 / BND-12 / FLT-9: after a freeze, any byte change to an artifact,
    analysis script, gate record, seed set, or the declaration itself is
    reported as drift with ok:false; an untouched tree checks clean."""
    root = _tree(tmp_path)
    manifest = build_manifest(root, _declaration(), git_head=None)
    assert check_manifest(root, manifest)["ok"] is True

    (root / "graphs" / "expert.yaml").write_text("nodes: [changed]\n")
    report = check_manifest(root, manifest)
    assert report["ok"] is False
    assert report["drift"] and report["drift"][0].startswith("artifact_hashes.graph")

    (root / "graphs" / "expert.yaml").write_text("nodes: []\n")
    (root / "private" / "seeds.json").write_text("[101, 102, 104]\n")
    assert any(d.startswith("seed_commitment") for d in check_manifest(root, manifest)["drift"])

    (root / "private" / "seeds.json").write_text("[101, 102, 103]\n")
    tampered = copy.deepcopy(manifest)
    tampered["declaration"]["budgets"]["tokens"] = 999999
    assert any(d.startswith("declaration_sha256") for d in check_manifest(root, tampered)["drift"])


def test_build_is_deterministic_and_reads_no_clock(tmp_path):
    """CON-5: identical inputs give byte-identical manifests; the only time
    field is the caller-supplied timestamp."""
    root = _tree(tmp_path)
    a = build_manifest(root, _declaration(), git_head="h")
    b = build_manifest(root, _declaration(), git_head="h")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["timestamp"] is None and a["timestamp_source"] is None
    with pytest.raises(FreezeError, match="timestamp requires its source"):
        build_manifest(root, _declaration(), git_head="h", timestamp="2026-09-05T00:00:00+00:00")


def test_cli_build_and_check_follow_con8(tmp_path):
    """CON-8 / RPR-9 / HWP-14: JSON on stdout, exit 0 iff ok; build writes the
    manifest, check reports drift with exit 1, output colliding with the
    declaration is refused."""
    root = _tree(tmp_path)
    declaration = root / "declaration.json"
    declaration.write_text(json.dumps(_declaration()))
    manifest = root / "freeze-manifest.json"
    proc = run_module(
        "aisle.harness.cli",
        "freeze",
        "build",
        "--declaration",
        str(declaration),
        "--output",
        str(manifest),
        "--root",
        str(root),
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"] is True and out["status"] == "registered_pending_review"
    assert json.loads(manifest.read_text())["campaign_id"] == "demo-campaign-v1"

    proc = run_module(
        "aisle.harness.cli", "freeze", "check", "--manifest", str(manifest), "--root", str(root)
    )
    assert proc.returncode == 0 and json.loads(proc.stdout)["ok"] is True

    (root / "tools" / "analyze.py").write_text("print('changed')\n")
    proc = run_module(
        "aisle.harness.cli", "freeze", "check", "--manifest", str(manifest), "--root", str(root)
    )
    assert proc.returncode == 1
    drift = json.loads(proc.stdout)["drift"]
    assert any(d.startswith("analysis_script_hashes.tools/analyze.py") for d in drift)

    proc = run_module(
        "aisle.harness.cli",
        "freeze",
        "build",
        "--declaration",
        str(declaration),
        "--output",
        str(declaration),
        "--root",
        str(root),
    )
    assert proc.returncode == 1 and "collides" in json.loads(proc.stdout)["error"]


def test_withheld_seed_sources_are_unverified_not_invented(tmp_path):
    """BND-13 / CSE-9: held-out seed sources live outside the worktree. A
    checker without them refuses by default; with the explicit allowance it
    reports the commitment as unverified and still checks every other hash."""
    root = _tree(tmp_path)
    manifest = build_manifest(root, _declaration(), git_head=None)
    (root / "private" / "seeds.json").unlink()
    with pytest.raises(FreezeError, match="seed commitment sources are missing"):
        check_manifest(root, manifest)
    report = check_manifest(root, manifest, require_seed_sources=False)
    assert report["ok"] is True and report["seed_commitment"] == "unverified"
    (root / "gates" / "parity.json").write_text('{"ok": false}\n')
    report = check_manifest(root, manifest, require_seed_sources=False)
    assert report["ok"] is False and report["drift"][0].startswith("gate_record_hashes.MON-parity")


def _committed_manifests() -> list[Path]:
    return sorted((REPO_ROOT / "analysis" / "freeze").glob("*/freeze-manifest.json"))


def test_committed_registrations_check_clean_with_withheld_seeds():
    """CSE-15 / FEL-18 / SFE-9 / SEM-9 / BND-12 / FLT-9: every committed
    campaign registration still binds the bytes it hashed, and none of them
    claims a freeze the spec hands to a human."""
    manifests = _committed_manifests()
    assert len(manifests) == 6
    for path in manifests:
        manifest = json.loads(path.read_text())
        report = check_manifest(REPO_ROOT, manifest, require_seed_sources=False)
        assert report["ok"] is True, (path.name, report["drift"])
        assert manifest["frozen"] is False
        assert manifest["status"] == "registered_pending_review"
        assert manifest["pending_gates"], path
        declaration = json.loads(path.with_name("declaration.json").read_text())
        assert declaration == manifest["declaration"], path
        assert not any(
            "/Users/" in v for v in declaration["seed_commitment"].values() if isinstance(v, str)
        )


def test_confirmatory_registrations_carry_a_refused_freeze():
    """STA-1 / STA-12 / CSE-8: the two confirmatory registrations hold a SPEC
    400 protocol whose power output is labeled an assumption and whose
    freeze validation is a retained refusal, not a pass."""
    for cid in ("cse-causal-study-v1", "fel-fault-evidence-study-v1"):
        folder = REPO_ROOT / "analysis" / "freeze" / cid
        power = json.loads((folder / "power.json").read_text())
        assert power["ok"] is True
        assert "ASSUMPTION" in power["assumptions"]["assumption_note"]
        refusal = json.loads((folder / "protocol-freeze-refusal.json").read_text())
        assert refusal["ok"] is False
        assert "independent statistical review" in refusal["errors"][0]
