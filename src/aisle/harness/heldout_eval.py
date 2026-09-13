"""Controller-run held-out evaluation of a finished session's final deliverable.

CSE-2: session success is whether the retained final deliverable launches
through its arm's launcher and passes the hidden held-out acceptance; CSE-9:
seed values and verdicts stay evaluator-private (only the registered salted
commitment is recorded, and the launch runs in a separate process); BND-3:
the oracle is the held-out scorer while the loop runs on the realistic
verifier. The evaluation runs only after the agent process has finished,
never inside a participant view, and never in the controller's own process.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

from aisle.harness.candidate_launch import arm_arguments
from aisle.harness.candidates import launch_fields_for

HELDOUT_SCHEMA = "aisle.heldout-evidence.v1"
RULE_KIND = "oracle_successes_at_least"
HELDOUT_DIR = "heldout"  # the one place the evidence lives: <session_dir>/heldout
SOURCE_MAP = "analysis/safety-exposure/source-map.json"


class HeldoutError(ValueError):
    """The held-out evaluation cannot be established on the retained evidence."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise HeldoutError(f"unreadable evidence: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HeldoutError(f"evidence is not an object: {path}")
    return value


def verify_ready(session_dir: Path) -> dict:
    """An intact, completed engineering session with its final snapshot retained."""
    session_dir = Path(session_dir).absolute()
    record = _read_json(session_dir / "matched-session.json")
    process = record.get("process")
    if (
        record.get("ok") is not True
        or record.get("error") is not None
        or record.get("classification") != "engineering_execution"
        or not isinstance(process, dict)
        or process.get("rc") != 0
        or not isinstance(record.get("lifecycle"), dict)
        or record["lifecycle"].get("finished_at") is None
    ):
        raise HeldoutError("session is not an intact completed engineering session")
    final = (record.get("snapshots") or {}).get("final") or {}
    if not isinstance(final, dict) or not final:
        raise HeldoutError("session retained no final snapshot")
    for name, row in final.items():
        path = session_dir / "final" / name
        if path.is_symlink() or not path.is_file() or _sha256(path) != row["sha256"]:
            raise HeldoutError(f"final snapshot drifted: {name}")
    return record


def load_private_seeds(seeds_source: Path, salt_source: Path, manifest: dict) -> list[int]:
    """Seeds whose salted digest equals the REGISTERED commitment; never logged."""
    from aisle.harness.freeze import canonical_bytes

    commitment = manifest.get("seed_commitment")
    if type(commitment) is not str or not commitment.startswith("sha256:"):
        raise HeldoutError("registration carries no salted seed commitment")
    try:
        seeds = json.loads(Path(seeds_source).expanduser().read_bytes())
        salt = Path(salt_source).expanduser().read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise HeldoutError("private seed sources are unreadable") from exc
    if (
        type(seeds) is not list
        or not seeds
        or any(type(s) is not int for s in seeds)
        or len(set(seeds)) != len(seeds)
    ):
        raise HeldoutError("private seed list is malformed")
    if "sha256:" + hashlib.sha256(salt + canonical_bytes(seeds)).hexdigest() != commitment:
        raise HeldoutError("private seeds do not match the registered commitment")
    return seeds


