"""H1 table generator (design doc §8.2.4; Phase 1 DoD "H1 table produced").

Two-stage so a CLEAN CHECKOUT can regenerate the table (PR #33 review):
when gitignored runs/h1/ data is present, per-attempt rows (including the
mechanism read from each attempt's first_graph.yaml) are EXTRACTED into
committed analysis/h1/h1_attempts_<agent>.json bundles; rendering always
consumes the bundles. Without runs/ the committed bundles alone rebuild
h1_table.md byte-identically.
CON-8: JSON to stdout, logs to stderr, exit 0 iff every arm rendered.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ARMS = ("claude", "codex")

SUMMARY_ROWS = [
    ("attempts", "Attempts"),
    ("zero_shot_valid", "Zero-shot valid"),
    ("zero_shot_valid_and_launching", "Zero-shot valid AND launching"),
    ("zero_shot_rate", "Zero-shot rate (H1 target >=0.80)"),
    ("working_within_3_cycles", "Working (<=3 cycles, pass@1 > 0)"),
    ("mean_validate_calls", "Mean validate calls"),
    ("mean_final_pass1", "Mean final pass@1"),
    ("attempts_with_workspace_violations", "Workspace violations"),
    ("sessions_timed_out", "Sessions timed out"),
]


def mechanism(graph_path: Path) -> str:
    """Perception-stack choice: the discriminating composition decision."""
    doc = yaml.safe_load(graph_path.read_text())
    ids = {n["id"] for n in doc.get("nodes", [])}
    if "oracle-pose" in ids:
        return "oracle-pose"
    if "detector-openvocab" in ids or "pose-estimator" in ids:
        return "detector-stack"
    return "other"


def extract_arm(root: Path, agent: str) -> dict | None:
    """Bundle from raw runs/ data (gitignored); None when absent."""
    results = root / "runs" / "h1" / f"h1_results_{agent}.json"
    if not results.exists():
        return None
    data = json.loads(results.read_text())
    attempts = []
    for d in sorted((root / "runs" / "h1" / agent).glob("attempt_*")):
        rec = json.loads((d / "record.json").read_text())
        first = rec["first_graph"]
        attempts.append(
            {
                "attempt": rec["attempt"],
                "mechanism": mechanism(d / "first_graph.yaml"),
                "valid": first["valid"],
                "launch": first["launch_outcome"],
                "pass1": first["pass1"],
                "cycles": rec["validate_calls"],
                "wall_s": rec["session_wall_s"],
                "failures": first["failures"],
                "violations": rec["workspace_violations"],
            }
        )
    return {"treatment": data["treatment"], "summary": data["summary"], "attempts": attempts}


def bundle_path(root: Path, agent: str) -> Path:
    return root / "analysis" / "h1" / f"h1_attempts_{agent}.json"


def load_bundle(root: Path, agent: str) -> dict | None:
    """Fresh extraction when runs/ exists (also refreshes the committed
    bundle); otherwise the committed bundle."""
    bundle = extract_arm(root, agent)
    if bundle is not None:
        bundle_path(root, agent).write_text(json.dumps(bundle, indent=1))
        return bundle
    committed = bundle_path(root, agent)
    return json.loads(committed.read_text()) if committed.exists() else None


def render(arms: dict[str, dict]) -> str:
    out = ["# H1 composition experiment — results table (§8.2.4)", ""]
    for agent, arm in arms.items():
        t = arm["treatment"]
        out += [
            f"## Arm: {agent}",
            "",
            f"Treatment: commit `{t['commit'][:12]}`, model `{t.get('model', '?')}`.",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        out += [f"| {label} | {arm['summary'].get(key)} |" for key, label in SUMMARY_ROWS]
        out += [
            "",
            "| # | Perception choice | First-graph outcome | pass@1 | Cycles | Failures |",
            "|---|---|---|---|---|---|",
        ]
        for a in arm["attempts"]:
            fails = ", ".join(f"{k}:{v}" for k, v in a["failures"].items()) or "-"
            out.append(
                f"| {a['attempt']} | {a['mechanism']} | {a['launch']} "
                f"| {a['pass1']:.3f} | {a['cycles']} | {fails} |"
            )
        by_mech: dict[str, list] = {}
        for a in arm["attempts"]:
            by_mech.setdefault(a["mechanism"], []).append(a)
        out += ["", "Mechanism split:", ""]
        for mech, group in sorted(by_mech.items()):
            launched = sum(1 for a in group if a["launch"] == "launched")
            out.append(f"- `{mech}`: {len(group)} attempts, {launched} launched")
        out.append("")
    return "\n".join(out)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    arms = {agent: arm for agent in ARMS if (arm := load_bundle(root, agent)) is not None}
    missing = [a for a in ARMS if a not in arms]
    if not arms:
        print(json.dumps({"ok": False, "error": "no arm data (runs/ or committed bundles)"}))
        return 1
    table = root / "analysis" / "h1" / "h1_table.md"
    table.write_text(render(arms))
    print(
        json.dumps(
            {
                "ok": not missing,
                "table": str(table.relative_to(root)),
                "arms": {a: arms[a]["summary"]["zero_shot_rate"] for a in arms},
                "missing_arms": missing,
            }
        )
    )
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
