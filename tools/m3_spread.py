"""m3_spread — the M3 v2 spread-population measurement (ADR-m3
amendment 1). Generates the frozen 16-graph population, collects FRESH
Genesis ground truth and surrogate outcomes under identical staging,
and recomputes the pre-registered analyses (overall Spearman; the
contact-geometry exclusion; the per-family fidelity table). CON-8;
UNATTESTED free-run dev measurement."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from m3_ranking import POP_DIR, screening_agreement, spearman  # noqa: E402

SEEDS = "0,1,2,3,4,5,6,7"
OUT_DEFAULT = REPO_ROOT / "runs" / "m3-spread"
CONTACT_GEOMETRY = {"fault_f1", "fault_f2"}  # the pre-registered exclusion set


def _load_expert() -> dict:
    import yaml

    return yaml.safe_load((REPO_ROOT / "graphs" / "expert_t1.yaml").read_text())


def build_population(pop_dir: Path) -> list[dict]:
    import copy

    import yaml

    pop_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    def emit(name: str, doc: dict, family: str) -> None:
        text = yaml.safe_dump(doc, sort_keys=False)
        (pop_dir / f"{name}.yaml").write_text(text)
        manifest.append(
            {
                "id": name,
                "family": family,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )

    base = _load_expert()
    emit("clean", base, "clean")
    for vel in (0.3, 0.5, 0.7, 1.5, 2.0):
        doc = copy.deepcopy(base)
        node = next(n for n in doc["nodes"] if n["id"] == "ik-trajectory")
        node.setdefault("env", {})["AISLE_MAX_JOINT_VEL"] = str(vel)
        emit(f"vel_{vel}", doc, "timing")
    for fname, node_id, value in (
        ("fault_f1", "segmented-pose", "pose_bias"),
        ("fault_f2", "grasp-planner-topdown", "grasp_high"),
        ("fault_f3", "ik-trajectory", "traj_short"),
    ):
        doc = copy.deepcopy(base)
        node = next(n for n in doc["nodes"] if n["id"] == node_id)
        node.setdefault("env", {})["AISLE_H6_FAULT"] = value
        family = "timing" if fname == "fault_f3" else "contact-geometry"
        emit(fname, doc, family)
    doc = copy.deepcopy(base)
    node = next(n for n in doc["nodes"] if n["id"] == "grasp-planner-topdown")
    node["inputs"].pop("neighbours", None)
    emit("nof1_neighbours", doc, "wiring")
    for h1 in (
        "claude_00",
        "claude_10",
        "claude_15",
        "codex_02",
        "codex_06",
        "codex_13",
    ):
        text = (POP_DIR / f"{h1}.yaml").read_text()
        (pop_dir / f"h1_{h1}.yaml").write_text(text)
        manifest.append(
            {
                "id": f"h1_{h1}",
                "family": "authored",
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    (pop_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def stage(entry_id: str, pop_dir: Path, work: Path, surrogate: bool) -> Path:
    import yaml

    doc = yaml.safe_load((pop_dir / f"{entry_id}.yaml").read_text())
    if surrogate:
        # ADR-m3 amendment staging note: the v0 surrogate is free-run;
        # lockstep is opt-in by env, so strip the barrier, its edges,
        # and the turn envs — pipeline nodes then run free exactly as
        # pre-ADR-30 graphs do
        doc["nodes"] = [n for n in doc["nodes"] if n["id"] != "turn-barrier"]
        for node in doc["nodes"]:
            inputs = node.get("inputs") or {}
            node["inputs"] = {
                k: v
                for k, v in inputs.items()
                if not str(v.get("source", v) if isinstance(v, dict) else v).startswith(
                    "turn-barrier/"
                )
            }
            env = node.get("env") or {}
            for key in list(env):
                if key.startswith("AISLE_TURN") or key == "AISLE_LOCKSTEP":
                    env.pop(key)
            outputs = node.get("outputs")
            if isinstance(outputs, list):
                node["outputs"] = [o for o in outputs if o not in ("sim_turn", "turn_done")]
    for node in doc["nodes"]:
        raw = str(node.get("path", ""))
        if surrogate and raw.endswith("dora_genesis.py"):
            node["path"] = str(REPO_ROOT / "src" / "aisle" / "nodes" / "world_model_env.py")
        elif raw.startswith("../"):
            node["path"] = str((REPO_ROOT / raw.removeprefix("../")).resolve())
        if node["id"] == "rollout-client":
            node.setdefault("env", {}).update(
                {
                    "AISLE_SEEDS": SEEDS,
                    "AISLE_TIER": "T1",
                    "AISLE_RESULTS": str(work / "episodes.jsonl"),
                }
            )
    graph = work / "graph.yaml"
    graph.write_text(yaml.safe_dump(doc, sort_keys=False))
    return graph


def run_graph(
    entry_id: str, pop_dir: Path, out_root: Path, surrogate: bool, timeout_s: float
) -> dict:
    import os
    import signal

    kind = "surrogate" if surrogate else "genesis"
    work = out_root / kind / entry_id
    work.mkdir(parents=True, exist_ok=True)
    graph = stage(entry_id, pop_dir, work, surrogate)
    results = work / "episodes.jsonl"
    proc = subprocess.Popen(
        ["dora", "run", str(graph), "--uv"],
        cwd=REPO_ROOT,
        stdout=open(work / "dora.stdout.log", "w"),
        stderr=open(work / "dora.stderr.log", "w"),
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout_s
    rows: list[dict] = []
    while time.monotonic() < deadline:
        if results.exists():
            rows = [json.loads(x) for x in results.read_text().splitlines() if x.strip()]
            if len(rows) >= 8:
                break
        if proc.poll() is not None:
            break
        time.sleep(3)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=20)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    rows = (
        [json.loads(x) for x in results.read_text().splitlines() if x.strip()]
        if results.exists()
        else rows
    )
    out = {"id": entry_id, "kind": kind, "episodes": len(rows)}
    if rows:
        out["pass1"] = round(sum(1 for r in rows if r.get("status") == "success") / len(rows), 3)
        failures: dict[str, int] = {}
        for r in rows:
            if r.get("failure"):
                failures[r["failure"]] = failures.get(r["failure"], 0) + 1
        out["failures"] = failures
    else:
        out["pass1"] = None
        out["infra"] = "no episodes"
    return out


def analyze(records: dict, manifest: list[dict]) -> dict:
    families = {m["id"]: m["family"] for m in manifest}
    # join BY ID, never by position: a partial re-run appends out of
    # manifest order, and a positional zip silently pairs wrong rows
    # (measured: the first v2 analysis printed h1 rows at 0.0 while the
    # records held 0.875)
    surrogate_by_id = {s["id"]: s for s in records["surrogate"]}
    paired = [
        (g["id"], g["pass1"], surrogate_by_id[g["id"]]["pass1"], families.get(g["id"], "?"))
        for g in records["genesis"]
        if g["pass1"] is not None
        and g["id"] in surrogate_by_id
        and surrogate_by_id[g["id"]]["pass1"] is not None
    ]
    gvals = [p[1] for p in paired]
    svals = [p[2] for p in paired]
    excl = [p for p in paired if p[0] not in CONTACT_GEOMETRY]
    return {
        "n": len(paired),
        "spearman_all": spearman(gvals, svals),
        "spearman_excl_contact_geometry": spearman([p[1] for p in excl], [p[2] for p in excl]),
        "screening_agreement_all": screening_agreement(gvals, svals),
        "table": [
            {"id": i, "family": fam, "genesis": g, "surrogate": s} for i, g, s, fam in paired
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--run", choices=["genesis", "surrogate", "both"])
    parser.add_argument("--only", default=None)
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    pop_dir = args.out / "population"

    if args.build:
        manifest = build_population(pop_dir)
        print(json.dumps({"ok": True, "population": len(manifest)}))
        return 0
    manifest = json.loads((pop_dir / "manifest.json").read_text())
    if args.only:
        manifest = [m for m in manifest if m["id"] == args.only]
    records_path = args.out / "records.json"
    records = (
        json.loads(records_path.read_text())
        if records_path.exists()
        else {"genesis": [], "surrogate": []}
    )
    if args.run:
        kinds = ["genesis", "surrogate"] if args.run == "both" else [args.run]
        for kind in kinds:
            surrogate = kind == "surrogate"
            timeout_s = 240.0 if surrogate else 1500.0
            done = {r["id"] for r in records[kind]}
            for m in manifest:
                if m["id"] in done:
                    continue
                print(f"[m3-spread] {kind} {m['id']}", file=sys.stderr)
                records[kind].append(run_graph(m["id"], pop_dir, args.out, surrogate, timeout_s))
                records_path.write_text(json.dumps(records, indent=1) + "\n")
        print(json.dumps({"ok": True}))
        return 0
    if args.analyze:
        full_manifest = json.loads((pop_dir / "manifest.json").read_text())
        print(json.dumps(analyze(records, full_manifest), indent=1))
        return 0
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