def build_view(controller_root: Path, session_dir: Path, snapshot: dict, destination: Path) -> dict:
    """The controller's committed tree (git HEAD, never the working tree) with the
    final deliverable overlaid: the exact system the agent submitted, outside every
    participant view. Returns the bound identities."""
    controller_root, destination = Path(controller_root).absolute(), Path(destination).absolute()
    if destination.exists():
        raise HeldoutError("held-out view destination exists; evaluation is never resumed")
    try:
        commit = subprocess.run(
            ["git", "-C", str(controller_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        archive = subprocess.run(
            ["git", "-C", str(controller_root), "archive", "--format=tar", commit],
            check=True,
            capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HeldoutError(f"controller tree is not an archivable git commit: {exc}") from exc
    destination.mkdir(parents=True)
    import io

    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination, filter="data")
    overlaid = {}
    for name, row in snapshot.items():
        source = Path(session_dir) / "final" / name
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        if _sha256(target) != row["sha256"]:
            raise HeldoutError(f"held-out view drifted while copying: {name}")
        overlaid[name] = row["sha256"]
    return {"controller_commit": commit, "overlaid": overlaid, "view": str(destination)}


def oracle_verdicts(run_dir: Path) -> dict[str, dict]:
    """Held-out oracle verdicts by goal id from every trace directory of the run
    (relaunches write under traces/relaunch-N); a run without any oracle row is
    an empty mapping, and a duplicate goal id refuses (BND-3)."""
    import pyarrow as pa

    verdicts: dict[str, dict] = {}
    for path in sorted(Path(run_dir).glob("traces/**/verifier-oracle__episode_result.arrow")):
        try:
            with pa.ipc.open_stream(path) as reader:
                batches = []
                try:
                    for batch in reader:
                        batches.append(batch)
                except (pa.ArrowInvalid, OSError):
                    pass
        except (pa.ArrowInvalid, OSError) as exc:
            raise HeldoutError(f"oracle trace unreadable: {path}: {exc}") from exc
        for batch in batches:
            for row in batch.to_pylist():
                payload = json.loads(row["text"])
                goal = payload.get("goal_id")
                if goal in verdicts:
                    raise HeldoutError(f"duplicate oracle verdict for goal {goal}")
                verdicts[goal] = {
                    "status": payload.get("status"),
                    "failure": payload.get("failure"),
                }
    return verdicts


def exposure_counts(run_dir: Path, controller_root: Path, campaign_id: str) -> dict:
    """STA-10 exposure of the held-out run from the SPEC 470 ledger: commands are the
    guard-gated motion proposals, safety events the wrong-object deliveries and
    collision proxies. Fails closed to an explicit not-derived record."""
    from aisle.harness.exposure import ledger_for_run

    try:
        source_map = json.loads((Path(controller_root) / SOURCE_MAP).read_bytes())
        ledger = ledger_for_run(Path(run_dir), campaign_id=campaign_id, source_map=source_map)
    except Exception as exc:  # the ledger is instrument code; any failure is a refusal
        return {"commands": None, "safety_events": None, "unit": "command", "error": repr(exc)}
    episodes = ledger.get("episodes") or []
    return {
        "commands": len(ledger.get("proposals") or []),
        "safety_events": sum(
            len(row.get("wrong_object_events") or []) + len(row.get("collisions") or [])
            for row in episodes
        ),
        "unit": "command",
        "error": None,
    }


def launch_arm(
    arm: str, fields: dict, view: Path, seeds, run_id: str, timeout_s, development
) -> dict:
    """Run the submitted system through its own launcher in a separate process
    rooted at the rebuilt view (the module never executes where seeds were read)."""
    args = arm_arguments(
        arm,
        "run",
        fields,
        view,
        {"seeds": list(seeds), "timeout_s": timeout_s},
        root=view,
        run_id=run_id,
    )
    args[args.index("--verifier") + 1] = "realistic"
    command = [
        sys.executable,
        "-B",
        "-m",
        "aisle.harness.cli",
        *args,
        "--no-idea-gate",
        "--env-baseline",
        "local",
    ]
    try:
        completed = subprocess.run(
            command, cwd=view, capture_output=True, timeout=float(timeout_s) * len(seeds) + 600.0
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "infrastructure_invalid": True,
            "error": f"launcher did not finish: {exc}",
        }
    try:
        report = json.loads(completed.stdout.decode().splitlines()[-1])
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError):
        report = {"ok": False, "infrastructure_invalid": True, "error": "launcher emitted no JSON"}
    report.setdefault("rc", completed.returncode)
    return report


def run_heldout(request: dict, *, launcher=None, verdicts=None, exposure=None) -> dict:
    """Evaluate one finished session; write <session_dir>/heldout/heldout-evidence.json.

    `ok` states whether the evaluation itself was established (launch attempted
    through the arm's launcher with the registered seeds); `session_success` is
    the CSE-2 outcome. An infrastructure refusal leaves `ok: false` so the
    session is excluded rather than scored."""
    launcher = launch_arm if launcher is None else launcher
    verdicts = oracle_verdicts if verdicts is None else verdicts
    exposure = exposure_counts if exposure is None else exposure
    try:
        output = Path(request["session_dir"]).absolute() / HELDOUT_DIR
        return _run_heldout(request, output, launcher, verdicts, exposure)
    except (KeyError, TypeError, AttributeError, ZeroDivisionError) as exc:
        raise HeldoutError(f"held-out request is unresolved: {exc!r}") from exc


def _run_heldout(request: dict, output: Path, launcher, verdicts, exposure) -> dict:
    if output.exists():
        raise HeldoutError("held-out output exists; evaluation is never resumed")
    session_dir = Path(request["session_dir"]).absolute()
    record = verify_ready(session_dir)
    development = _read_json(session_dir / "admission.json").get("development")
    if development is None:
        raise HeldoutError("session was admitted without a development protocol")
    fields = launch_fields_for(development)
    manifest = _read_json(Path(request["manifest"]))
    seeds = load_private_seeds(request["seeds_source"], request["salt_source"], manifest)
    if set(seeds) & set(development["seeds"]):
        raise HeldoutError("private seeds overlap the session's development seeds")
    rule = request["rule"]
    if (
        not isinstance(rule, dict)
        or rule.get("kind") != RULE_KIND
        or type(rule.get("threshold")) is not int
        or not 1 <= rule["threshold"] <= len(seeds)
    ):
        raise HeldoutError("held-out success rule is unresolved")
    output.mkdir(parents=True)
    view = build_view(
        request["controller_root"], session_dir, record["snapshots"]["final"], output / "view"
    )
    run_id = f"heldout-{record['session_id']}"
    report = launcher(
        record["arm"], fields, Path(view["view"]), seeds, run_id, request["timeout_s"], development
    )
    launched = report.get("ok") is True
    refused = not launched and (report.get("infrastructure_invalid") or "refused" in report)
    run_dir = Path(view["view"]) / "runs" / run_id
    rows = verdicts(run_dir) if launched else {}
    counts = (
        exposure(run_dir, request["controller_root"], manifest.get("campaign_id"))
        if launched
        else {"commands": None, "safety_events": None, "unit": "command", "error": "not launched"}
    )
    episodes = [f"ep-{index:04d}" for index in range(len(seeds))]
    per_seed = [
        {
            "index": index,
            "goal_id": goal,
            "oracle_status": rows.get(goal, {}).get("status", "not_run"),
            "failure": rows.get(goal, {}).get("failure"),
        }
        for index, goal in enumerate(episodes)
    ]
    successes = sum(1 for row in per_seed if row["oracle_status"] == "success")
    evidence = {
        "schema_version": HELDOUT_SCHEMA,
        "ok": not refused,
        "session_id": record["session_id"],
        "arm": record["arm"],
        "candidate": development.get("candidate", "t1-oracle"),
        "plan_id": record["plan_id"],
        "controller_commit": view["controller_commit"],
        "final_snapshot_sha256": view["overlaid"],
        "campaign_id": manifest.get("campaign_id"),
        "seed_commitment": manifest.get("seed_commitment"),
        "n_seeds": len(seeds),
        "launch": {
            "ok": launched,
            "refused": bool(refused),
            "error": report.get("error"),
            "refusal": report.get("refused"),
            "infrastructure_invalid": bool(report.get("infrastructure_invalid")),
            "rc": report.get("rc"),
        },
        "run_id": run_id,
        "per_seed": per_seed,
        "oracle_successes": successes,
        "exposure": counts,
        "rule": rule,
        "session_success": launched and successes >= rule["threshold"],
        "loop_verifier": "realistic",
        "scorer": "oracle (held-out tap, BND-3)",
    }
    (output / "heldout-evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    )
    return evidence
